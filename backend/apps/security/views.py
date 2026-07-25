"""
Endpoint que o cliente abre ao clicar no Magic Link recebido por e-mail.

Painel React "bonito" fica pra Onda 4 (`docs/magicbi-ondas-desenvolvimento.md`)
— por ora, uma resposta simples é suficiente pro cliente saber que pode
voltar ao WhatsApp, no mesmo espírito de "Django admin como Grimório interino"
usado no resto do projeto.
"""
from django.http import HttpResponse
from django.views import View

from .services import validar_magic_link


class ValidarMagicLinkView(View):
    def get(self, request, token: str):
        ok, mensagem = validar_magic_link(token)
        status = 200 if ok else 400
        corpo = f"""<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8"><title>Magic BI</title></head>
<body style="font-family: sans-serif; max-width: 480px; margin: 4rem auto; text-align: center;">
<h1>Magic BI</h1>
<p>{mensagem}</p>
</body></html>"""
        return HttpResponse(corpo, status=status, content_type="text/html; charset=utf-8")
