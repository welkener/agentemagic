"""
A rotina contábil que o escritório de fato entrega: guias, obrigações e certidões.

**De onde vem o dado, e por que essa é a pergunta central.** Não há API pública
utilizável para buscar DAS, DARF, GPS ou o status da DCTFWeb — é o mesmo motivo
que fez a DEC-09 pôr o `ArquivoAdapter` antes de qualquer integração. Então
estas tabelas são preenchidas **pelo escritório**: hoje à mão pelo Grimório, no
Sprint 5 por importação de arquivo, e um dia por API onde ela existir.

Isso decide o comportamento das ferramentas que leem daqui: quando não há
registro, a resposta é **"ainda não tenho essa guia"** — nunca um valor
calculado, estimado ou inferido. Um DAS inventado é pior que nenhum: o cliente
paga errado, e paga confiando.

**Por que não guardar só o PDF.** O valor e o vencimento ficam em coluna própria
porque são o que a conversa responde ("quanto é o DAS de julho?") e o que a fila
do contador ordena (o que vence primeiro). Um anexo sozinho obrigaria alguém a
abrir o arquivo para saber as duas coisas que mais se pergunta.

**Competência é texto `AAAA-MM`, não data.** Guia de julho não acontece num dia
de julho — ela se refere ao mês inteiro. Guardar como `date` obrigaria a
escolher um dia arbitrário e convidaria a comparações erradas ("vence dia 1º?").
"""
from django.db import models
from django.utils import timezone


