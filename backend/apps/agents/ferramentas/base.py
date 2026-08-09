"""
O catálogo de ferramentas — o que o agente sabe fazer, declarado em um lugar só.

Até o Sprint 2 as nove intenções eram um `if/elif` dentro do orquestrador. Isso
funcionava e tinha três defeitos que só apareceriam com o catálogo crescendo:

1. **O tier era conferido por handler**, cada um do seu jeito — e um handler novo
   que esquecesse a checagem executava sem governança nenhuma. O catálogo de
   `governance/tiers.py` já tinha divergido do orquestrador uma vez (26/jul), e o
   sintoma foi consulta legítima recusada em produção.
2. **Não havia como perguntar "o que este cliente pode fazer"** sem ler o corpo
   de um método. O prompt por tenant (DEC-08) precisa exatamente dessa lista.
3. **Nada ligava a intenção ao schema** que o modelo preenche, então a garantia
   da DEC-05 valia para os schemas que por acaso estavam decorados.

Aqui os três somem por construção: o tier é conferido em `executar` e em lugar
nenhum mais, `disponiveis_para` responde a pergunta do prompt, e registrar uma
ferramenta com `entrada` exige um schema que já passou pelo guarda do registry.

**O que uma ferramenta NÃO recebe.** Nada de `cliente`, `cnpj` ou `escritorio` em
parâmetro. O handler recebe `(ctx, mensagem)` — escopo vem do `SessionContext`,
que o webhook montou, e o texto é só texto. É a metade prática da DEC-05: o
registry impede o campo de existir no schema, isto impede que ele apareça na
assinatura por outro caminho.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import structlog

from apps.audit.services import registrar
from apps.governance.tiers import tier_da_intencao, verificar_tier

logger = structlog.get_logger(__name__)

RECUSA_PADRAO = (
    "Essa operação não está liberada para o seu perfil no momento. "
    "Fale com seu contador para habilitá-la. 🙏"
)


@dataclass(frozen=True)
class Ferramenta:
    """Uma capacidade do agente, com tier, descrição e execução."""

    nome: str
    # Frase em primeira pessoa do catálogo, na voz do produto. Vai literalmente
    # para o system prompt do tenant, então é redação de produto e não comentário
    # de código — o modelo roteia por ela.
    descricao: str
    executar: Callable[..., str]
    # Schema que o modelo preenche, quando existe. `None` significa que a
    # ferramenta não recebe nada do modelo — a maioria das consultas.
    entrada: type | None = None
    # Depende de um adaptador de ERP conectado. Ferramenta de ERP oferecida a
    # quem não tem integração vira promessa vazia: o cliente pede e ouve
    # "não consegui consultar". Melhor não constar do catálogo dele.
    exige_erp: bool = False
    # Texto de recusa por tier. Cada uma tem o seu porque "consulta de notas não
    # está liberada" e "emissão não está liberada" levam o cliente a pedidos
    # diferentes ao contador.
    recusa: str = RECUSA_PADRAO
    exemplos: tuple[str, ...] = field(default_factory=tuple)
    # `False` quando a ferramenta **não executa** a ação do catálogo, só abre um
    # pedido para quem executa. Só `cancelar_nota` usa isso hoje, e o motivo está
    # escrito no registro dela: o cliente nunca cancela, ele pede — e travar o
    # pedido no tier do cancelamento (3) faria o perfil comum ouvir "não
    # liberado" ao tentar avisar que uma nota saiu errada, que é o oposto do que
    # se quer. A trava do cancelamento é do fluxo, não do tier
    # (`tests/test_cancelamento_nota.py` prova que nem Tier 3 cancela sozinho).
    tier_conferido: bool = True

    @property
    def tier(self) -> int:
        """Vem de `governance/tiers.py` — uma fonte de verdade, não duas.

        Guardar o tier aqui criaria a mesma divergência que o catálogo já sofreu
        uma vez. `tests/test_governance.py` exige que todo nome registrado tenha
        entrada explícita lá; nome ausente cai no fail-safe Tier 3 e é recusado.
        """
        return tier_da_intencao(self.nome)


FERRAMENTAS: dict[str, Ferramenta] = {}


def registrar_ferramenta(
    nome: str,
    *,
    descricao: str,
    entrada: type | None = None,
    exige_erp: bool = False,
    recusa: str = RECUSA_PADRAO,
    exemplos: tuple[str, ...] = (),
    tier_conferido: bool = True,
):
    """Decorador de registro. O nome é o mesmo que o roteador emite.

    Registrar duas ferramentas com o mesmo nome é erro no import, não a última
    ganhando em silêncio: duas capacidades disputando um nome significa que uma
    delas nunca vai rodar, e descobrir isso em produção custa uma investigação.
    """

    def decorador(fn):
        if nome in FERRAMENTAS:
            raise ValueError(
                f"Ferramenta {nome!r} já registrada por "
                f"{FERRAMENTAS[nome].executar.__module__}."
            )
        FERRAMENTAS[nome] = Ferramenta(
            nome=nome,
            descricao=descricao,
            executar=fn,
            entrada=entrada,
            exige_erp=exige_erp,
            recusa=recusa,
            exemplos=exemplos,
            tier_conferido=tier_conferido,
        )
        return fn

    return decorador


def obter(nome: str) -> Ferramenta | None:
    return FERRAMENTAS.get(nome)


def nomes() -> tuple[str, ...]:
    """Nomes registrados, em ordem estável.

    Ordem de registro e não alfabética: é ela que define a ordem das linhas no
    system prompt, e agrupar por assunto (nota, ERP, atendimento) ajuda mais um
    modelo pequeno do que ordenar por acaso da inicial.
    """
    return tuple(FERRAMENTAS)


def _tem_erp(ctx) -> bool:
    """O cliente tem alguma integração de ERP entre as ferramentas habilitadas.

    Lê o perfil, e não o cofre de credenciais, de propósito: quem decide o que é
    oferecido é a configuração do atendimento. Se a credencial existe mas está
    vencida, a tool continua no catálogo e o erro sai do adaptador, com mensagem
    específica — que é mais útil que a capacidade sumir sem explicação.
    """
    habilitadas = getattr(ctx.perfil, "ferramentas_habilitadas", None) or []
    return any(f in ("conta_azul", "bling", "erp_mock") for f in habilitadas)


def disponiveis_para(ctx) -> list[Ferramenta]:
    """O que este cliente, com este perfil, pode de fato usar agora.

    Três filtros, nesta ordem: existe empresa em foco, o tier do perfil alcança
    a ferramenta, e — para as de ERP — há integração habilitada. É esta lista
    que vira o system prompt do tenant, e é por isso que ela precisa ser
    conservadora: oferecer no prompt o que será recusado na execução ensina o
    cliente a pedir o que não pode ter.
    """
    perfil = ctx.perfil
    if ctx.cliente is None or perfil is None:
        # Perfil ausente é cliente cadastrado e ainda não configurado — o motor
        # de tiers já trata isso como "nada liberado" (fail-safe). A checagem
        # precisa vir ANTES da exceção de `tier_conferido`: sem ela,
        # `cancelar_nota` (que dispensa tier de propósito) apareceria no catálogo
        # de quem não tem atendimento nenhum ligado, oferecendo cancelar uma nota
        # que não existe.
        return []
    com_erp = _tem_erp(ctx)
    return [
        f
        for f in FERRAMENTAS.values()
        if (not f.tier_conferido or verificar_tier(f.tier, perfil))
        and (com_erp or not f.exige_erp)
    ]


def executar(nome: str, ctx, mensagem: str = "") -> str | None:
    """Roda a ferramenta, conferindo tier e medindo. `None` = não existe.

    Devolver `None` em vez de levantar é o que deixa o orquestrador responder com
    o menu de capacidades quando o roteador devolve `desconhecida` — que é o
    desfecho mais comum e não é erro.
    """
    ferramenta = obter(nome)
    if ferramenta is None:
        return None

    if ctx.cliente is None:
        # Não deveria acontecer: o orquestrador barra antes. Mantido porque este
        # é o ponto por onde toda execução passa, e uma ferramenta rodando sem
        # empresa em foco leria dado de ninguém — ou, pior, de quem sobrar.
        logger.warning("ferramenta_sem_cliente_em_foco", ferramenta=nome)
        return RECUSA_PADRAO

    # `tier_conferido=False` dispensa a checagem de *tier*, não a de cliente
    # configurado: sem perfil, nada roda.
    if ctx.perfil is None or (
        ferramenta.tier_conferido and not verificar_tier(ferramenta.tier, ctx.perfil)
    ):
        registrar(
            "ferramenta_recusada_por_tier",
            {"ferramenta": nome, "tier_exigido": ferramenta.tier, **ctx.para_trilha()},
            cliente=ctx.cliente,
        )
        return ferramenta.recusa

    inicio = time.perf_counter()
    try:
        resposta = ferramenta.executar(ctx, mensagem)
    except Exception:
        registrar(
            "ferramenta_falhou",
            {"ferramenta": nome, **ctx.para_trilha()},
            cliente=ctx.cliente,
        )
        raise

    registrar(
        "ferramenta_executada",
        {
            "ferramenta": nome,
            "tier": ferramenta.tier,
            "latencia_ms": int((time.perf_counter() - inicio) * 1000),
            **ctx.para_trilha(),
        },
        cliente=ctx.cliente,
    )
    return resposta
