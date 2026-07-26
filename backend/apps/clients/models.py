"""Modelos de cliente e perfil (um perfil por cliente — princípio da arquitetura)."""
from django.db import models


class Cliente(models.Model):
    """Empresa atendida por um escritório contábil parceiro (MEI/ME/EPP).

    `escritorio` é a raiz de multi-tenancy: tudo que pendura no cliente
    (perfil, credenciais, intenções, auditoria, sessão) herda o tenant por
    aqui. Ver `apps/tenants/models.py`.
    """

    escritorio = models.ForeignKey(
        "tenants.Escritorio",
        on_delete=models.PROTECT,  # nunca apagar escritório levando junto dado fiscal
        related_name="clientes",
        help_text="Escritório contábil dono desta carteira.",
    )
    cnpj = models.CharField(max_length=14)
    nome = models.CharField(max_length=200)
    telefone_whatsapp = models.CharField(
        max_length=20,
        help_text="Número no formato internacional, ex.: 5511999998888",
    )
    email_contato = models.EmailField(
        blank=True,
        default="",
        help_text=(
            "E-mail cadastrado na Receita/ERP — canal do Magic Link e do código 2FA. "
            "Nunca reaproveitar o WhatsApp como canal desses segredos (2º fator de canal)."
        ),
    )
    cnae_padrao = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text=(
            "CNAE do serviço prestado, cadastrado pelo contador — nunca inferido "
            "pelo LLM (guard determinístico da emissão fiscal). ⚠ NÃO é o que vai "
            "na DPS: a NFS-e Nacional usa `codigo_tributacao_nacional` (cTribNac). "
            "CNAE é classificação de atividade econômica, serve pro cadastro."
        ),
    )

    # --- Campos exigidos pela DPS da NFS-e Nacional ------------------------
    # Confirmados contra o XSD oficial (nfelib.nfse.bindings.v1_0), não
    # inferidos: cada um é `required` no schema e tem pattern próprio. Sem eles
    # não existe DPS válida — ver apps/fiscal/dps.py::montar_dps, que recusa a
    # emissão listando exatamente o que falta em vez de mandar XML inválido.
    codigo_municipio_ibge = models.CharField(
        max_length=7,
        blank=True,
        default="",
        help_text="Código IBGE do município (7 dígitos) — `cLocEmi`/`cLocPrestacao` da DPS.",
    )
    inscricao_municipal = models.CharField(
        max_length=15,
        blank=True,
        default="",
        help_text="Inscrição municipal do prestador (`prest.IM`). Opcional em alguns municípios.",
    )
    codigo_tributacao_nacional = models.CharField(
        max_length=6,
        blank=True,
        default="",
        help_text=(
            "`cTribNac` — 6 dígitos da lista nacional de serviços (LC 116). "
            "É ESTE que vai na nota, não o CNAE. Cadastrado pelo contador, "
            "nunca inferido pelo LLM."
        ),
    )
    regime_tributario = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="`prest.regTrib` — regime de tributação do prestador (tabela da NT vigente).",
    )
    serie_dps = models.CharField(
        max_length=5,
        blank=True,
        default="1",
        help_text="Série da DPS (`serie`). Numeração sequencial é por prestador+série.",
    )
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "cliente"
        constraints = [
            # Unicidade por ESCRITÓRIO, não global: dois escritórios podem ter
            # o mesmo CNPJ/telefone na carteira (cliente que troca de contador,
            # ou que tem contador fiscal e trabalhista separados). O que não
            # pode é o mesmo escritório duplicar o cliente.
            models.UniqueConstraint(
                fields=["escritorio", "cnpj"], name="cliente_cnpj_unico_por_escritorio"
            ),
            models.UniqueConstraint(
                fields=["escritorio", "telefone_whatsapp"],
                name="cliente_telefone_unico_por_escritorio",
            ),
        ]

    def __str__(self):
        return f"{self.nome} ({self.cnpj})"


class Perfil(models.Model):
    """Perfil de atendimento do cliente: persona, ferramentas e teto de tier.

    O motor de governança usa `tier_maximo` para recusar intenções acima do
    permitido (no piloto, ERP fica travado em Tier 0–1).
    """

    cliente = models.OneToOneField(
        Cliente, on_delete=models.CASCADE, related_name="perfil"
    )
    persona = models.CharField(max_length=40, default="lumen")
    ferramentas_habilitadas = models.JSONField(
        default=list,
        help_text='Adaptadores/ferramentas ativos, ex.: ["erp_mock", "nfse_mock"]',
    )
    tier_maximo = models.PositiveSmallIntegerField(default=1)
    valor_2fa_acima_de = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Emissões acima deste valor exigem código de 2FA por e-mail "
            "(apps/security). Vazio = 2FA desligado para este cliente."
        ),
    )

    class Meta:
        verbose_name = "perfil"
        verbose_name_plural = "perfis"

    def __str__(self):
        return f"Perfil de {self.cliente} (tier máx. {self.tier_maximo})"