class Competencia(models.CharField):
    """`AAAA-MM`. Um `CharField` com validação, e não um `DateField`.

    Ver o cabeçalho do módulo: o mês de referência não é um dia.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 7)
        kwargs.setdefault("help_text", "Mês de referência no formato AAAA-MM, ex.: 2026-07.")
        super().__init__(*args, **kwargs)


class Guia(models.Model):
    """Uma guia de recolhimento daquele cliente, naquela competência."""

    class Tipo(models.TextChoices):
        DAS = "das", "DAS — Simples Nacional"
        DARF = "darf", "DARF"
        GPS = "gps", "GPS — INSS"
        FGTS = "fgts", "FGTS"
        ISS = "iss", "ISS municipal"

    class Situacao(models.TextChoices):
        ABERTA = "aberta", "Em aberto"
        PAGA = "paga", "Paga"
        CANCELADA = "cancelada", "Cancelada"

    cliente = models.ForeignKey(
        "clients.Cliente", on_delete=models.CASCADE, related_name="guias"
    )
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    competencia = Competencia()
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    vencimento = models.DateField()
    situacao = models.CharField(
        max_length=12, choices=Situacao.choices, default=Situacao.ABERTA
    )

    # O documento em si. Fica opcional porque o valor e o vencimento já
    # respondem à conversa — e porque muitas vezes o escritório sabe o número
    # antes de ter o PDF em mãos.
    url_documento = models.URLField(
        blank=True, default="", help_text="Link do PDF da guia, se houver."
    )
    codigo_barras = models.CharField(
        max_length=60,
        blank=True,
        default="",
        help_text="Linha digitável, para o cliente pagar sem abrir o arquivo.",
    )
    observacao = models.CharField(max_length=200, blank=True, default="")

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "guia"
        verbose_name_plural = "guias"
        ordering = ["-competencia", "tipo"]
        constraints = [
            # Uma guia por tipo e competência. Duas iguais no sistema significam
            # que alguém lançou duas vezes — e o cliente pode acabar pagando as
            # duas, ou perguntando qual vale.
            models.UniqueConstraint(
                fields=["cliente", "tipo", "competencia"], name="guia_unica_por_competencia"
            )
        ]
        indexes = [models.Index(fields=["cliente", "situacao", "vencimento"], name="guia_fila")]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.competencia} — {self.cliente}"

    @property
    def vencida(self) -> bool:
        return self.situacao == self.Situacao.ABERTA and self.vencimento < timezone.localdate()

    @property
    def dias_para_vencer(self) -> int:
        return (self.vencimento - timezone.localdate()).days


class Obrigacao(models.Model):
    """Uma declaração acessória e o estado dela naquela competência.

    Diferente da guia, aqui não há valor a pagar — há prazo a cumprir. O que o
    contador pergunta é "entreguei?", e o que o cliente pergunta é "falta algo
    de mim?".
    """

    class Tipo(models.TextChoices):
        DCTFWEB = "dctfweb", "DCTFWeb"
        EFD = "efd", "EFD-Contribuições"
        ESOCIAL = "esocial", "eSocial"
        SPED = "sped", "SPED Fiscal"
        DEFIS = "defis", "DEFIS"

    class Situacao(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        ENVIADA = "enviada", "Enviada"
        DISPENSADA = "dispensada", "Dispensada"

    cliente = models.ForeignKey(
        "clients.Cliente", on_delete=models.CASCADE, related_name="obrigacoes"
    )
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    competencia = Competencia()
    prazo = models.DateField()
    situacao = models.CharField(
        max_length=12, choices=Situacao.choices, default=Situacao.PENDENTE
    )
    # Preenchido quando o cliente precisa fazer algo antes de o escritório
    # entregar. É a diferença entre "estamos cuidando" e "estou esperando você".
    pendente_com_o_cliente = models.BooleanField(default=False)
    observacao = models.CharField(max_length=200, blank=True, default="")

    enviada_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "obrigação"
        verbose_name_plural = "obrigações"
        ordering = ["-competencia", "tipo"]
        constraints = [
            models.UniqueConstraint(
                fields=["cliente", "tipo", "competencia"],
                name="obrigacao_unica_por_competencia",
            )
        ]
        indexes = [models.Index(fields=["cliente", "situacao", "prazo"], name="obrigacao_fila")]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.competencia} — {self.cliente}"

    @property
    def atrasada(self) -> bool:
        return self.situacao == self.Situacao.PENDENTE and self.prazo < timezone.localdate()


class Certidao(models.Model):
    """Certidão negativa e sua validade.

    Vale por si: certidão vencida trava licitação, financiamento e renovação de
    contrato — e o cliente costuma descobrir no dia em que precisa dela.
    """

    class Tipo(models.TextChoices):
        FEDERAL = "federal", "Federal (RFB/PGFN)"
        ESTADUAL = "estadual", "Estadual"
        MUNICIPAL = "municipal", "Municipal"
        FGTS = "fgts", "FGTS (CRF)"
        TRABALHISTA = "trabalhista", "Trabalhista (CNDT)"

    class Situacao(models.TextChoices):
        NEGATIVA = "negativa", "Negativa"
        POSITIVA_COM_EFEITO = "positiva_efeito", "Positiva com efeito de negativa"
        POSITIVA = "positiva", "Positiva (há débito)"

    cliente = models.ForeignKey(
        "clients.Cliente", on_delete=models.CASCADE, related_name="certidoes"
    )
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    situacao = models.CharField(max_length=20, choices=Situacao.choices)
    emitida_em = models.DateField()
    valida_ate = models.DateField()
    url_documento = models.URLField(blank=True, default="")
    observacao = models.CharField(max_length=200, blank=True, default="")

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "certidão"
        verbose_name_plural = "certidões"
        ordering = ["tipo", "-valida_ate"]
        indexes = [models.Index(fields=["cliente", "valida_ate"], name="certidao_validade")]

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.cliente} (até {self.valida_ate})"

    @property
    def vencida(self) -> bool:
        return self.valida_ate < timezone.localdate()

    @property
    def dias_para_vencer(self) -> int:
        return (self.valida_ate - timezone.localdate()).days


class Folha(models.Model):
    """Resumo da folha de pagamento fechada na competência.

    Só o resumo, de propósito: salário individual é dado sensível de terceiro
    (o funcionário), que não é titular da relação com o Magic BI. O que a
    conversa responde é "a folha fechou? quanto deu? quantas pessoas?".
    """

    cliente = models.ForeignKey(
        "clients.Cliente", on_delete=models.CASCADE, related_name="folhas"
    )
    competencia = Competencia()
    funcionarios = models.PositiveIntegerField(default=0)
    total_bruto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_encargos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fechada_em = models.DateField(null=True, blank=True)
    observacao = models.CharField(max_length=200, blank=True, default="")

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "folha"
        verbose_name_plural = "folhas"
        ordering = ["-competencia"]
        constraints = [
            models.UniqueConstraint(
                fields=["cliente", "competencia"], name="folha_unica_por_competencia"
            )
        ]

    def __str__(self):
        return f"Folha {self.competencia} — {self.cliente}"

    @property
    def fechada(self) -> bool:
        return self.fechada_em is not None
