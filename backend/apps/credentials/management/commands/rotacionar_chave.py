"""
Recifra todos os segredos com a chave ativa.

Passo 3 do procedimento de rotação (`apps/credentials/chaves.py`). Roda com o
sistema **no ar**: enquanto não termina, tudo continua legível pelas chaves
antigas, que ainda estão na lista.

    python manage.py rotacionar_chave --conferir     # só relatório, não escreve
    python manage.py rotacionar_chave

Depois que passar limpo, remova a chave antiga da lista — e só então ela deixa
de decifrar qualquer coisa.

Por que percorre model por model em vez de um UPDATE genérico: `CampoTextoCifrado`
decifra na leitura e cifra na escrita, então `obj.save()` já recifra com a chave
ativa. Um SQL cru passaria por baixo disso e gravaria texto puro.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.credentials.chaves import chaves_configuradas
from apps.credentials.crypto import CampoTextoCifrado


def _models_com_segredo():
    """Descobre sozinho quem tem campo cifrado.

    Lista fixa envelheceria calada: alguém adiciona um `CampoTextoCifrado` num
    model novo, a rotação silenciosamente não o cobre, e o segredo fica preso na
    chave antiga até alguém notar — provavelmente ao removê-la.
    """
    from django.apps import apps

    encontrados = []
    for model in apps.get_models():
        campos = [f.name for f in model._meta.get_fields() if isinstance(f, CampoTextoCifrado)]
        if campos:
            encontrados.append((model, campos))
    return encontrados


class Command(BaseCommand):
    help = "Recifra todos os segredos com a chave ativa (rotação de chave)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--conferir",
            action="store_true",
            help="Só mostra o que seria recifrado, sem escrever nada.",
        )

    def handle(self, *args, **opcoes):
        chaves = chaves_configuradas()
        w = self.stdout.write

        w(f"Chaves configuradas: {len(chaves)} (ativa + {len(chaves) - 1} antiga(s))")
        if len(chaves) == 1:
            w(
                self.style.WARNING(
                    "  ⚠ Só uma chave na lista. Se ela já é a nova, o que estava cifrado "
                    "com a anterior NÃO é mais legível — coloque a antiga de volta na "
                    "lista antes de rotacionar."
                )
            )

        alvos = _models_com_segredo()
        if not alvos:
            w("Nenhum model com campo cifrado encontrado.")
            return

        total = 0
        ilegiveis = []

        for model, campos in alvos:
            rotulo = f"{model._meta.app_label}.{model.__name__}"
            # Só os PKs: `from_db_value` decifra na materialização do queryset,
            # então carregar tudo de uma vez estouraria no primeiro registro
            # ilegível — antes de qualquer try/except por linha.
            pks = list(model.objects.values_list("pk", flat=True))
            w(f"\n{rotulo} — {len(pks)} registro(s), campos: {', '.join(campos)}")

            for pk in pks:
                try:
                    obj = model.objects.get(pk=pk)  # a leitura já decifra
                except Exception as exc:  # noqa: BLE001
                    # Segredo que nenhuma chave abre: provavelmente a chave que o
                    # cifrou saiu da lista. Reportar e SEGUIR — abortar aqui
                    # deixaria a base metade rotacionada.
                    ilegiveis.append((rotulo, pk, str(exc) or type(exc).__name__))
                    continue

                if not opcoes["conferir"]:
                    # Salvar recifra com a chave ativa — é a rotação em si.
                    with transaction.atomic():
                        obj.save(update_fields=campos)
                total += 1

        if opcoes["conferir"]:
            w(self.style.SUCCESS(f"\n[conferência] {total} registro(s) legíveis e prontos."))
        else:
            w(self.style.SUCCESS(f"\n{total} registro(s) recifrados com a chave ativa."))

        if ilegiveis:
            w(self.style.ERROR(f"\n⚠ {len(ilegiveis)} registro(s) que NENHUMA chave abre:"))
            for rotulo, pk, erro in ilegiveis:
                w(f"    {rotulo} #{pk}: {erro}")
            w(
                self.style.ERROR(
                    "  Não remova nenhuma chave da lista até resolver — estes segredos "
                    "precisarão ser recadastrados à mão."
                )
            )
        elif not opcoes["conferir"]:
            w("\nAgora pode remover a chave antiga da lista com segurança.")
