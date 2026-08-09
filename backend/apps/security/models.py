"""
Identidade de sessão (`docs/magicbi-seguranca-sessao.md`): vínculo `wa_id ↔
CNPJ`, tokens de Magic Link de uso único e códigos de 2FA para ações acima de
threshold. Camada de AUTENTICAÇÃO (quem está falando) — não confundir com
`apps.governance` (AUTORIZAÇÃO — o que essa intenção pode fazer).
"""
from django.db import models

from apps.clients.models import Cliente, Usuario


class SessaoWhatsapp(models.Model):
    """Estado atual do vínculo `wa_id ↔ CNPJ` de um cliente.

    Um registro por cliente (não histórico) — o histórico de ativação/
    expiração/bloqueio já fica na trilha de auditoria via `apps.audit`.
    """

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        ATIVA = "ativa", "Ativa"
        EXPIRADA = "expirada", "Expirada"
        BLOQUEADA = "bloqueada", "Bloqueada"

    cliente = models.OneToOneField(
        Cliente, on_delete=models.CASCADE, related_name="sessao_whatsapp"
    )
    wa_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Número validado na última ativação — comparado ao telefone atual do cliente a cada mensagem.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    validado_em = models.DateTimeField(null=True, blank=True)
    expira_em = models.DateTimeField(null=True, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "sessão WhatsApp"
        verbose_name_plural = "sessões WhatsApp"

    def __str__(self):
        return f"Sessão de {self.cliente} ({self.status})"


class EmpresaEmFoco(models.Model):
    """De qual empresa este telefone está falando agora (DEC-03).

    Só existe quando o número fala por mais de uma empresa. `SessaoWhatsapp`
    não serve para guardar isso: ela é um-para-um com o **cliente**, e a
    pergunta aqui é anterior — qual cliente. Daí um registro próprio, chaveado
    pelo usuário.

    `cliente` nulo é estado de propósito, e é o que distingue "já perguntei,
    estou esperando o número" de "ninguém perguntou nada ainda". Sem essa
    diferença, um "2" solto vindo de quem não recebeu menu nenhum seria lido
    como escolha.

    **Por que expira.** Falar da empresa errada aqui não é confusão de tela: é
    nota fiscal no CNPJ errado. Se a conversa esfriou, perguntar de novo custa
    uma mensagem; assumir custa uma retificação.
    """

    usuario = models.OneToOneField(Usuario, on_delete=models.CASCADE, related_name="foco")
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="foco_de_usuarios",
        help_text="Empresa escolhida. Vazio = menu enviado, resposta pendente.",
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "empresa em foco"
        verbose_name_plural = "empresas em foco"

    def __str__(self):
        alvo = self.cliente or "aguardando escolha"
        return f"{self.usuario} → {alvo}"


class TokenMagicLink(models.Model):
    """Registro de controle de um token de Magic Link (o JWT em si não é persistido).

    Só o `jti` fica no banco — permite marcar uso único e detectar replay sem
    guardar o segredo assinado.
    """

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="magic_links")
    wa_id = models.CharField(max_length=32)
    jti = models.CharField(max_length=64, unique=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    usado_em = models.DateTimeField(null=True, blank=True)
    expira_em = models.DateTimeField()

    class Meta:
        verbose_name = "token de Magic Link"
        verbose_name_plural = "tokens de Magic Link"

    def __str__(self):
        status = "usado" if self.usado_em else "pendente"
        return f"Magic Link de {self.cliente} ({status})"


class Codigo2FA(models.Model):
    """Código avulso (6 dígitos) para ações acima do threshold do perfil.

    Não é TOTP — é código de uso único gerado sob demanda e enviado por
    e-mail (evita SIM swap de SMS, ver `docs/magicbi-seguranca-sessao.md` §7).
    Só o hash do código fica salvo.
    """

    LIMITE_TENTATIVAS = 3

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="codigos_2fa")
    intencao = models.ForeignKey(
        "agente_nf.Intencao", on_delete=models.CASCADE, related_name="codigos_2fa"
    )
    codigo_hash = models.CharField(max_length=64)
    tentativas = models.PositiveSmallIntegerField(default=0)
    criado_em = models.DateTimeField(auto_now_add=True)
    expira_em = models.DateTimeField()
    usado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "código 2FA"
        verbose_name_plural = "códigos 2FA"

    def __str__(self):
        return f"Código 2FA de {self.cliente} p/ intenção {self.intencao_id}"
