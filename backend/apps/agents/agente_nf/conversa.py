"""
O fluxo de conversa da NFS-e — coleta, confirmação, 2FA, emissão e consulta.

**Por que saiu do orquestrador.** Até o Sprint 2 tudo isto vivia como métodos de
`Orquestrador`, que assim acumulava dois papéis: decidir *qual* assunto é a
mensagem e conduzir *um* dos assuntos até o fim. Com as intenções virando
ferramentas registradas (DEC-05), a segunda metade precisava de casa própria —
senão cada tool nova voltaria a engordar a mesma classe, e a regra "o registry é
o único caminho até o modelo" conviveria com um objeto que chama o modelo por
fora.

O que ficou no orquestrador: sessão, continuações em aberto, escada de modelo e
despacho. O que veio para cá: tudo que é específico de nota fiscal.

**Funções de módulo, não métodos.** Cada uma recebe `ctx: SessionContext` e não
guarda estado entre chamadas. O estado da conversa já mora onde deve — na
`Intencao`, que é a máquina de estados auditada. Um objeto com atributos aqui
seria uma segunda fonte de verdade sobre a mesma emissão.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

import structlog
from django.conf import settings
from django.utils import timezone
from pydantic import BaseModel, Field

from apps.agents import llm
from apps.agents.agente_nf.models import Intencao
from apps.agents.agente_nf.services import (
    ErroCancelamento,
    cancelar_emissao,
    confirmar_emissao,
    solicitar_cancelamento,
)
from apps.agents.registry import exposto_ao_modelo
from apps.security.models import Codigo2FA
from apps.security.services import exige_2fa, gerar_codigo_2fa, verificar_codigo_2fa

logger = structlog.get_logger(__name__)

PALAVRAS_CONFIRMACAO = ("sim", "confirmar", "confirmo", "pode emitir", "ok", "👍", "✅")
PALAVRAS_CANCELAMENTO = ("não", "nao", "cancelar", "cancela", "❌")

# Por quanto tempo uma nota incompleta continua "em conversa". Curto de
# propósito: é o tempo de uma conversa, não de uma pendência. Ver `coleta_em_aberto`.
COLETA_TTL_MINUTOS = getattr(settings, "COLETA_NOTA_TTL_MINUTOS", 30)

_CAMPOS_DA_NOTA = (
    ("tomador", "tomador"),
    ("valor", "valor"),
    ("descricao_servico", "descrição do serviço"),
)


@exposto_ao_modelo
class DadosNotaExtraidos(BaseModel):
    """Campos extraídos da mensagem para emitir NFS-e — nunca inclui CNAE/alíquota.

    Nem CNPJ, nem qualquer identificador de escopo: `cnpj_prestador` e `cnae` são
    montados pelo núcleo a partir do cadastro do cliente resolvido no webhook
    (`iniciar_emissao`). O decorador acima é o que impede isso mudar em silêncio.
    """

    tomador: str | None = Field(None, description="Nome de quem recebeu o serviço")
    valor: float | None = Field(None, description="Valor do serviço em reais")
    descricao_servico: str | None = Field(None, description="Descrição do serviço prestado")


@dataclass(frozen=True)
class Retomada:
    """Resultado de continuar uma coleta em aberto.

    `reclassificar` preenchido significa "o cliente mudou de assunto, roteie de
    novo". A coleta não despacha a intenção nova ela mesma de propósito: quem
    conhece o catálogo de ferramentas é o orquestrador, e uma volta daqui para
    lá e de volta criaria um ciclo entre os dois módulos.
    """

    resposta: str | None = None
    reclassificar: str | None = None


# ---------------------------------------------------------------------------
# Extração de campos (única chamada ao modelo deste módulo)
# ---------------------------------------------------------------------------
def extrair_dados_nota(ctx, mensagem: str) -> DadosNotaExtraidos:
    """Campos ditos na mensagem. Devolve tudo nulo quando não há modelo.

    Sem Groq, sem orçamento ou com o provedor fora do ar, a conversa segue pelo
    caminho de coleta: o sistema pergunta o que falta, campo a campo. É mais
    lento e continua correto — o que não pode acontecer é inventar valor.
    """
    if not llm.disponivel():
        return DadosNotaExtraidos()
    try:
        return llm.executar(
            ctx=ctx,
            etapa=llm.ETAPA_EXTRACAO,
            output_type=DadosNotaExtraidos,
            system_prompt=(
                "Extraia tomador, valor (em reais) e descrição do serviço da "
                "mensagem do cliente. Nunca invente CNAE ou alíquota — isso não "
                "é decisão sua. Deixe um campo nulo se não estiver claro na mensagem."
            ),
            mensagem=mensagem,
        )
    except llm.SemOrcamento as exc:
        logger.info("extracao_sem_orcamento_cai_para_coleta", motivo=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("groq_extracao_indisponivel_fallback_menu", erro=str(exc))
    return DadosNotaExtraidos()


# ---------------------------------------------------------------------------
# Continuações em aberto — o que o orquestrador precisa checar antes de rotear
# ---------------------------------------------------------------------------
def confirmacao_pendente(ctx) -> "Intencao | None":
    return (
        Intencao.objects.filter(
            cliente=ctx.cliente, estado=Intencao.Estado.AGUARDANDO_APROVACAO
        )
        .order_by("-criado_em")
        .first()
    )


def coleta_em_aberto(ctx) -> "Intencao | None":
    """Coleta recente do cliente, se houver — e encerra as vencidas.

    A janela existe porque mesclar é perigoso fora do contexto da conversa:
    uma coleta abandonada na semana passada com `tomador=Maria` casaria com
    um "emite nota de 300, consultoria" de hoje e emitiria para a Maria,
    sem ninguém pedir. Passado o prazo, a coleta morre e a próxima mensagem
    começa do zero — que é o que o cliente espera depois de sumir.
    """
    limite = timezone.now() - timedelta(minutes=COLETA_TTL_MINUTOS)
    abertas = Intencao.objects.filter(
        cliente=ctx.cliente, tipo_acao="emitir_nfse", estado=Intencao.Estado.RECEBIDO
    ).order_by("-atualizado_em")

    recente = None
    for intencao in abertas:
        if intencao.atualizado_em >= limite and recente is None:
            recente = intencao
        elif intencao.atualizado_em < limite:
            cancelar_emissao(intencao, motivo="coleta abandonada (sem resposta no prazo)")
    return recente


# ---------------------------------------------------------------------------
# Emissão
# ---------------------------------------------------------------------------
def iniciar_emissao(ctx, mensagem: str) -> str:
    dados = extrair_dados_nota(ctx, mensagem)
    cliente = ctx.cliente
    payload = {
        "cnpj_prestador": cliente.cnpj,
        "cnae": cliente.cnae_padrao,
        "valor": dados.valor,
        "descricao_servico": dados.descricao_servico,
        "tomador": dados.tomador,
    }
    chave = (
        f"nfse-{ctx.message_id}"
        if ctx.message_id
        else f"nfse-{cliente.id}-{uuid.uuid4().hex[:12]}"
    )

    intencao, criada = Intencao.objects.get_or_create(
        chave_idempotencia=chave,
        defaults={"cliente": cliente, "tipo_acao": "emitir_nfse", "payload": payload},
    )
    if not criada:
        # Reprocessamento (retry do Celery) da mesma mensagem — não duplica.
        return mensagem_para_intencao_existente(intencao)

    # A intenção nasce em RECEBIDO e SÓ avança quando estiver completa. Antes
    # ela só era criada com tudo em mãos, e o que faltava era pedido numa
    # mensagem solta — nada era guardado. Quem respondia em partes ("pra
    # Fulano, serviço de TI" e depois "100 reais") via o sistema esquecer o
    # que já tinha dito e pedir de novo, em loop.
    return avancar_coleta(ctx, intencao)


def faltantes(payload: dict) -> list[str]:
    return [rotulo for campo, rotulo in _CAMPOS_DA_NOTA if not payload.get(campo)]


def continuar_coleta(ctx, intencao: Intencao, mensagem: str, reclassificar_fn) -> Retomada:
    """Mescla o que veio agora no que já havia sido dito.

    Desistir também é uma resposta válida aqui: "cancelar" no meio da coleta
    significa abandonar ESTA nota, não procurar uma nota emitida para
    cancelar — que era o que acontecia, e devolvia "não encontrei nenhuma
    nota que possa ser cancelada" para quem só queria desistir.
    """
    if any(p in mensagem.lower().strip() for p in PALAVRAS_CANCELAMENTO):
        cancelar_emissao(intencao, motivo="cliente desistiu durante a coleta")
        return Retomada(
            resposta="Sem problema, cancelei essa nota. Se precisar, é só chamar de novo. 👍"
        )

    dados = extrair_dados_nota(ctx, mensagem)
    novos = {
        "tomador": dados.tomador,
        "valor": dados.valor,
        "descricao_servico": dados.descricao_servico,
    }
    # Valor novo vence o antigo: permite corrigir ("na verdade são 200").
    aproveitados = {campo: valor for campo, valor in novos.items() if valor}

    if not aproveitados:
        # Nada de útil na mensagem. Se ela é claramente outro assunto, o
        # cliente mudou de ideia — insistir na nota o deixaria preso num
        # fluxo que ele abandonou.
        outra = reclassificar_fn(mensagem)
        if outra and outra != "emitir_nota":
            cancelar_emissao(intencao, motivo=f"cliente mudou de assunto ({outra})")
            return Retomada(reclassificar=outra)
        return Retomada(
            resposta="Ainda preciso de: " + ", ".join(faltantes(intencao.payload)) + ". 🧾"
        )

    intencao.payload = {**intencao.payload, **aproveitados}
    intencao.save(update_fields=["payload", "valor", "atualizado_em"])
    return Retomada(resposta=avancar_coleta(ctx, intencao))


def avancar_coleta(ctx, intencao: Intencao) -> str:
    """Pede o que falta, ou fecha a coleta e vai para a confirmação."""
    pendentes = faltantes(intencao.payload)
    if pendentes:
        return "Quase lá! Ainda preciso de: " + ", ".join(pendentes) + ". 🧾"

    if not ctx.cliente.cnae_padrao:
        # Encerra a coleta em vez de deixá-la aberta: falta cadastro, e isso
        # o cliente não resolve respondendo mais nada. Deixar pendurada faria
        # a próxima mensagem dele ser lida como continuação desta nota.
        cancelar_emissao(intencao, motivo="cadastro sem CNAE de serviço")
        return (
            "Seu cadastro ainda não tem o CNAE de serviço configurado. "
            "Fale com seu contador na Rotina para completar o cadastro "
            "antes da primeira emissão. 🙏"
        )

    intencao.transicionar(Intencao.Estado.VALIDANDO, motivo="campos extraídos e CNAE do cadastro")
    intencao.transicionar(
        Intencao.Estado.AGUARDANDO_APROVACAO, motivo="aguardando confirmação Tier 1"
    )

    p = intencao.payload
    return (
        "Confirma a emissão desta nota? 🧾\n"
        f"Tomador: {p['tomador']}\n"
        f"Valor: R$ {float(p['valor']):.2f}\n"
        f"Serviço: {p['descricao_servico']}\n"
        "Responda *sim* para emitir ou *não* para cancelar."
    )


# ---------------------------------------------------------------------------
# Confirmação, 2FA e emissão
# ---------------------------------------------------------------------------
def resolver_confirmacao(ctx, intencao: Intencao, mensagem: str) -> str:
    codigo_pendente = (
        Codigo2FA.objects.filter(intencao=intencao, usado_em__isnull=True)
        .order_by("-criado_em")
        .first()
    )
    if codigo_pendente is not None:
        return _resolver_2fa(ctx, intencao, codigo_pendente, mensagem)

    texto = mensagem.lower().strip()
    if any(p in texto for p in PALAVRAS_CANCELAMENTO):
        cancelar_emissao(intencao, motivo="cliente cancelou")
        return "Combinado, cancelei a emissão. Se precisar, é só chamar de novo. 👍"

    if not any(p in texto for p in PALAVRAS_CONFIRMACAO):
        return (
            "Não entendi. Responda *sim* para confirmar a emissão da nota "
            "pendente ou *não* para cancelar."
        )

    if exige_2fa(intencao):
        registro = gerar_codigo_2fa(intencao)
        if registro is None:
            return (
                "Essa emissão passa do limite de segurança do seu perfil e "
                "precisa de um código extra, mas não encontrei um e-mail "
                "cadastrado pra te mandar. Fale com seu contador na Rotina "
                "pra completar o cadastro. 🙏"
            )
        return (
            "Por segurança, essa emissão passa do limite configurado pro "
            "seu perfil. Te mandei um código de 6 dígitos por e-mail — "
            "só responder aqui com o código. 🔒"
        )

    return emitir(ctx, intencao)


def _resolver_2fa(ctx, intencao: Intencao, codigo_pendente: Codigo2FA, mensagem: str) -> str:
    if codigo_pendente.expira_em <= timezone.now():
        cancelar_emissao(intencao, motivo="código 2FA expirou")
        return "O código expirou. Cancelei a emissão por segurança — pode iniciar de novo."

    if verificar_codigo_2fa(codigo_pendente, mensagem):
        return emitir(ctx, intencao)

    if codigo_pendente.tentativas >= Codigo2FA.LIMITE_TENTATIVAS:
        cancelar_emissao(intencao, motivo="2FA excedeu o número de tentativas")
        return (
            "Código incorreto demais vezes. Cancelei a emissão por segurança — "
            "pode iniciar de novo."
        )

    return "Código inválido. Confere o e-mail e tenta de novo. 🔒"


def motivo_autorizacao(ctx) -> str:
    """Texto do `motivo` da transição que autoriza a emissão.

    Vai para a trilha encadeada e é o que responde, meses depois, "quem
    autorizou esta nota e a partir de quê". Sem o `message_id` não dá para
    apontar a mensagem exata; sem o número, não dá para ligar ao vínculo de
    sessão validado.
    """
    partes = ["cliente confirmou pelo WhatsApp"]
    if ctx.message_id:
        partes.append(f"mensagem {ctx.message_id}")
    # O número de quem escreveu, não o da empresa: com vários autorizados
    # por CNPJ (DEC-03), é ele que responde "quem autorizou".
    if ctx.telefone_de_quem_escreve:
        partes.append(f"wa_id {ctx.telefone_de_quem_escreve}")
    return " — ".join(partes)


def emitir(ctx, intencao: Intencao) -> str:
    # A autorização é o ato que a auditoria precisa reconstituir depois:
    # "cliente confirmou" sozinho não diz QUAL mensagem autorizou nem de
    # qual número. Amarrar aos dois torna a evidência verificável — e é
    # barato, porque os dois valores já estão em mãos aqui.
    resultado = confirmar_emissao(
        intencao,
        motivo=motivo_autorizacao(ctx),
        origem="cliente_whatsapp",
        wa_id=ctx.telefone_de_quem_escreve,
        referencia=ctx.message_id or "",
        exigiu_2fa=intencao.codigos_2fa.filter(usado_em__isnull=False).exists(),
    )

    if resultado.ok:
        return (
            "Nota emitida com sucesso! 🎉\n"
            f"Protocolo: {resultado.protocolo}\n"
            f"DANFSE: {resultado.danfse_url}"
        )

    return (
        "A nota foi rejeitada pela Sefin 😕 "
        f"(motivo: {resultado.erro}). Ajusto os dados e você confirma de novo?"
    )


def mensagem_para_intencao_existente(intencao: Intencao) -> str:
    if intencao.estado == Intencao.Estado.AGUARDANDO_APROVACAO:
        return (
            "Essa nota já está aguardando sua confirmação. Responda *sim* "
            "para emitir ou *não* para cancelar."
        )
    if intencao.estado == Intencao.Estado.CONCLUIDO:
        return (
            "Essa nota já foi emitida anteriormente. Se precisar da 2ª via, "
            "fale com seu contador."
        )
    if intencao.estado in (Intencao.Estado.REJEITADO, Intencao.Estado.CANCELADO):
        return (
            "Essa tentativa de emissão já foi encerrada. Me manda os dados de "
            "novo se quiser tentar outra vez."
        )
    return "Já estou processando essa emissão, só um instante. 🙏"


# ---------------------------------------------------------------------------
# Consulta e cancelamento
# ---------------------------------------------------------------------------
def consultar_notas(ctx) -> str:
    notas = Intencao.objects.filter(
        cliente=ctx.cliente, tipo_acao="emitir_nfse", estado=Intencao.Estado.CONCLUIDO
    ).order_by("-atualizado_em")[:5]

    if not notas:
        return "Você ainda não tem nenhuma nota emitida por aqui. 🧾"

    linhas = ["Suas últimas notas emitidas 🧾", ""]
    for nota in notas:
        valor = nota.payload.get("valor")
        valor_txt = f"R$ {valor:.2f}" if isinstance(valor, (int, float)) else "—"
        status = " ❌ CANCELADA" if nota.cancelada else ""
        linhas.append(
            f"• {nota.atualizado_em.strftime('%d/%m/%Y')} — {valor_txt} — "
            f"{nota.payload.get('tomador') or 'sem tomador'}{status}"
        )
        linhas.append(f"  protocolo {nota.protocolo or '—'}")
        if nota.danfse_url and not nota.cancelada:
            linhas.append(f"  DANFSE: {nota.danfse_url}")
    return "\n".join(linhas)


def pedir_cancelamento(ctx, mensagem: str) -> str:
    """Cliente NUNCA cancela sozinho — o pedido vai pro contador.

    Cancelar documento fiscal tem efeito contábil e prazo legal; a decisão
    é de quem responde tecnicamente por ela. Ver `solicitar_cancelamento`
    em `agents/agente_nf/services.py` pro porquê isso não depende do
    `tier_maximo` do perfil.
    """
    nota = (
        Intencao.objects.filter(
            cliente=ctx.cliente,
            tipo_acao="emitir_nfse",
            estado=Intencao.Estado.CONCLUIDO,
            cancelada_em__isnull=True,
        )
        .order_by("-atualizado_em")
        .first()
    )
    if nota is None:
        return "Não encontrei nenhuma nota emitida que possa ser cancelada. 🤔"

    try:
        solicitar_cancelamento(nota, motivo=mensagem.strip(), origem="whatsapp")
    except ErroCancelamento as exc:
        return f"{exc} 🙏"

    valor = nota.payload.get("valor")
    valor_txt = f"R$ {valor:.2f}" if isinstance(valor, (int, float)) else "—"
    return (
        "Registrei seu pedido de cancelamento 📩\n\n"
        f"Nota: {nota.protocolo}\n"
        f"Valor: {valor_txt}\n"
        f"Emitida em: {nota.atualizado_em.strftime('%d/%m/%Y')}\n\n"
        "Cancelamento de nota fiscal precisa passar pelo seu contador — "
        "ele foi avisado e vai analisar. Te aviso assim que houver resposta. 🙏"
    )
