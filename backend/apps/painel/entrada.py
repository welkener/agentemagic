"""
A porta da frente do Grimório.

**O que estava errado.** `/entrar/` apontava direto para a `LoginView` do
django-sesame, que só sabe uma coisa: validar um token na query string. Sem
token ela responde **403**. Ou seja: o contador que digitasse o endereço do
painel, ou cuja sessão expirasse, batia num 403 sem explicação e sem caminho —
e a única saída era um link por e-mail, que depende de um SMTP que ainda não
está configurado. Na prática o produto ficava sem porta de entrada, e a única
forma de trabalhar era pelo `/admin/login/`: exatamente o backoffice de
programador que o Grimório existe para o contador não precisar abrir (DEC-12).

**Duas entradas, e as duas já existiam.** O Magic Link continua igual — mesmo
token, mesmo TTL, mesma `LoginView` do sesame, que passa a ser chamada só
quando há token. A senha não é novidade nem superfície nova: o `ModelBackend`
já estava ligado e esses mesmos usuários já entravam com ela no `/admin/`. O
que muda é onde se digita, e por isso o ganho é de produto, não de permissão.

**Contra força bruta.** Formulário de senha exposto na internet sem limite é
convite. O contador tem um número pequeno de tentativas por janela, contadas
por usuário **e** por IP: sem o IP, quem descobre um username tranca o dono
fora de propósito; sem o username, um escritório inteiro atrás do mesmo NAT
tranca junto.

O limite falha **aberto**: se o Redis cair, ninguém é barrado. É escolha
consciente — um contador sem acesso ao painel em dia de fechamento é dano certo
e imediato, e força bruta sem contador é dano possível. Entre os dois, o
alarme na trilha vale mais que a tranca.
"""
from __future__ import annotations

import structlog
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.core.cache import cache
from django.core.mail import send_mail
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import TemplateView
from sesame.utils import get_query_string
from sesame.views import LoginView as LoginPorToken

from apps.audit.services import registrar
from apps.painel import branding

logger = structlog.get_logger(__name__)

TENTATIVAS_POR_JANELA = 8
JANELA_SEGUNDOS = 15 * 60

DESTINO_PADRAO = "/grimorio/"


