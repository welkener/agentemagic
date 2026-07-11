"""
Intenção fiscal + máquina de estados da emissão (seção 5.1 da arquitetura):

    RECEBIDO → VALIDANDO → AGUARDANDO_APROVACAO → EMITINDO → CONCLUIDO
                    │                                  │
                    └── REJEITADO                      └── REJEITADO
    (CANCELADO pode ocorrer antes da emissão começar)

Toda transição é auditada (trilha append-only) — nada muda de estado em
silêncio.
"""
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
    estado = models.CharField(
        max_length=24, choices=Estado.choices, default=Estado.RECEBIDO
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "intenção fiscal"
        verbose_name_plural = "intenções fiscais"

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

    def __str__(self):
        return f"Intenção {self.id} ({self.tipo_acao}) — {self.estado}"
