"""
Eliminação de conteúdo pessoal sem quebrar a trilha imutável.

O problema, registrado em `docs/lgpd-inventario-dados.md` §3: a trilha guarda o
texto das conversas, é append-only, e apagar uma linha quebraria a cadeia de
hash de todas as seguintes. Direito de eliminação (LGPD art. 18, VI) contra
imutabilidade de auditoria fiscal.

A saída é **crypto-shredding**, e ela funciona por causa de um detalhe do
desenho: o hash é calculado sobre o conteúdo de `dados` — seja ele qual for. Se
o que está gravado ali já é **texto cifrado**, destruir a chave torna o conteúdo
irrecuperável **sem alterar um byte da linha**. A cadeia continua verificando, o
registro continua provando que o evento aconteceu, e o conteúdo pessoal some.

Uma chave por titular: eliminar os dados de um cliente não afeta nenhum outro.

⚠ **Só vale para o que for gravado a partir de agora.** As linhas que já existem
estão em texto claro e são imutáveis — não há como cifrá-las retroativamente
(mudar `dados` mudaria o hash). Para elas, a única eliminação possível é apagar
a linha, o que quebra a cadeia. É por isso que ligar isto cedo importa: cada dia
de atraso acrescenta linhas que não poderão ser eliminadas.
"""
from __future__ import annotations

from django.db import models
from django.utils import timezone

# Chaves do payload de auditoria que carregam conteúdo pessoal. Só estas são
# cifradas — o resto (nome do evento, id da intenção, código de erro) precisa
# continuar legível para a trilha servir de auditoria.
CAMPOS_PESSOAIS = frozenset(
    {
        "texto",
        "mensagem",
        "resposta",
        "telefone",
        "tomador",
        "descricao_servico",
        # Número de celular sob outros nomes. `telefone` já estava aqui; estes
        # três entraram no Sprint 2, quando o contexto passou a levar o número de
        # quem escreveu para dentro da trilha (`SessionContext.para_trilha`). O
        # dado é o mesmo — cifrar um e deixar os outros em claro protegeria o
        # nome do campo, não a pessoa.
        "wa_id",
        "wa_id_sessao",
        "wa_id_atual",
    }
)

MARCA_CIFRADO = "__cifrado__"
CONTEUDO_ELIMINADO = "[eliminado a pedido do titular]"
SEM_CHAVE = "[conteúdo não recuperável]"


class ChaveConteudo(models.Model):
    """Chave de conteúdo de um titular. Destruí-la elimina o conteúdo dele."""

    cliente = models.OneToOneField(
        "clients.Cliente", on_delete=models.CASCADE, related_name="chave_conteudo"
    )
    # Cifrada com a chave mestra (apps/credentials/chaves.py) — assim a rotação
    # da chave mestra também cobre estas, sem tratamento especial.
    chave_cifrada = models.BinaryField()
    criado_em = models.DateTimeField(auto_now_add=True)
    destruida_em = models.DateTimeField(
        null=True, blank=True, help_text="Preenchido na eliminação a pedido do titular."
    )

    class Meta:
        verbose_name = "chave de conteúdo (LGPD)"
        verbose_name_plural = "chaves de conteúdo (LGPD)"

    def __str__(self):
        estado = "destruída" if self.destruida_em else "ativa"
        return f"Chave de conteúdo de {self.cliente} ({estado})"

    @property
    def destruida(self) -> bool:
        return self.destruida_em is not None


def _fernet_do_titular(cliente, criar: bool = False):
    """Fernet da chave de conteúdo do titular. `None` se destruída ou ausente."""
    from cryptography.fernet import Fernet

    from apps.credentials.crypto import cifrar_bytes, decifrar_bytes

    registro = ChaveConteudo.objects.filter(cliente=cliente).first()
    if registro is None:
        if not criar:
            return None
        registro = ChaveConteudo.objects.create(
            cliente=cliente, chave_cifrada=cifrar_bytes(Fernet.generate_key())
        )
    if registro.destruida:
        return None
    return Fernet(decifrar_bytes(bytes(registro.chave_cifrada)))


