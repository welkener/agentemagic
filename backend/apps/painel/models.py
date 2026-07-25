"""
Escritório (tenant) — Magic BI é **SaaS multi-tenant** (docs/magicbi-marca-e-nomes.md
§1: Magic BI é dona da plataforma, distribuída por vários escritórios
contábeis parceiros, a Rotina é o primeiro, não o único). Nome/logo/cores do
Grimório nunca podem ficar hardcoded num escritório específico no template —
isto é o que faz o painel branding-able por escritório.

Sem `Escritorio` cadastrado, o dashboard cai num branding genérico "Magic BI"
(a marca da plataforma em si, não a de nenhum escritório) — nunca falha, nunca
mostra uma marca de terceiro por padrão.
"""
from django.db import models


class Escritorio(models.Model):
    nome = models.CharField(max_length=120, help_text='Ex.: "Rotina Contábil".')
    logo = models.ImageField(upload_to="logos/", blank=True, null=True)
    cor_primaria = models.CharField(
        max_length=7, default="#1a1a2e", help_text="Hex, ex.: #000066. Usada no cabeçalho do painel."
    )
    cor_acento = models.CharField(
        max_length=7, default="#5B67C9", help_text="Hex — links e destaques do painel."
    )
    ativo = models.BooleanField(
        default=False, help_text="Só o escritório ativo mais recente aparece no painel."
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "escritório (tenant)"
        verbose_name_plural = "escritórios (tenants)"

    def __str__(self):
        return self.nome


def escritorio_ativo() -> "Escritorio | None":
    return Escritorio.objects.filter(ativo=True).order_by("-atualizado_em").first()
