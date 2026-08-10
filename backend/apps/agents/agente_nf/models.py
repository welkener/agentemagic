"""
Intenção fiscal + máquina de estados da emissão (seção 5.1 da arquitetura):

    RECEBIDO → VALIDANDO → AGUARDANDO_APROVACAO → EMITINDO → CONCLUIDO
                    │                                  │
                    └── REJEITADO                      └── REJEITADO
    (CANCELADO pode ocorrer antes da emissão começar)

Toda transição é auditada (trilha append-only) — nada muda de estado em
silêncio.
"""
from decimal import Decimal, InvalidOperation

from django.db import models

from apps.audit.services import registrar
from apps.clients.models import Cliente


class TransicaoInvalida(Exception):
    """Levantada quando uma transição de estado não é permitida."""


class Intencao(models.Model):
    """Intenção de emissão fiscal proposta pelo LLM e decidida pelo núcleo."""

    class Estado(models.TextChoices):
        RECEBIDO = "RECEBIDO", "Recebido"
        VALIDANDO = "VALIDANDO", "Validando"
        AGUARDANDO_APROVACAO = "AGUARDANDO_APROVACAO", "Aguardando aprovação"
        EMITINDO = "EMITINDO", "Emitindo"
        CONCLUIDO = "CONCLUIDO", "Concluído"
        REJEITADO = "REJEITADO", "Rejeitado"
        CANCELADO = "CANCELADO", "Cancelado"

    # Transições permitidas — dicionário explícito, nada de inferência.
    TRANSICOES_PERMITIDAS: dict[str, set[str]] = {
        Estado.RECEBIDO: {Estado.VALIDANDO, Estado.CANCELADO},
        Estado.VALIDANDO: {Estado.AGUARDANDO_APROVACAO, Estado.REJEITADO, Estado.CANCELADO},
        Estado.AGUARDANDO_APROVACAO: {Estado.EMITINDO, Estado.REJEITADO, Estado.CANCELADO},
        Estado.EMITINDO: {Estado.CONCLUIDO, Estado.REJEITADO},
        # Estados terminais
        Estado.CONCLUIDO: set(),
        Estado.REJEITADO: set(),
        Estado.CANCELADO: set(),
    }

    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name="intencoes_fiscais"
    )
    chave_idempotencia = models.CharField(max_length=100, unique=True)
    tipo_acao = models.CharField(max_length=40, default="emitir_nfse")
    payload = models.JSONField(default=dict)
    # Valor da nota, desnormalizado do `payload` para ser somável e ordenável
    # no banco. O payload continua sendo a verdade do que foi pedido; este
    # campo é derivado e sincronizado no `save()`. Existe porque análise de
    # faturamento não pode depender de cast de JSON: o valor chega do LLM ora
    # como número, ora como string, e um cast que falha some com a linha da
    # soma **em silêncio** — o contador veria um total menor sem saber.
    valor = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        db_index=True,
        help_text="Derivado de payload['valor'] — não editar à mão.",
    )
    estado = models.CharField(
        max_length=24, choices=Estado.choices, default=Estado.RECEBIDO
    )
    protocolo = models.CharField(
        max_length=100, blank=True, default="", help_text="Preenchido na emissão bem-sucedida (CONCLUIDO)."
    )
    danfse_url = models.URLField(
        blank=True, default="", help_text="Link do DANFSE — preenchido na emissão bem-sucedida."
    )
    chave_nfse = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text=(
            "Chave de acesso da NFS-e (50 dígitos), devolvida pela Sefin na autorização. "
            "NÃO é o protocolo: protocolo identifica o processamento, a chave identifica "
            "o documento — e é a chave que o evento de cancelamento exige."
        ),
    )

    # --- Cancelamento (tipo_acao="cancelar_nfse") --------------------------
    # O pedido de cancelamento é uma Intencao PRÓPRIA, que aponta pra nota
    # cancelada. Não se reaproveita o estado CANCELADO da nota original: lá
    # ele significa "o cliente desistiu ANTES de emitir", coisa completamente
    # diferente de "a nota existiu e foi cancelada na Sefin". Misturar os dois
    # apagaria essa distinção da trilha de auditoria de um sistema fiscal.
    intencao_original = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pedidos_cancelamento",
        help_text="Só para tipo_acao='cancelar_nfse': a nota que se quer cancelar.",
    )
    cancelada_em = models.DateTimeField(
        null=True, blank=True, help_text="Preenchido na NOTA quando o cancelamento é aceito."
    )
    protocolo_cancelamento = models.CharField(
        max_length=100, blank=True, default="", help_text="Protocolo do evento de cancelamento."
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        # Nome de produto, não de arquitetura: "intenção fiscal" descreve o
        # que o modelo é por dentro (proposta do LLM decidida pelo núcleo), e o
        # contador não pensa nesse termo — ele procura a nota.
        verbose_name = "nota fiscal"
        verbose_name_plural = "notas fiscais"

    def valor_do_payload(self) -> "Decimal | None":
        """Lê `payload['valor']` como Decimal, ou None se não der.

        Devolver None em vez de zero é deliberado: zero seria somado como uma
        nota de R$ 0,00 e afundaria a média; None sai da conta e aparece como
        "—" na tela, que é a verdade — não sabemos o valor.
        """
        bruto = (self.payload or {}).get("valor")
        if bruto is None or bruto == "":
            return None
        try:
            return Decimal(str(bruto)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return None

    def save(self, *args, **kwargs):
        """Mantém `valor` em sincronia com o payload em qualquer caminho de escrita.

        Sincronizar aqui, e não em cada `objects.create(...)`, é o que garante
        que nenhum caminho novo de criação esqueça o campo — inclusive os
        `update_fields` estreitos de `transicionar()`.
        """
        derivado = self.valor_do_payload()
        if derivado != self.valor:
            self.valor = derivado
            campos = kwargs.get("update_fields")
            if campos is not None:
                kwargs["update_fields"] = [*campos, "valor"]
        super().save(*args, **kwargs)

    def transicionar(self, novo_estado: str, motivo: str = "") -> None:
        """Muda de estado respeitando o dicionário de transições e auditando.

        Levanta `TransicaoInvalida` para qualquer caminho fora do fluxo
        (ex.: pular direto de RECEBIDO para EMITINDO).
        """
        permitidos = self.TRANSICOES_PERMITIDAS.get(self.estado, set())
        if novo_estado not in permitidos:
            raise TransicaoInvalida(
                f"Transição {self.estado} → {novo_estado} não é permitida."
            )
        estado_anterior = self.estado
        self.estado = novo_estado
        self.save(update_fields=["estado", "atualizado_em"])
        registrar(
            "intencao_fiscal_transicao",
            {
                "intencao_id": self.id,
                "de": estado_anterior,
                "para": novo_estado,
                "motivo": motivo,
            },
            cliente=self.cliente,
        )

    @property
    def cancelada(self) -> bool:
        return self.cancelada_em is not None

    @property
    def pode_ser_cancelada(self) -> bool:
        """Só nota emitida de fato e ainda não cancelada.

        Não cobre o **prazo legal** de cancelamento, que varia por município e
        NT vigente — quem recusa fora do prazo é a Sefin, e o adapter devolve
        a rejeição. Fingir aqui um prazo que não foi confirmado seria pior que
        deixar a autoridade decidir.
        """
        return (
            self.tipo_acao == "emitir_nfse"
            and self.estado == self.Estado.CONCLUIDO
            and not self.cancelada
        )

    def __str__(self):
        return f"Intenção {self.id} ({self.tipo_acao}) — {self.estado}"


class Confirmacao(models.Model):
    """Quem autorizou uma emissão ou um cancelamento, e por qual ato.

    **Por que uma tabela, se a trilha já registrava.** Até aqui a confirmação em
    duas etapas vivia dentro do `motivo` da transição para EMITINDO — um texto
    livre, correto e verdadeiro, mas que só responde à pergunta se alguém abrir a
    trilha e ler linha por linha. O critério de aceite da plataforma é outro:
    *"nenhuma nota sem confirmação registrada"*, e afirmação assim precisa ser
    **consultável**, não reconstituível. A diferença aparece no dia em que
    alguém contesta uma nota e a resposta tem que sair de um `filter`, não de
    uma leitura.

    A trilha continua sendo a fonte imutável e encadeada; esta tabela é o índice
    do ato de autorizar. As duas são gravadas na mesma operação — se um dia
    divergirem, `tests/test_confirmacoes.py` acusa.

    **O que ela deliberadamente não guarda:** o texto da mensagem do cliente. Ele
    já está na trilha, cifrado por titular, e uma segunda cópia aqui ficaria fora
    do alcance da eliminação por LGPD. O que fica é a referência — o
    `message_id` —, que aponta para lá sem duplicar conteúdo.
    """

    class Origem(models.TextChoices):
        CLIENTE_WHATSAPP = "cliente_whatsapp", "Cliente, pelo WhatsApp"
        CONTADOR_PAINEL = "contador_painel", "Contador, no Grimório"
        EQUIPE_ADMIN = "equipe_admin", "Equipe, pelo admin"

    intencao = models.ForeignKey(
        Intencao, on_delete=models.CASCADE, related_name="confirmacoes"
    )
    origem = models.CharField(max_length=20, choices=Origem.choices)
    # Quem autorizou, na forma que aquele canal identifica: o número de quem
    # escreveu (DEC-03 — a empresa tem vários autorizados) ou o login de quem
    # clicou. Um dos dois, nunca os dois.
    wa_id = models.CharField(max_length=20, blank=True, default="")
    usuario = models.CharField(max_length=150, blank=True, default="")
    # A mensagem exata que autorizou. Sem ela, "o cliente confirmou" não aponta
    # para nada verificável.
    referencia = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="`message_id` do WhatsApp, ou vazio quando a origem é o painel.",
    )
    exigiu_2fa = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "confirmação"
        verbose_name_plural = "confirmações"
        ordering = ["-criado_em"]
        indexes = [models.Index(fields=["intencao", "criado_em"], name="confirmacao_intencao")]

    def __str__(self):
        return f"{self.get_origem_display()} — intenção {self.intencao_id}"

    @property
    def autor(self) -> str:
        return self.usuario or self.wa_id or "não identificado"
