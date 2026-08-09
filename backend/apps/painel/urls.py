"""Rotas do Grimório — a aplicação do contador (DEC-12).

URLs próprias, fora do `/admin/`. O admin continua existindo como backoffice e
segue escopado por tenant; o que muda é que ele deixa de ser a superfície de
trabalho diária.
"""
from django.urls import path

from apps.painel import grimorio

app_name = "grimorio"

urlpatterns = [
    path("", grimorio.HojeView.as_view(), name="hoje"),
    path("carteira/", grimorio.CarteiraView.as_view(), name="carteira"),
    # `cliente_id` e não slug: o id já é o que circula nas outras telas, e um
    # slug de razão social mudaria quando o cliente troca de nome na Receita.
    path("empresa/<int:cliente_id>/", grimorio.EmpresaView.as_view(), name="empresa"),
    path("documentos/", grimorio.DocumentosView.as_view(), name="documentos"),
    path("integracoes/", grimorio.IntegracoesView.as_view(), name="integracoes"),
    path("operacao/", grimorio.OperacaoView.as_view(), name="operacao"),
]
