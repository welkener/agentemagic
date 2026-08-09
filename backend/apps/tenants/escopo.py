"""
Escopo de tenant no admin — o que cada contador enxerga.

Regra, em uma frase: **superuser vê tudo; membro de escritório vê o próprio
escritório; qualquer outro não vê nada.** O terceiro caso é o que importa —
negar por padrão. Um staff sem `MembroEscritorio` é um usuário meio-provisionado,
e meio-provisionado nunca pode significar "acesso total à plataforma".

Filtrar `get_queryset` cobre listagem, busca e acesso direto por URL (o admin
resolve o objeto dentro do queryset já filtrado, então `/admin/.../<id>/` de
outro tenant vira 404). O que ele **não** cobre é o formulário: sem limitar
também os `ForeignKey`, o contador do escritório A conseguiria criar uma
credencial apontando pro cliente do B pelo dropdown. Por isso o mixin mexe nos
dois — ver `formfield_for_foreignkey`.
"""
from apps.tenants.models import Escritorio, MembroEscritorio


def escopo_do_usuario(usuario) -> "tuple[bool, Escritorio | None]":
    """Devolve `(irrestrito, escritorio)`.

    - `(True, None)`  → superuser (equipe Magic BI): sem filtro.
    - `(False, esc)`  → contador do escritório `esc`.
    - `(False, None)` → sem vínculo: não enxerga nada.
    """
    if not usuario or not usuario.is_authenticated:
        return False, None
    if usuario.is_superuser:
        return True, None

    # A consulta roda SEM escopo, e é a única maneira de funcionar: descobrir a
    # que escritório o usuário pertence é o ato que PRECEDE o escopo, e
    # `MembroEscritorio` é ela própria uma tabela com policy de RLS. Consultá-la
    # já escopada devolveria nada, o vínculo pareceria não existir, e todo
    # contador legítimo levaria 403 — inclusive no middleware, que chama esta
    # função justamente para descobrir qual tenant declarar.
    #
    # Não é brecha: a pergunta é "de quem é ESTE usuário", a resposta é uma
    # linha só, e ela vira imediatamente o escopo de tudo que vem depois. Mesmo
    # padrão do webhook, que resolve o tenant pelo número que recebeu a mensagem.
    #
    # Achado no primeiro screenshot real do Grimório, não em teste: a fixture
    # deixa o modo de montagem ligado, então a suíte inteira passava por cima
    # deste caminho sem exercitá-lo. `tests/test_rls.py` cobre isso agora.
    from apps.tenants.rls import escopo_irrestrito

    with escopo_irrestrito():
        membro = MembroEscritorio.objects.filter(usuario=usuario).select_related(
            "escritorio"
        ).first()
    return False, membro.escritorio if membro is not None else None


def escritorio_do_usuario(usuario) -> "Escritorio | None":
    """Escritório do usuário logado, ou None (superuser e sem-vínculo incluídos)."""
    _, escritorio = escopo_do_usuario(usuario)
    return escritorio


class EscopoEscritorioMixin:
    """Restringe um `ModelAdmin` ao escritório do usuário logado.

    `campo_escritorio` é o caminho de lookup deste model até o `Escritorio`
    (ex.: `"escritorio"` no Cliente, `"cliente__escritorio"` em tudo que
    pendura no cliente). `None` = model da plataforma, só superuser.
    """

    campo_escritorio: "str | None" = "cliente__escritorio"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        irrestrito, escritorio = escopo_do_usuario(request.user)
        if irrestrito:
            return qs
        if self.campo_escritorio is None or escritorio is None:
            return qs.none()
        # `pk`/`id` (o próprio Escritorio) precisa do valor, não do objeto —
        # lookups de FK aceitam a instância, o campo id não.
        valor = escritorio.pk if self.campo_escritorio in ("pk", "id") else escritorio
        return qs.filter(**{self.campo_escritorio: valor})

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Impede escolher, no formulário, objeto de outro tenant."""
        irrestrito, escritorio = escopo_do_usuario(request.user)
        if not irrestrito and escritorio is not None:
            if db_field.name == "cliente":
                from apps.clients.models import Cliente

                kwargs["queryset"] = Cliente.objects.filter(escritorio=escritorio)
            elif db_field.name == "escritorio":
                kwargs["queryset"] = Escritorio.objects.filter(pk=escritorio.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class SomentePlataformaMixin(EscopoEscritorioMixin):
    """Model que é da Magic BI, não de nenhum escritório (ex.: `AplicativoIntegracao`,
    que é o app OAuth de desenvolvedor compartilhado por todos os tenants)."""

    campo_escritorio = None

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser
