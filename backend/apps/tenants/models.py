"""
Escritório (tenant) — a **raiz de multi-tenancy** do Magic BI.

Magic BI é SaaS multi-tenant (`docs/magicbi-marca-e-nomes.md` §1: a Magic BI é
dona da plataforma, distribuída por vários escritórios contábeis parceiros; a
Rotina é o primeiro, não o único). Até 26/jul/2026 isto só existia na doc: o
`Escritorio` era uma tabela de logo e cores, nada apontava pra ele, e um
contador logado via os clientes de todos os outros. Agora ele é dono de:

- **clientes** (`apps.clients.Cliente.escritorio`) e, por tabela, de tudo que
  pendura no cliente — perfil, credenciais, intenções, auditoria, sessões;
- **canal WhatsApp próprio** (decisão 26/jul/2026: número por escritório).
  O número que RECEBE a mensagem é o que identifica o tenant no webhook, então
  o isolamento não depende de o remetente estar cadastrado corretamente;
- **membros** (`MembroEscritorio`) — quem loga no admin e o que enxerga.

Um contador sem `MembroEscritorio` não vê nada (não "vê tudo"): o padrão
seguro é negar. Só `is_superuser` (equipe Magic BI) enxerga a plataforma
inteira — ver `apps/tenants/escopo.py`.

**Permissões x escopo são coisas separadas, de propósito.** O `Group` do Django
(um por escritório, `grupo_do_escritorio`) decide *o que* a pessoa pode fazer —
quais models, quais ações. O `MembroEscritorio` decide *sobre quais linhas* —
as do escritório dela. Um não substitui o outro: sem o grupo a pessoa não tem
permissão de nada; sem o vínculo ela teria permissão sobre nada.
"""
from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models

from apps.credentials.crypto import CampoTextoCifrado, cifrar, decifrar


class Escritorio(models.Model):
    nome = models.CharField(max_length=120, help_text='Ex.: "Rotina Contábil".')
    slug = models.SlugField(
        max_length=60,
        unique=True,
        help_text="Identificador estável (aparece em log/auditoria, não muda quando o nome muda).",
    )
    logo = models.ImageField(upload_to="logos/", blank=True, null=True)
    cor_primaria = models.CharField(
        max_length=7, default="#1a1a2e", help_text="Hex, ex.: #000066. Usada no cabeçalho do painel."
    )
    cor_acento = models.CharField(
        max_length=7, default="#5B67C9", help_text="Hex — links e destaques do painel."
    )
    ativo = models.BooleanField(
        default=True,
        help_text="Escritório habilitado. Inativo não recebe mensagem nem aparece no roteamento.",
    )

    # --- Canal WhatsApp próprio (Cloud API oficial) ------------------------
    # Um número por escritório: o `phone_number_id` que recebeu a mensagem é o
    # que resolve o tenant no webhook (apps/channel_whatsapp/views.py).
    whatsapp_phone_number_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text=(
            "ID do número no WhatsApp Cloud API (Meta) deste escritório. "
            "É por ele que o webhook descobre de quem é a mensagem recebida."
        ),
    )
    whatsapp_token_cifrado = CampoTextoCifrado(
        blank=True,
        null=True,
        help_text="Token permanente da Cloud API do escritório, cifrado em repouso.",
    )

    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "escritório (tenant)"
        verbose_name_plural = "escritórios (tenants)"
        constraints = [
            # Dois escritórios não podem dividir o mesmo número — isso tornaria
            # o roteamento do webhook ambíguo. Vazio é permitido em vários
            # (escritório que ainda não conectou o WhatsApp).
            models.UniqueConstraint(
                fields=["whatsapp_phone_number_id"],
                condition=~models.Q(whatsapp_phone_number_id=""),
                name="escritorio_phone_number_id_unico",
            ),
        ]

    def __str__(self):
        return self.nome

    @property
    def whatsapp_token(self) -> str:
        dado = self.whatsapp_token_cifrado
        if not dado:
            return ""
        return dado if isinstance(dado, str) else decifrar(bytes(dado))

    @whatsapp_token.setter
    def whatsapp_token(self, texto_puro: str) -> None:
        self.whatsapp_token_cifrado = cifrar(texto_puro) if texto_puro else None

    @property
    def canal_whatsapp_configurado(self) -> bool:
        return bool(self.whatsapp_phone_number_id and self.whatsapp_token)


class MembroEscritorio(models.Model):
    """Vincula um `auth.User` (contador) ao escritório dele.

    Sem este vínculo, um usuário staff não-superuser não enxerga nenhum dado no
    admin — ver `apps/tenants/escopo.py`. É o que impede o contador do escritório
    A de abrir a carteira de clientes do escritório B.
    """

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="membro_escritorio"
    )
    escritorio = models.ForeignKey(Escritorio, on_delete=models.CASCADE, related_name="membros")
    responsavel = models.BooleanField(
        default=False,
        help_text=(
            "Pode convidar e remover colegas DESTE escritório. Não é um papel de "
            "permissão (isso é o Grupo do Django) — é só a capacidade de gerir a "
            "própria equipe."
        ),
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "membro de escritório"
        verbose_name_plural = "membros de escritório"

    def __str__(self):
        papel = " (responsável)" if self.responsavel else ""
        return f"{self.usuario} — {self.escritorio}{papel}"


def grupo_do_escritorio(escritorio: "Escritorio") -> Group:
    """Grupo do Django que carrega as permissões deste escritório.

    Um grupo por escritório (`escritorio:<slug>`) em vez de papéis fixos no
    código: a equipe Magic BI monta as permissões caso a caso, e o que muda de
    um parceiro pro outro fica no banco, não num `if papel ==` espalhado.

    O grupo NÃO decide isolamento — quem faz isso é `MembroEscritorio` +
    `escopo.py`. Dar permissão demais aqui amplia o que a pessoa faz dentro do
    escritório dela, nunca a alcança o escritório do vizinho.
    """
    grupo, _ = Group.objects.get_or_create(name=f"escritorio:{escritorio.slug}")
    return grupo


def escritorio_por_phone_number_id(phone_number_id: str) -> "Escritorio | None":
    """Resolve o tenant pelo número que RECEBEU a mensagem (canal oficial).

    Compat com a instalação single-tenant que já está no ar: enquanto o número
    mora no `.env` e nenhum `Escritorio` tem `whatsapp_phone_number_id`, cair
    no único escritório ativo é seguro — com um tenant só não há para onde
    vazar. A partir do segundo escritório o fallback deixa de valer e a
    mensagem sem número casado é recusada, em vez de ir pro tenant errado.
    """
    if phone_number_id:
        escritorio = Escritorio.objects.filter(
            whatsapp_phone_number_id=phone_number_id, ativo=True
        ).first()
        if escritorio is not None:
            return escritorio

    ativos = list(Escritorio.objects.filter(ativo=True)[:2])
    return ativos[0] if len(ativos) == 1 else None


def escritorio_ativo() -> "Escritorio | None":
    """Único escritório ativo, se houver só um.

    Usado como branding de fallback quando não há usuário logado (ex.: tela de
    login). Com mais de um tenant devolve None de propósito — mostrar a marca
    de um escritório qualquer pro contador de outro seria pior que a genérica.
    """
    ativos = list(Escritorio.objects.filter(ativo=True)[:2])
    return ativos[0] if len(ativos) == 1 else None
