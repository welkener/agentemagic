"""
Atende pedido de eliminação de dados do titular (LGPD art. 18, VI).

Faz o que é possível num sistema cuja trilha é imutável por exigência fiscal:
**destrói a chave de conteúdo do titular**, tornando irrecuperável o texto das
conversas dele, sem apagar linha nenhuma e sem quebrar a cadeia de hash. Ver
`apps/audit/conteudo.py`.

    python manage.py eliminar_dados_titular 11222333000181 --conferir
    python manage.py eliminar_dados_titular 11222333000181 --confirmar

⚠ **É irreversível.** Não há backup da chave — esse é justamente o ponto.

⚠ **O que NÃO é eliminado, e por quê**: o dado fiscal (que a nota foi emitida,
valor, protocolo, chave da NFS-e) permanece. Ele é obrigação legal de guarda, é
o que prova a emissão perante o fisco, e a própria LGPD ressalva o cumprimento
de obrigação legal. Apagá-lo criaria um problema maior que o que resolveria —
mas **quanto tempo guardar é decisão jurídica**, não deste comando.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.audit.conteudo import ChaveConteudo, eliminar_conteudo_do_titular
from apps.audit.models import Auditoria
from apps.audit.services import registrar
from apps.clients.models import Cliente


class Command(BaseCommand):
    help = "Elimina o conteúdo pessoal de um titular destruindo a chave dele (LGPD art. 18, VI)."

    def add_arguments(self, parser):
        parser.add_argument("cnpj", help="CNPJ do titular (só dígitos).")
        parser.add_argument("--conferir", action="store_true", help="Mostra o impacto e sai.")
        parser.add_argument(
            "--confirmar", action="store_true", help="Executa. Sem isto, nada é destruído."
        )

    def handle(self, *args, **opcoes):
        cnpj = "".join(c for c in opcoes["cnpj"] if c.isdigit())
        clientes = list(Cliente.objects.filter(cnpj=cnpj).select_related("escritorio"))
        if not clientes:
            raise CommandError(f"Nenhum cliente com CNPJ {cnpj}.")

        w = self.stdout.write

        for cliente in clientes:
            # O mesmo CNPJ pode estar em mais de um escritório (multi-tenant) —
            # e cada um é um titular distinto do ponto de vista do tratamento.
            linhas = Auditoria.objects.filter(cliente=cliente).count()
            chave = ChaveConteudo.objects.filter(cliente=cliente).first()
            estado = "já eliminada" if (chave and chave.destruida) else ("ativa" if chave else "inexistente")

            w(f"\n{cliente.nome} — escritório: {cliente.escritorio.nome}")
            w(f"  linhas de auditoria .....: {linhas}")
            w(f"  chave de conteúdo .......: {estado}")

            if opcoes["conferir"]:
                continue

            if not opcoes["confirmar"]:
                w(self.style.WARNING("  → nada feito. Use --confirmar para executar."))
                continue

            afetadas = eliminar_conteudo_do_titular(cliente)
            if afetadas == 0 and estado != "ativa":
                w(self.style.WARNING("  → nada a eliminar."))
                continue

            # A própria eliminação vira registro na trilha: é o que prova, depois,
            # que o pedido foi atendido e quando.
            registrar(
                "conteudo_pessoal_eliminado",
                {"motivo": "pedido do titular (LGPD art. 18, VI)", "linhas_afetadas": afetadas},
                cliente=cliente,
            )
            w(self.style.SUCCESS(f"  → chave destruída. {afetadas} linha(s) sem conteúdo legível."))

        if opcoes["conferir"]:
            w("\n[conferência] nada foi alterado.")
        elif opcoes["confirmar"]:
            w(
                "\nO dado FISCAL foi mantido (obrigação legal de guarda): que a nota "
                "existiu, valor, protocolo e chave da NFS-e."
            )