def _chaves_de_tentativa(request, usuario: str) -> list[str]:
    ip = (
        (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
        or "sem-ip"
    )
    return [f"entrar:u:{usuario.lower()[:80]}", f"entrar:ip:{ip}"]


def _excedeu_tentativas(request, usuario: str) -> bool:
    try:
        return any(cache.get(chave, 0) >= TENTATIVAS_POR_JANELA
                   for chave in _chaves_de_tentativa(request, usuario))
    except Exception as erro:  # noqa: BLE001 — cache fora do ar não tranca ninguém
        logger.warning("limite_de_login_indisponivel", erro=str(erro))
        return False


def _contar_falha(request, usuario: str) -> None:
    for chave in _chaves_de_tentativa(request, usuario):
        try:
            # `add` só cria se não existir — é o que dá à janela um começo fixo,
            # em vez de ela se renovar a cada erro e nunca expirar.
            cache.add(chave, 0, JANELA_SEGUNDOS)
            cache.incr(chave)
        except Exception as erro:  # noqa: BLE001
            logger.warning("limite_de_login_nao_contou", erro=str(erro))


def _limpar_tentativas(request, usuario: str) -> None:
    for chave in _chaves_de_tentativa(request, usuario):
        try:
            cache.delete(chave)
        except Exception:  # noqa: BLE001, S110 — contador é acessório
            pass


def _destino_seguro(request, bruto: str | None) -> str:
    """`?next=` sem validação é redirecionamento aberto — o vetor clássico de
    phishing: link que passa pelo domínio legítimo e joga em outro."""
    if bruto and url_has_allowed_host_and_scheme(
        bruto, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return bruto
    return DESTINO_PADRAO


class EntrarView(TemplateView):
    """Página de entrada. Com token, delega ao sesame; sem token, atende gente.

    A delegação é literal — a `LoginView` do sesame é instanciada e chamada,
    não reimplementada. Copiar a validação de token para cá seria criar uma
    segunda implementação de autenticação para manter em dia com a biblioteca.
    """

    template_name = "grimorio/entrar.html"

    def get(self, request, *args, **kwargs):
        if request.GET.get(getattr(settings, "SESAME_TOKEN_NAME", "sesame")):
            return LoginPorToken.as_view()(request, *args, **kwargs)
        if request.user.is_authenticated:
            return HttpResponseRedirect(_destino_seguro(request, request.GET.get("next")))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["marca"] = branding.marca_do_usuario(self.request.user)
        contexto["next"] = self.request.GET.get("next", "")
        # Sem SMTP o botão de Magic Link não some: some a promessa. Um botão que
        # responde "enviei" sem enviar é pior que um botão desabilitado, porque
        # o contador fica esperando um e-mail que não vem.
        contexto["email_configurado"] = bool(getattr(settings, "EMAIL_HOST", ""))
        return contexto

    def post(self, request, *args, **kwargs):
        if request.POST.get("acao") == "link":
            return self._mandar_link(request)
        return self._entrar_com_senha(request)

    # -- senha ------------------------------------------------------------
    def _entrar_com_senha(self, request):
        identificacao = (request.POST.get("usuario") or "").strip()
        senha = request.POST.get("senha") or ""

        if _excedeu_tentativas(request, identificacao):
            registrar(
                "login_painel_bloqueado",
                {"usuario": identificacao, "motivo": "limite de tentativas"},
            )
            messages.error(
                request,
                "Tentativas demais. Espere 15 minutos ou entre pelo link no e-mail.",
            )
            return self._de_volta(request)

        usuario = authenticate(request, username=identificacao, password=senha)
        if usuario is None:
            _contar_falha(request, identificacao)
            registrar("login_painel_negado", {"usuario": identificacao})
            # Mensagem única para usuário inexistente e senha errada: dizer qual
            # dos dois falhou entrega ao atacante metade da resposta de graça.
            messages.error(request, "Usuário ou senha não conferem.")
            return self._de_volta(request)

        if not usuario.is_staff:
            registrar("login_painel_negado", {"usuario": identificacao, "motivo": "sem staff"})
            messages.error(request, "Esta conta não tem acesso ao Grimório.")
            return self._de_volta(request)

        _limpar_tentativas(request, identificacao)
        login(request, usuario)
        registrar("login_painel", {"usuario": usuario.get_username(), "por": "senha"})
        return HttpResponseRedirect(_destino_seguro(request, request.POST.get("next")))

    # -- magic link -------------------------------------------------------
    def _mandar_link(self, request):
        from django.contrib.auth import get_user_model

        identificacao = (request.POST.get("usuario") or "").strip()
        if not getattr(settings, "EMAIL_HOST", ""):
            messages.error(
                request,
                "O envio de e-mail ainda não está configurado neste servidor. "
                "Entre com sua senha por enquanto.",
            )
            return self._de_volta(request)

        busca = {"email__iexact": identificacao} if "@" in identificacao else {
            "username__iexact": identificacao
        }
        usuario = get_user_model().objects.filter(**busca, is_staff=True).first()

        # A resposta é a mesma exista ou não a conta: variar aqui transformaria a
        # tela num verificador de quem trabalha no escritório.
        if usuario is not None and usuario.email:
            base = getattr(settings, "PAINEL_BASE_URL", "").rstrip("/")
            link = f"{base}{reverse('painel_login')}{get_query_string(usuario)}"
            minutos = settings.SESAME_MAX_AGE // 60
            try:
                send_mail(
                    subject="Magic BI — acesso ao Grimório",
                    message=(
                        f"Olá, {usuario.get_username()}!\n\n"
                        f"Entre pelo link abaixo (expira em {minutos} minutos):\n{link}\n\n"
                        "Se você não pediu isso, ignore este e-mail."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[usuario.email],
                    fail_silently=False,
                )
                registrar("login_painel_link_enviado", {"usuario": usuario.get_username()})
            except Exception as erro:  # noqa: BLE001 — SMTP fora do ar não é 500
                logger.warning("magic_link_nao_enviou", erro=str(erro))

        messages.success(
            request,
            "Se essa conta existir, o link chega em instantes. Confira o e-mail.",
        )
        return self._de_volta(request)

    def _de_volta(self, request):
        destino = reverse("painel_login")
        alvo = request.POST.get("next") or ""
        if alvo:
            destino += f"?next={alvo}"
        return HttpResponseRedirect(destino)
