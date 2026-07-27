"""
Exemplos de intenção — como o roteador aprende sem release.

Motivação concreta (25/jul/2026): "envie um relatório das vendas" caía na
resposta genérica, e a correção exigiu commit + deploy. Cada jeito novo de o
cliente falar virava trabalho de desenvolvedor. Aqui o exemplo é **dado**: quem
atende cadastra a frase e a intenção certa no admin, e o roteador passa a
acertar já na mensagem seguinte.

Por que exemplo curado e não a conversa crua
--------------------------------------------
A tentação é alimentar o classificador com o histórico real. Duas razões para
não fazer isso:

1. **LGPD.** Conversa real é dado pessoal do titular, e o titular pode pedir
   eliminação (`eliminar_dados_titular`). Um corpus de treino alimentado com
   texto cru viraria uma cópia paralela justamente do que precisamos poder
   destruir. Exemplo despersonalizado não é dado do titular — é gramática do
   negócio.
2. **Qualidade.** Frase real vem com ruído, erro de digitação e contexto que não
   se repete. Exemplo curado generaliza melhor com muito menos volume.

Por que no prompt e não em RAG
------------------------------
Recuperação existe para contornar limite de contexto. Com 9 intenções e ordem de
centenas de exemplos curtos, tudo cabe no prompt — e o prompt inteiro é melhor
que vizinhos recuperados, porque a recuperação pode trazer justamente os
exemplos da intenção errada e piorar a decisão. `LIMITE_NO_PROMPT` marca onde
essa premissa deixa de valer: passando disso, aí sim vale embedding.

O contrato ResultadoAcao (dataclass) continua em apps/core/resultado.py.
"""
from django.db import models

# Acima disto o prompt fica grande o bastante para custar latência e diluir as
# regras de desambiguação — é o gatilho para reavaliar recuperação por
# similaridade, não um limite técnico rígido.
LIMITE_NO_PROMPT = 300


class ExemploIntencao(models.Model):
    """Frase de exemplo → intenção que ela representa."""

    INTENCOES = [
        ("emitir_nota", "Emitir nota fiscal"),
        ("consultar_nota", "Consultar notas emitidas"),
        ("cancelar_nota", "Cancelar nota emitida"),
        ("consultar_estoque", "Consultar estoque"),
        ("consultar_pedido", "Consultar pedidos/vendas"),
        ("consultar_contas_receber", "Contas a receber"),
        ("consultar_contas_pagar", "Contas a pagar"),
        ("consultar_fluxo_caixa", "Fluxo de caixa"),
        ("desconhecida", "Nenhuma das anteriores"),
    ]

    frase = models.CharField(
        max_length=300,
        unique=True,
        help_text=(
            "Como o cliente falaria. ⚠ Não cole a mensagem real de um cliente: "
            "troque nome, CNPJ e valores por exemplos genéricos."
        ),
    )
    intencao = models.CharField(max_length=40, choices=INTENCOES)
    ativo = models.BooleanField(
        default=True, help_text="Desmarque para tirar do prompt sem perder o registro."
    )
    observacao = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="Por que este exemplo existe — ex.: “cliente chamou nota de recibo”.",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "exemplo de intenção"
        verbose_name_plural = "exemplos de intenção"
        ordering = ["intencao", "frase"]

    def __str__(self):
        return f"{self.frase} → {self.intencao}"


def exemplos_para_prompt() -> str:
    """Bloco de exemplos a acrescentar ao prompt do roteador. Vazio se não houver.

    Agrupado por intenção porque o classificador erra justamente entre as três
    intenções de nota: exemplos lado a lado tornam a fronteira visível.
    """
    por_intencao: dict[str, list[str]] = {}
    for exemplo in ExemploIntencao.objects.filter(ativo=True)[:LIMITE_NO_PROMPT]:
        por_intencao.setdefault(exemplo.intencao, []).append(exemplo.frase)

    if not por_intencao:
        return ""

    linhas = ["", "Exemplos cadastrados pela operação (têm precedência sobre sua intuição):"]
    for intencao, frases in sorted(por_intencao.items()):
        citadas = "; ".join(f'"{f}"' for f in frases)
        linhas.append(f"- {intencao}: {citadas}")
    return "\n".join(linhas)
