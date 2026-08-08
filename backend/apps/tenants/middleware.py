"""
Porta de entrada HTTP do escopo de tenant (DEC-04).

Toda requisição passa por aqui e sai com o Postgres sabendo de quem é o dado que
pode ser lido. O que decide é o **vínculo do usuário logado**, resolvido por
`escopo_do_usuario` — a mesma fonte que o admin já usa, não uma segunda regra
que poderia divergir dela.

Três casos, e o terceiro é o que importa:

| Quem | O que acontece |
|---|---|
| Contador com `MembroEscritorio` | `app.tenant_id` = escritório dele |
| Equipe Magic BI (`is_superuser`) | `app.irrestrito = on` — enxerga a plataforma |
| Anônimo, ou staff sem vínculo | nada setado → **nenhuma linha** |

O terceiro caso é o padrão seguro levado ao banco: um usuário meio-provisionado
já não via nada no admin (`apps/tenants/escopo.py`); agora também não vê nada no
Postgres, mesmo que algum código novo esqueça de filtrar.

**Por que a requisição inteira vira uma transação.** `SET LOCAL` só existe
dentro de uma; fora dela o Postgres apenas avisa e ignora, o que produziria RLS
ligada sem tenant nunca setado — telas vazias sem erro nenhum. Envolver a
requisição em `atomic` também é bom por si só num sistema fiscal: requisição que
estoura no meio não deixa escrita pela metade. O custo é que a transação vive o
tempo da requisição — aceitável aqui, onde o trabalho lento já foi todo para o
Celery (o webhook responde antes de processar).
"""
from django.db import transaction

from apps.tenants import rls
from apps.tenants.escopo import escopo_do_usuario


class EscopoDeTenantMiddleware:
    """Amarra a conexão do banco ao tenant do usuário desta requisição."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with transaction.atomic():
            rls.assumir_papel_restrito()

            irrestrito, escritorio = escopo_do_usuario(getattr(request, "user", None))

            if irrestrito:
                with rls.escopo_irrestrito():
                    return self.get_response(request)

            if escritorio is not None:
                with rls.escopo_de_tenant(escritorio.pk):
                    return self.get_response(request)

            # Sem vínculo: segue sem escopo. As policies devolvem zero linhas —
            # que é o certo. Não é erro: a tela de login e o webhook (que resolve
            # o tenant sozinho, pelo número que recebeu) passam por aqui.
            return self.get_response(request)
