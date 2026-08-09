"""
Cadastra um cliente a partir do CNPJ, puxando o que a Receita já publica.

    python manage.py cadastrar_cliente 11222333000181 \\
        --escritorio ambiente-de-teste --telefone 5511999998888

Substitui o preenchimento manual dos campos públicos (razão social, código IBGE
do município, CNAE, enquadramento no Simples). Os três de julgamento fiscal
ficam de fora **de propósito** — ver `apps/clients/receita.py` para o porquê — e
o comando termina dizendo exatamente quais são, com o link do admin.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.clients.models import Cliente, Perfil
from apps.clients.receita import ErroConsultaCnpj, consultar_cnpj
from apps.fiscal.dps import conferir_cadastro
from apps.tenants.models import Escritorio


class Command(BaseCommand):
    help = "Cadastra (ou atualiza) um cliente a partir do CNPJ, via consulta pública."

    def add_arguments(self, parser):
        parser.add_argument("cnpj", help="CNPJ do cliente (com ou sem pontuação).")
        parser.add_argument("--escritorio", required=True, help="Slug do escritório dono.")
        parser.add_argument("--telefone", required=True, help="WhatsApp, ex.: 5511999998888.")
        parser.add_argument("--tier", type=int, default=1, help="Tier máximo do perfil (padrão 1).")

    @transaction.atomic
    def handle(self, *args, **opcoes):
        escritorio = Escritorio.objects.filter(slug=opcoes["escritorio"]).first()
        if escritorio is None:
            raise CommandError(
                f"Escritório '{opcoes['escritorio']}' não encontrado. "
                "Rode `provisionar_escritorio` antes."
            )

        try:
            dados = consultar_cnpj(opcoes["cnpj"])
        except ErroConsultaCnpj as exc:
            raise CommandError(str(exc)) from exc

        if not dados.ativa:
            self.stdout.write(
                self.style.WARNING(
                    f"⚠ Situação cadastral: {dados.situacao_cadastral}. "
                    "Emitir nota de empresa não ativa é problema — confira antes de seguir."
                )
            )

        cliente, criado = Cliente.objects.update_or_create(
            escritorio=escritorio,
            cnpj=dados.cnpj,
            defaults={
                "nome": dados.razao_social,
                "email_contato": dados.email,
                "codigo_municipio_ibge": dados.codigo_municipio_ibge,
                "cnae_padrao": dados.cnae_padrao,
                "opcao_simples_nacional": dados.opcao_simples_nacional,
                "data_inicio_atividade": dados.data_inicio_atividade,
                "ativo": True,
            },
        )
        # O telefone é do usuário, não da empresa (DEC-03) — fora do
        # `update_or_create` porque criar a pessoa é outro passo, não um campo.
        cliente.vincular_usuario(opcoes["telefone"], principal=True)

        Perfil.objects.get_or_create(
            cliente=cliente,
            defaults={
                "persona": "lumen",
                "ferramentas_habilitadas": ["erp_mock", "nfse_mock"],
                "tier_maximo": opcoes["tier"],
            },
        )

        w = self.stdout.write
        w(self.style.SUCCESS(f"\n{'Criado' if criado else 'Atualizado'}: {cliente.nome}"))
        w(f"  CNPJ .......: {cliente.cnpj}")
        w(f"  Município ..: {dados.municipio}/{dados.uf} (IBGE {dados.codigo_municipio_ibge or '—'})")
        w(f"  CNAE .......: {cliente.cnae_padrao or '—'}")
        w(f"  Simples ....: {cliente.get_opcao_simples_nacional_display()}")

        faltantes = conferir_cadastro(cliente)
        if faltantes:
            w(self.style.WARNING("\n⚠ AINDA NÃO EMITE. Falta você definir (a Receita não tem):"))
            for item in faltantes:
                w(f"    • {item}")
            w(f"\n  Complete em: /admin/clients/cliente/{cliente.pk}/change/")
        else:
            w(self.style.SUCCESS("\n✅ Cadastro fiscal completo — este cliente já pode emitir."))
