"""
Backoffice dos documentos — e a fila de trabalho pendurada nele.

A changelist responde "onde está o documento X"; a fila de revisão responde "o
que exige você agora". São perguntas diferentes e por isso são telas diferentes,
mas moram no mesmo lugar desde 12/ago/2026: o pedido do usuário foi uma
superfície só, dentro do admin. As páginas próprias entram por `get_urls()`,
como a Carteira já fazia em `clients/admin.py`.
"""
from django.contrib import admin
from django.urls import path
from unfold.admin import ModelAdmin

from apps.documentos.models import Documento
from apps.tenants.escopo import EscopoEscritorioMixin


@admin.register(Documento)
class DocumentoAdmin(EscopoEscritorioMixin, ModelAdmin):
    list_display = (
        "protocolo", "cliente", "tipo", "situacao", "nome_arquivo", "criado_em"
    )
    list_filter = ("situacao", "tipo", "origem", "criado_em")
    search_fields = ("protocolo", "nome_arquivo", "cliente__nome", "hash_sha256")
    # O endereço no storage e o hash são fatos do arquivo recebido — editá-los
    # faria a linha apontar para outro objeto sem que nada acusasse.
    readonly_fields = (
        "protocolo", "cliente", "usuario", "bucket", "chave", "nome_arquivo",
        "tipo_mime", "tamanho", "hash_sha256", "origem", "criado_em",
    )

    def get_urls(self):
        """A fila de revisão e suas ações penduram aqui.

        **Antes de `super()` de propósito**, e não por estilo: a lista de URLs
        do ModelAdmin termina num catch-all `<path:object_id>/`, que engoliria
        "revisao" e tentaria abrir um documento com esse id. É a mesma armadilha
        já documentada em `clients/admin.py`.
        """
        from apps.painel import revisao

        proprio = self.admin_site.admin_view
        return [
            path(
                "revisao/",
                proprio(revisao.RevisaoDocumentosView.as_view(model_admin=self)),
                name="painel_revisao",
            ),
            path(
                "revisao/enviar/",
                proprio(revisao.EnviarDocumentoView.as_view()),
                name="painel_revisao_enviar",
            ),
            path(
                "revisao/<int:pk>/classificar/",
                proprio(revisao.ClassificarDocumentoView.as_view()),
                name="painel_revisao_classificar",
            ),
            path(
                "revisao/<int:pk>/arquivo/",
                proprio(revisao.ArquivoDocumentoView.as_view()),
                name="painel_revisao_arquivo",
            ),
        ] + super().get_urls()
