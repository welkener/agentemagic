"""
Consumo de LLM por tenant — o item 2 da DEC-08.

**Por que uma tabela em vez de uma métrica de log.** O critério de aceite da
plataforma é "custo de LLM por cliente/mês abaixo de R$ 0,60". Isso é uma
pergunta de negócio, feita meses depois, por cliente e por competência — não uma
série temporal de observabilidade que expira em 15 dias. Log estruturado
responde "está caro agora?"; só uma tabela responde "quanto custou o cliente X
em julho", que é o que decide preço e é o que o gate do Sprint 2 exige medido.

**Uma linha por chamada ao modelo, não por mensagem.** Uma mensagem pode custar
duas chamadas (roteador e extração de campos da nota) com modelos e preços
diferentes; somá-las numa linha só apagaria justamente a informação que faz a
degradação de gasto ser possível — qual das duas etapas está cara.

**Os tokens são a verdade; o custo é derivado.** `custo_brl` é gravado no
momento da chamada, com a tabela de preços e a cotação vigentes, porque
recalcular tudo com o preço de hoje mentiria sobre o passado. Mas os tokens
ficam guardados em bruto: se a tabela mudar e for preciso reprocessar, dá.

⚠ **Limite honesto:** só entra aqui o que passou pelo modelo. Mensagem resolvida
no T0 custa zero e por isso não gera linha — o que significa que esta tabela
sozinha **não** mede latência típica de atendimento, só a das mensagens caras.
A latência ponta a ponta de toda mensagem fica na trilha de auditoria (campo
`latencia_ms` do evento `orquestrador_mensagem_processada`), que é de onde o p95
do gate é calculado.
"""
from django.db import models


class ConsumoLLM(models.Model):
    """Uma chamada ao modelo: quem pagou, quanto custou, quanto demorou."""

    class Etapa(models.TextChoices):
        ROTEADOR = "roteador", "Classificação de intenção"
        EXTRACAO = "extracao", "Extração de campos"

    # PROTECT: apagar um escritório com histórico de consumo apagaria a base de
    # cálculo da fatura dele. Se um dia for preciso encerrar um tenant, o
    # caminho é desativar (`Escritorio.ativo`), não excluir.
    escritorio = models.ForeignKey(
        "tenants.Escritorio", on_delete=models.PROTECT, related_name="consumo_llm"
    )
    # Nulo é estado real: o roteador pode rodar antes de a empresa estar
    # resolvida. O custo é do escritório de qualquer jeito — é ele que paga.
    cliente = models.ForeignKey(
        "clients.Cliente", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="consumo_llm",
    )

    etapa = models.CharField(max_length=20, choices=Etapa.choices)
    modelo = models.CharField(
        max_length=80,
        help_text="Identificador exato do modelo chamado — é a chave da tabela de preços.",
    )

    tokens_entrada = models.PositiveIntegerField(default=0)
    tokens_saida = models.PositiveIntegerField(default=0)
    tokens_cache_leitura = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Tokens de entrada servidos do cache do provedor, cobrados pela metade. "
            "Medi-los separado é o que permite responder, com número, se o system "
            "prompt por tenant compensa (DEC-08 item 4)."
        ),
    )
    requisicoes = models.PositiveIntegerField(
        default=1, help_text="Chamadas HTTP reais — retry de validação do Pydantic AI conta."
    )
    tool_calls = models.PositiveIntegerField(default=0)

    latencia_ms = models.PositiveIntegerField(
        default=0, help_text="Tempo da chamada ao modelo, do envio à resposta validada."
    )
    custo_brl = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        default=0,
        help_text=(
            "Seis casas porque uma mensagem custa frações de centavo — arredondar "
            "para dois zeraria toda linha e a soma do mês daria zero."
        ),
    )
    # Preenchido quando a chamada falhou. A linha é gravada assim mesmo: uma
    # chamada que estourou consumiu tempo e às vezes tokens, e some do custo se
    # só o sucesso for registrado.
    erro = models.CharField(max_length=200, blank=True, default="")

    momento = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "consumo de LLM"
        verbose_name_plural = "consumo de LLM"
        indexes = [
            # A consulta que existe é sempre "deste escritório, neste período" —
            # fatura, teto de gasto e a tela de Operação fazem as três a mesma
            # pergunta. Sem este índice o teto de gasto varre a tabela inteira a
            # cada mensagem, que é o pior lugar possível para isso.
            models.Index(fields=["escritorio", "momento"], name="consumo_escritorio_momento"),
            models.Index(fields=["cliente", "momento"], name="consumo_cliente_momento"),
        ]

    def __str__(self):
        return f"{self.modelo} — {self.escritorio_id} — R$ {self.custo_brl}"
