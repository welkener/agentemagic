"""
Leva um banco vazio ao estado testável de ponta a ponta, em um comando.

Existe porque "rodar o sistema" hoje exige acertar sete coisas em ordem
(escritório → contador → grupo de permissões → cliente com cadastro fiscal
completo → perfil → sessão WhatsApp → app de integração), e errar qualquer uma
dá um erro que não diz qual foi. Isso queimava tempo em toda rodada de teste.

**Não substitui o provisionamento real.** `provisionar_escritorio` é o caminho
de produção; este aqui é o de bancada: cria dado de demonstração, marcado como
tal, e é idempotente (rodar de novo não duplica).

    python manage.py preparar_teste
    python manage.py preparar_teste --limpar   # apaga o que ele mesmo criou

O que fica pronto:
- escritório "Magic BI — Ambiente de Teste" + contador `teste.contador`;
- cliente `Padaria do Teste` com o cadastro fiscal **completo** (IBGE, cTribNac,
  ISS) — é o que faltava pra DPS válida sair;
- perfil Tier 1 e sessão WhatsApp ativa (senão o gate de segurança barra);
- app de integração NFS-e apontando pra **Produção Restrita**.

O que ele NÃO faz, de propósito: subir certificado (é segredo real, vai pelo
admin) e configurar canal WhatsApp (depende de token seu).
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.clients.models import Cliente, Perfil
from apps.credentials.models import AplicativoIntegracao
from apps.security.models import SessaoWhatsapp
from apps.tenants.models import Escritorio, MembroEscritorio, grupo_do_escritorio
from apps.tenants.permissoes import aplicar_permissoes_base

SLUG = "ambiente-de-teste"
USUARIO = "teste.contador"
CNPJ = "11222333000181"
TELEFONE = "5511977776666"

URL_PRODUCAO_RESTRITA = "https://adn.producaorestrita.nfse.gov.br"


class Command(BaseCommand):
    help = "Prepara um ambiente de teste completo (escritório, contador, cliente, perfil, sessão)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limpar", action="store_true", help="Remove o ambiente de teste e sai."
        )
        parser.add_argument(
            "--telefone",
            default=TELEFONE,
            help=f"WhatsApp do cliente de teste (padrão: {TELEFONE}). Use o SEU número real.",
        )

    @transaction.atomic
    def handle(self, *args, **opcoes):
        User = get_user_model()

        if opcoes["limpar"]:
            Cliente.objects.filter(cnpj=CNPJ).delete()
            User.objects.filter(username=USUARIO).delete()
            Escritorio.objects.filter(slug=SLUG).delete()
            self.stdout.write(self.style.SUCCESS("Ambiente de teste removido."))
            return

        escritorio, novo_esc = Escritorio.objects.get_or_create(
            slug=SLUG, defaults={"nome": "Magic BI — Ambiente de Teste", "ativo": True}
        )
        grupo = grupo_do_escritorio(escritorio)
        aplicar_permissoes_base(grupo)

        contador, novo_cont = User.objects.get_or_create(
            username=USUARIO, defaults={"email": "teste@magicbi.example.com", "is_staff": True}
        )
        if novo_cont:
            contador.set_unusable_password()
            contador.save()
        contador.groups.add(grupo)
        MembroEscritorio.objects.get_or_create(
            usuario=contador, defaults={"escritorio": escritorio, "responsavel": True}
        )

        # Cadastro fiscal COMPLETO — sem isto a DPS não passa no XSD, que é
        # justamente o erro que consome a primeira meia hora de cada teste.
        cliente, _ = Cliente.objects.update_or_create(
            cnpj=CNPJ,
            defaults={
                "escritorio": escritorio,
                "nome": "Padaria do Teste Ltda",
                "telefone_whatsapp": opcoes["telefone"],
                "email_contato": "dono@padariateste.example.com",
                "cnae_padrao": "5611-2/01",
                "codigo_municipio_ibge": "3550308",  # São Paulo
                "codigo_tributacao_nacional": "010101",
                "inscricao_municipal": "1234567",
                "opcao_simples_nacional": Cliente.OpcaoSimplesNacional.MEI,
                "regime_especial_tributacao": 0,
                "iss_tributacao": Cliente.TributacaoIssqn.OPERACAO_TRIBUTAVEL,
                "iss_retencao": Cliente.RetencaoIssqn.NAO_RETIDO,
                "serie_dps": "1",
                "ativo": True,
            },
        )
        Perfil.objects.update_or_create(
            cliente=cliente,
            defaults={
                "persona": "lumen",
                "ferramentas_habilitadas": ["erp_mock", "nfse_mock"],
                "tier_maximo": 1,
            },
        )
        # Sem sessão ativa o gate de segurança barra toda mensagem — e o erro
        # ("preciso confirmar sua identidade") parece bug de canal.
        agora = timezone.now()
        SessaoWhatsapp.objects.update_or_create(
            cliente=cliente,
            defaults={
                "wa_id": cliente.telefone_whatsapp,
                "status": SessaoWhatsapp.Status.ATIVA,
                "validado_em": agora,
                "expira_em": agora + timedelta(days=7),
            },
        )
        AplicativoIntegracao.objects.get_or_create(
            nome=AplicativoIntegracao.Nome.NFSE_NACIONAL,
            defaults={
                "ambiente": "homologacao",
                "base_url": URL_PRODUCAO_RESTRITA,
                "ativo": False,  # só ativar quando houver cadastro no ADN
            },
        )

        self._resumo(escritorio, contador, cliente, grupo, novo_esc)

    def _resumo(self, escritorio, contador, cliente, grupo, novo):
        w = self.stdout.write
        w(self.style.SUCCESS(f"\n{'CRIADO' if novo else 'ATUALIZADO'}: ambiente de teste\n"))
        w(f"  Escritório .......: {escritorio.nome} ({escritorio.slug})")
        w(f"  Contador .........: {contador.username}  [responsável, {grupo.permissions.count()} permissões]")
        w(f"  Cliente ..........: {cliente.nome} — CNPJ {cliente.cnpj}")
        w(f"  WhatsApp .........: {cliente.telefone_whatsapp}  (sessão ATIVA)")
        w(f"  Cadastro fiscal ..: IBGE {cliente.codigo_municipio_ibge} | "
          f"cTribNac {cliente.codigo_tributacao_nacional} | série {cliente.serie_dps}")

        w("\nPróximos passos:")
        w(f"  1. python manage.py enviar_link_contador {contador.username}")
        w("     (ou crie um superuser e entre em /admin/)")
        w("  2. Teste sem canal nenhum, direto no shell:")
        w('     python manage.py testar_conversa "quais notas eu emiti?"')
        w("  3. Emissão real: suba o .pfx em Credenciais e ative o app NFS-e")
        w("     (só depois do cadastro na Produção Restrita).")
        w(self.style.WARNING(
            "\n  ⚠ Dado de DEMONSTRAÇÃO. O adapter resolvido é o MOCK enquanto não "
            "houver Credencial de certificado + AplicativoIntegracao ativo."
        ))
