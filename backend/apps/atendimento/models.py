"""
Chamados e pedidos de atendimento vindos do WhatsApp.

**Por que uma tabela e não só uma linha de auditoria.** As duas ferramentas do
Sprint 2 que escrevem sem tocar ERP — `abrir_chamado` e `agendar_atendimento` —
respondem ao cliente "registrei seu pedido". Se o registro fosse só um evento na
trilha, a frase seria falsa na parte que importa: ninguém trabalha a partir da
trilha. Chamado que ninguém lê é pior que chamado nenhum, porque o cliente
para de cobrar achando que está resolvido.

Por isso a fila aparece no Grimório, na tela Hoje, junto do resto do que exige o
contador agora. É a mesma regra das outras telas: o que o sistema promete ao
cliente tem que ter dono do outro lado.

**Estas são as duas únicas tools de escrita que não passam por confirmação em
duas etapas**, e é deliberado: abrir chamado não tem efeito fiscal, não gasta
dinheiro do cliente e é trivialmente reversível pelo contador. Exigir "confirma?"
para registrar um pedido de ajuda só somaria uma volta de conversa a quem já
está com problema.

**LGPD.** `descricao` guarda texto escrito pelo titular. Diferente da trilha de
auditoria — que é append-only e por isso usa crypto-shredding —, esta tabela é
mutável, então a eliminação a pedido do titular simplesmente apaga o texto e
mantém o registro de que houve um chamado (ver
`apps/audit/conteudo.eliminar_conteudo_do_titular`).
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Solicitacao(models.Model):
    """Um pedido do cliente para o escritório, aberto pela conversa."""

    class Tipo(models.TextChoices):
        CHAMADO = "chamado", "Chamado"
        ATENDIMENTO = "atendimento", "Pedido de atendimento"

    class Estado(models.TextChoices):
        ABERTA = "aberta", "Aberta"
        EM_ANDAMENTO = "em_andamento", "Em andamento"
        RESOLVIDA = "resolvida", "Resolvida"

    cliente = models.ForeignKey(
        "clients.Cliente", on_delete=models.CASCADE, related_name="solicitacoes"
    )
    # Quem pediu, sob DEC-03: uma empresa tem vários autorizados, e "o sócio
    # pediu" e "o financeiro pediu" levam o contador a respostas diferentes.
    # SET_NULL porque desligar uma pessoa não pode apagar o histórico da empresa.
    usuario = models.ForeignKey(
        "clients.Usuario",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="solicitacoes",
    )

    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.CHAMADO)
    assunto = models.CharField(
        max_length=120,
        help_text="Resumo curto, montado pelo núcleo — nunca inventado pelo modelo.",
    )
    descricao = models.TextField(
        blank=True, default="", help_text="O que o cliente escreveu, nas palavras dele."
    )
    # Preenchido só em `agendar_atendimento`. Data preferida, não compromisso
    # firmado: quem confirma horário é o escritório, e prometer agenda que o
    # contador não viu seria assumir disponibilidade que o sistema não conhece.
    preferencia_data = models.DateField(null=True, blank=True)

    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.ABERTA)
    canal = models.CharField(max_length=20, default="whatsapp")

    protocolo = models.CharField(
        max_length=24,
        unique=True,
        help_text="O que o cliente cita quando cobra. Gerado no núcleo, estável.",
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    resolvido_em = models.DateTimeField(null=True, blank=True)
    # Quem fechou. Num escritório com quatro contadores, é a primeira pergunta
    # quando o cliente diz que ninguém respondeu — e sem o campo a resposta só
    # existiria na trilha de auditoria, que não é onde se olha no dia a dia.
    # SET_NULL: desligar um funcionário não pode apagar o histórico do chamado.
    resolvido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="solicitacoes_resolvidas",
    )

    class Meta:
        verbose_name = "solicitação"
        verbose_name_plural = "solicitações"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["cliente", "estado"], name="solicitacao_cliente_estado"),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.protocolo} — {self.assunto}"

    @property
    def aberta(self) -> bool:
        return self.estado != self.Estado.RESOLVIDA

    def resolver(self, por=None) -> None:
        """Fecha o chamado. `por` é quem clicou — o contador logado.

        Continua aceitando `None` porque a ação em massa do admin e os testes
        fecham sem pessoa identificada; o que não pode é a tela do Grimório
        deixar de informar, e é isso que `tests/test_grimorio_acoes.py` cobra.
        """
        self.estado = self.Estado.RESOLVIDA
        self.resolvido_em = timezone.now()
        self.resolvido_por = por
        self.save(
            update_fields=["estado", "resolvido_em", "resolvido_por", "atualizado_em"]
        )