def cifrar_campos_pessoais(dados: dict, cliente) -> dict:
    """Cifra os campos pessoais do payload antes de ele virar linha de auditoria.

    Sem cliente identificado não há chave de titular — nesse caso o conteúdo é
    **omitido** em vez de gravado em claro: mensagem de número não cadastrado é
    justamente a que não temos como eliminar depois.
    """
    if not isinstance(dados, dict):
        return dados

    presentes = [c for c in dados if c in CAMPOS_PESSOAIS and dados[c]]
    if not presentes:
        return dados

    if cliente is None:
        return {**dados, **{c: CONTEUDO_ELIMINADO for c in presentes}}

    fernet = _fernet_do_titular(cliente, criar=True)
    if fernet is None:  # titular já pediu eliminação
        return {**dados, **{c: CONTEUDO_ELIMINADO for c in presentes}}

    cifrados = {
        c: f"{MARCA_CIFRADO}{fernet.encrypt(str(dados[c]).encode()).decode()}" for c in presentes
    }
    return {**dados, **cifrados}


def revelar(dados: dict, cliente) -> dict:
    """Decifra os campos pessoais para exibição. Nunca levanta exceção."""
    if not isinstance(dados, dict):
        return dados

    cifrados = [
        c for c, v in dados.items() if isinstance(v, str) and v.startswith(MARCA_CIFRADO)
    ]
    if not cifrados:
        return dados

    fernet = _fernet_do_titular(cliente) if cliente is not None else None
    if fernet is None:
        return {**dados, **{c: CONTEUDO_ELIMINADO for c in cifrados}}

    revelados = {}
    for campo in cifrados:
        try:
            revelados[campo] = fernet.decrypt(
                dados[campo][len(MARCA_CIFRADO):].encode()
            ).decode()
        except Exception:  # noqa: BLE001
            revelados[campo] = SEM_CHAVE
    return {**dados, **revelados}


def _apagar_conteudo_mutavel(cliente) -> None:
    """Apaga o texto do titular nas tabelas que NÃO são append-only.

    O crypto-shredding existe porque a trilha é imutável — mudar `dados` mudaria
    o hash. Onde essa restrição não vale, cifrar e jogar a chave fora seria
    complicação sem ganho: apagar o texto é mais simples, mais verificável e não
    deixa criptograma nenhum para trás.

    Hoje é uma tabela só (`atendimento.Solicitacao`). Toda tabela nova que
    guardar texto escrito pelo titular precisa entrar aqui — a alternativa
    silenciosa é o direito de eliminação passar a valer só em parte, sem que
    nada acuse.
    """
    from apps.atendimento.models import Solicitacao

    Solicitacao.objects.filter(cliente=cliente).update(
        descricao="", assunto=CONTEUDO_ELIMINADO
    )


def eliminar_conteudo_do_titular(cliente) -> int:
    """Destrói a chave do titular. Devolve quantas linhas ficam ilegíveis.

    Na trilha não apaga nada: as linhas continuam lá, a cadeia continua
    verificando, e o conteúdo pessoal deixa de ser recuperável. É a eliminação
    possível num sistema cuja trilha é imutável por exigência fiscal. Nas tabelas
    mutáveis o texto é apagado de fato — ver `_apagar_conteudo_mutavel`.
    """
    from apps.audit.models import Auditoria

    _apagar_conteudo_mutavel(cliente)

    registro = ChaveConteudo.objects.filter(cliente=cliente).first()
    if registro is None or registro.destruida:
        return 0

    registro.chave_cifrada = b""  # a chave em si some do banco
    registro.destruida_em = timezone.now()
    registro.save(update_fields=["chave_cifrada", "destruida_em"])

    return Auditoria.objects.filter(cliente=cliente).count()
