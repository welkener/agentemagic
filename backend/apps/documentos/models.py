"""
Documentos que o cliente manda pela conversa — nota, boleto, extrato, contrato.

**Quem sai da fila sem humano, e por quê.** O padrão continua sendo
`AGUARDANDO_REVISAO` com `confianca` zero — é onde cai todo documento cuja
leitura ninguém consegue provar. Sobem acima do limiar apenas os que trazem
prova junto: XML assinado pela SEFAZ, chave de acesso ou linha digitável, todos
conferidos por dígito verificador (ver `extracao.py`). Não é "o sistema achou
provável"; é "a conta fecha".

A ordem em que isso foi construído é a decisão que este modelo carrega: a fila
de revisão veio **antes** da extração. Com ela de pé, cada degrau de leitura que
entra apenas encurta a fila. Na ordem oposta, o primeiro lançamento automático
aconteceria antes de existir quem conferisse — que é o que o gate do Sprint 4
proíbe: *documento com baixa confiança nunca vira lançamento automático*.

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
        # Ainda não classificado — o estado de tudo que chegou sem prova junto.
        DESCONHECIDO = "desconhecido", "A classificar"
        NOTA_ENTRADA = "nota_entrada", "Nota de entrada"
        # Entrada e saída são a diferença entre despesa e receita, e a chave de
        # acesso responde qual é sem ninguém opinar: quem emitiu está dentro dos
        # 44 dígitos. Se foi o próprio cliente, ele vendeu.
        NOTA_SAIDA = "nota_saida", "Nota de saída"
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

    # --- o que a leitura provou (ver documentos/extracao.py) --------------
    dados_extraidos = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Chave de acesso, CNPJ emitente, competência, linha digitável, "
            "valor, vencimento — mais o método que leu. Vazio quando ninguém "
            "conseguiu provar nada sobre o arquivo."
        ),
    )
    confianca = models.PositiveSmallIntegerField(
        default=0,
        help_text=(
            "0 a 100. Só passa do limiar o que se confere sozinho: XML assinado "
            "ou número com dígito verificador. Abaixo dele o documento espera "
            "humano — é o gate do Sprint 4."
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

    @property
    def classificado_por_maquina(self) -> bool:
        """Classificado sem ninguém clicar — e portanto passível de contestação.

        Não é um estado à parte: é `CLASSIFICADO` com `revisado_por` vazio. A
        distinção existe para que o contador consiga achar exatamente o que saiu
        da fila sozinho. Leitura automática que ninguém consegue listar depois é
        leitura que ninguém consegue auditar.
        """
        return self.situacao == self.Situacao.CLASSIFICADO and self.revisado_por_id is None

    def aplicar_extracao(self, extracao) -> None:
        """Grava o que a leitura provou — e só tira da fila quando ela se prova.

        **O gate do Sprint 4 está nesta função, em uma condição.** `dispensa_revisao`
        é o único caminho para `CLASSIFICADO` sem humano, e ele exige confiança
        acima do limiar, que por sua vez só é alcançada por assinatura da SEFAZ ou
        por dígito verificador. Não há parâmetro para forçar, nem argumento de
        conveniência: quem quiser classificar sem prova chama `classificar()` e
        assina com o próprio usuário.

        O `tipo` também só é gravado dentro dessa condição. Preencher o campo com
        um palpite de baixa confiança pareceria ajuda e seria armadilha: o
        contador que revisa cem documentos aceita o que já vem preenchido, e um
        chute exibido em campo de formulário é um chute vestido de fato.
        """
        campos = ["dados_extraidos", "confianca"]
        self.dados_extraidos = extracao.como_dados()
        self.confianca = extracao.confianca

        if extracao.dispensa_revisao and extracao.tipo in self.Tipo.values:
            self.tipo = extracao.tipo
            self.situacao = self.Situacao.CLASSIFICADO
            self.revisado_em = timezone.now()
            campos += ["tipo", "situacao", "revisado_em"]

        self.save(update_fields=campos)

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
