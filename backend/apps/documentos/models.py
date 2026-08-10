"""
Documentos que o cliente manda pela conversa — nota, boleto, extrato, contrato.

**O que este modelo assume, e é a decisão do sprint:** todo documento entra
para **revisão humana**. Não há OCR ainda, e a ordem é deliberada — o pipeline
funciona ponta a ponta com 100% de revisão, e o OCR, quando entrar, reduz o
volume que precisa de gente. O inverso (ligar o OCR primeiro e ir "acertando")
começaria com lançamento automático de dado não conferido, que é exatamente o
que o gate do Sprint 4 proíbe: *documento com baixa confiança nunca vira
lançamento automático*.

Por isso `confianca` já existe, começa em zero e o estado inicial é
`AGUARDANDO_REVISAO`. Quando o OCR chegar, ele preenche os dois — e o caminho
que leva um documento a ser aceito sem humano continua sendo o mesmo, com um
limiar explícito no meio.

**O arquivo mora no storage; aqui fica o ponteiro.** `bucket` e `chave` são o
endereço; `hash_sha256` é o que responde "é o mesmo arquivo?" e o que permite
reconhecer reenvio sem baixar o objeto de novo.

**LGPD.** O conteúdo do documento é dado pessoal do titular, e diferente da
trilha (append-only, cifrada) este objeto é removível. A eliminação a pedido do
titular apaga o objeto no storage e a linha aqui — ver
`apps/audit/conteudo.eliminar_conteudo_do_titular`.
"""
from django.db import models
from django.utils import timezone


class Documento(models.Model):
    """Um arquivo recebido do cliente, e o que se sabe sobre ele."""

    class Tipo(models.TextChoices):
        # Ainda não classificado — o estado de quase tudo enquanto não há OCR.
        DESCONHECIDO = "desconhecido", "A classificar"
        NOTA_ENTRADA = "nota_entrada", "Nota de entrada"
        NOTA_SERVICO = "nota_servico", "Nota de serviço"
        BOLETO = "boleto", "Boleto"
        EXTRATO = "extrato", "Extrato bancário"
        CONTRATO = "contrato", "Contrato"
        OUTRO = "outro", "Outro"

    class Situacao(models.TextChoices):
        AGUARDANDO_REVISAO = "aguardando_revisao", "Aguardando revisão"
        CLASSIFICADO = "classificado", "Classificado"
        RECUSADO = "recusado", "Recusado"

    class Origem(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"
        PAINEL = "painel", "Enviado no painel"

    cliente = models.ForeignKey(
        "clients.Cliente", on_delete=models.CASCADE, related_name="documentos"
    )
    # Quem mandou, sob DEC-03: a empresa tem vários autorizados, e "quem mandou
    # este extrato" é pergunta que aparece quando o documento está errado.
    usuario = models.ForeignKey(
        "clients.Usuario",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documentos",
    )

    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.DESCONHECIDO)
    situacao = models.CharField(
        max_length=20, choices=Situacao.choices, default=Situacao.AGUARDANDO_REVISAO
    )
    origem = models.CharField(max_length=12, choices=Origem.choices, default=Origem.WHATSAPP)

    # --- endereço no storage ---------------------------------------------
    bucket = models.CharField(max_length=80)
    chave = models.CharField(max_length=300)
    nome_arquivo = models.CharField(max_length=200)
    tipo_mime = models.CharField(max_length=100, blank=True, default="")
    tamanho = models.PositiveIntegerField(default=0)
    hash_sha256 = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Identifica o arquivo sem baixá-lo — é como reenvio do mesmo documento é reconhecido.",
    )

    # --- o que o OCR vai preencher (Sprint 4, fase 2) ---------------------
    dados_extraidos = models.JSONField(
        default=dict,
        blank=True,
        help_text="Chave de acesso, valor, CNPJ emitente, vencimento. Vazio enquanto não há OCR.",
    )
    confianca = models.PositiveSmallIntegerField(
        default=0,
        help_text=(
            "0 a 100. Começa em zero e assim permanece sem OCR — e zero nunca "
            "vira lançamento automático, que é o gate do sprint."
        ),
    )

    protocolo = models.CharField(
        max_length=24,
        unique=True,
        help_text="O que o cliente cita ao cobrar. Gerado no núcleo, estável.",
    )
    observacao = models.CharField(max_length=200, blank=True, default="")

    revisado_por = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documentos_revisados",
    )
    revisado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "documento"
        verbose_name_plural = "documentos"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["cliente", "situacao"], name="documento_cliente_situacao"),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.protocolo} — {self.cliente}"

    @property
    def aguardando(self) -> bool:
        return self.situacao == self.Situacao.AGUARDANDO_REVISAO

    def classificar(self, tipo: str, por=None, observacao: str = "") -> None:
        self.tipo = tipo
        self.situacao = self.Situacao.CLASSIFICADO
        self.revisado_por = por
        self.revisado_em = timezone.now()
        if observacao:
            self.observacao = observacao[:200]
        self.save(
            update_fields=[
                "tipo", "situacao", "revisado_por", "revisado_em", "observacao"
            ]
        )

    def recusar(self, por=None, motivo: str = "") -> None:
        """Ilegível, duplicado ou nada a ver — e o cliente merece saber qual."""
        self.situacao = self.Situacao.RECUSADO
        self.revisado_por = por
        self.revisado_em = timezone.now()
        self.observacao = (motivo or "recusado na revisão")[:200]
        self.save(
            update_fields=["situacao", "revisado_por", "revisado_em", "observacao"]
        )
