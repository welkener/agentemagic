"""
Enche a carteira de um escritório com empresas fictícias, para demonstração.

**Por que isso existe.** Com três empresas cadastradas não dá para julgar a
aplicação: a Carteira não mostra se aguenta duzentas linhas, o Hoje não mostra
se prioriza bem quando há o que priorizar, e a Operação fica em "ainda medindo".
Quem vê a tela vazia conclui coisa errada sobre o produto — para melhor ou para
pior, e as duas atrapalham.

    python manage.py popular_carteira_demo
    python manage.py popular_carteira_demo --quantidade 120
    python manage.py popular_carteira_demo --limpar

**Três decisões que valem explicação:**

1. **As empresas entram no escritório que já existe, não num tenant novo.** Um
   segundo `Escritorio` ativo quebraria `escritorio_por_phone_number_id`, que só
   cai no escritório único enquanto existe um — e a mensagem de WhatsApp sem
   número casado passaria a ser recusada. Trocar a demonstração pelo atendimento
   seria péssimo negócio.

2. **Toda empresa criada aqui carrega `demonstracao=True`.** É o que responde
   "isso é de verdade?" no meio da reunião e o que faz `--limpar` remover
   exatamente o que foi criado, sem adivinhar.

3. **Nada passa pelos serviços que auditam.** As notas são gravadas com o estado
   final direto no modelo, em vez de `transicionar()`; os chamados, sem
   `atendimento.abrir()`. É deliberado: `Auditoria.cliente` é PROTECT e a trilha
   é encadeada por hash — se a demonstração escrevesse nela, `--limpar`
   precisaria apagar linhas de auditoria e quebraria a cadeia. Demonstração não
   pode custar a integridade do registro que sustenta a emissão fiscal.

O preço disso é honesto e pequeno: as notas fictícias não têm histórico de
transição. Quem abrir uma no admin vê o estado, não o caminho.
"""
from datetime import date, timedelta
from decimal import Decimal
import random

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.agents.agente_nf.models import Intencao
from apps.atendimento.models import Solicitacao
from apps.clients.models import Cliente, Perfil
from apps.credentials.models import Credencial
from apps.security.models import SessaoWhatsapp
from apps.tenants import rls
from apps.tenants.models import Escritorio

# Semente fixa: rodar duas vezes dá a mesma carteira. Numa demonstração isso
# importa — o contador que voltar à tela amanhã precisa reencontrar as mesmas
# empresas, senão o sistema parece instável.
SEMENTE = 20260809

# Ramo → (CNAE, nomes possíveis, serviços que ELE presta).
#
# Os serviços andam junto com o ramo, e não numa lista solta, porque a lista
# solta produzia "Corte e escova" numa transportadora. Ninguém quebra por causa
# disso — mas quem lê a fila percebe, e a partir daí lê a tela inteira como
# maquete. Verossimilhança é o produto aqui.
RAMOS = [
    ("Padaria", "1091-1/02", ["Pão Nosso", "Estrela do Sul", "Trigo de Ouro", "Delícia"],
     ["Fornecimento de salgados", "Encomenda de bolo", "Coffee break para evento"]),
    ("Salão", "9602-5/01", ["Bela Vista", "Charme", "Studio Vip", "Espaço Ana"],
     ["Corte e escova", "Coloração", "Manicure e pedicure", "Penteado para evento"]),
    ("Oficina", "4520-0/01", ["do Zé", "Mecânica Central", "AutoCar", "Freio Certo"],
     ["Troca de óleo e filtros", "Revisão de freios", "Alinhamento e balanceamento"]),
    ("Consultoria", "6204-0/00", ["Nexo TI", "Dados & Cia", "Rumo Digital", "Vetor"],
     ["Consultoria mensal", "Implantação de sistema", "Suporte técnico", "Treinamento de equipe"]),
    ("Doceria", "1091-1/01", ["Doce Lar", "Açúcar", "Confeitaria Bela", "Amor aos Doces"],
     ["Bolo de aniversário", "Doces para festa", "Encomenda de tortas"]),
    ("Mercearia", "4712-1/00", ["do Bairro", "Bom Preço", "Vizinhança", "Esquina"],
     ["Cesta básica para empresa", "Fornecimento de mercadorias"]),
    ("Fotografia", "7420-0/01", ["Click", "Luz & Cor", "Retrato", "Momento"],
     ["Ensaio fotográfico", "Cobertura de evento", "Foto de produto para catálogo"]),
    ("Academia", "9313-1/00", ["Corpo Livre", "Movimento", "Fit Center", "Energia"],
     ["Mensalidade de plano", "Avaliação física", "Personal trainer"]),
    ("Marcenaria", "1622-6/99", ["Madeira Nobre", "Arte em Móveis", "Serra & Cia"],
     ["Móvel sob medida", "Restauro de móvel", "Instalação de armário"]),
    ("Transportes", "4930-2/02", ["Rápido", "Entrega Já", "Rota Certa"],
     ["Frete municipal", "Entrega expressa", "Mudança residencial"]),
    ("Contabilidade", "6920-6/01", ["Precisa", "Balanço", "Guia Fiscal"],
     ["Honorários contábeis", "Abertura de empresa", "Declaração de imposto de renda"]),
    ("Pet Shop", "4789-0/04", ["Amigo Fiel", "Patinhas", "Mundo Animal"],
     ["Banho e tosa", "Consulta veterinária", "Hospedagem de pet"]),
]

SOBRENOMES = [
    "Silva", "Souza", "Oliveira", "Santos", "Pereira", "Lima", "Costa",
    "Ferreira", "Almeida", "Ribeiro", "Carvalho", "Gomes", "Martins", "Rocha",
]

ASSUNTOS_CHAMADO = [
    "a guia do DAS de julho veio com valor diferente",
    "preciso de uma certidão negativa pro banco",
    "meu funcionário pediu demissão, o que eu faço",
    "a nota que emiti ontem saiu com o valor errado",
    "quero saber se posso continuar como MEI ano que vem",
    "o cliente está pedindo nota com retenção de ISS",
]

TOMADORES = [
    "Comércio Ltda", "Serviços ME", "Distribuidora Ltda", "Participações S.A.",
    "& Filhos Ltda", "Empreendimentos ME", "Indústria Ltda",
]

ASSUNTOS_ATENDIMENTO = [
    "queria conversar sobre virar ME",
    "posso marcar uma conversa sobre o parcelamento",
    "preciso de ajuda para organizar as notas do ano",
]


class Command(BaseCommand):
    help = "Popula a carteira de um escritório com empresas de demonstração."

    def add_arguments(self, parser):
        parser.add_argument(
            "--escritorio",
            help="Slug do escritório. Padrão: o único ativo, se houver só um.",
        )
        parser.add_argument("--quantidade", type=int, default=48)
        parser.add_argument(
            "--limpar",
            action="store_true",
            help="Remove as empresas de demonstração em vez de criar.",
        )

    def handle(self, *args, **opcoes):
        # A demonstração é dado de plataforma sendo semeado antes de existir
        # requisição: não há tenant no contexto, e sem isto a RLS devolveria
        # zero linhas em tudo. Motivo escrito, como manda `escopo_irrestrito`.
        with rls.escopo_irrestrito():
            escritorio = self._escritorio(opcoes.get("escritorio"))
            if opcoes["limpar"]:
                return self._limpar(escritorio)
            self._criar(escritorio, opcoes["quantidade"])

    # ------------------------------------------------------------------
    def _escritorio(self, slug) -> Escritorio:
        if slug:
            escritorio = Escritorio.objects.filter(slug=slug).first()
            if escritorio is None:
                raise CommandError(f"Escritório {slug!r} não existe.")
            return escritorio
        ativos = list(Escritorio.objects.filter(ativo=True)[:2])
        if len(ativos) != 1:
            raise CommandError(
                "Há mais de um escritório ativo (ou nenhum) — diga qual com "
                "--escritorio <slug>. Semear no tenant errado é o tipo de erro "
                "que só se descobre na frente do cliente."
            )
        return ativos[0]

    def _limpar(self, escritorio: Escritorio):
        alvos = Cliente.objects.filter(escritorio=escritorio, demonstracao=True)
        quantos = alvos.count()
        # As tabelas-filhas caem por CASCADE. `Auditoria` não cai — é PROTECT —,
        # e é justamente por isso que nada aqui escreve nela.
        alvos.delete()
        self.stdout.write(
            self.style.SUCCESS(f"{quantos} empresa(s) de demonstração removida(s).")
        )

    # ------------------------------------------------------------------
    def _criar(self, escritorio: Escritorio, quantidade: int):
        sorteio = random.Random(SEMENTE)
        hoje = timezone.localdate()
        agora = timezone.now()

        nomes = self._nomes(sorteio, quantidade)
        criadas, reaproveitadas = 0, 0

        for indice, (razao, cnae, servicos) in enumerate(nomes):
            cnpj = f"{90000000 + indice:08d}{indice % 9 + 1:04d}{indice % 89 + 10:02d}"
            with transaction.atomic():
                cliente, nova = Cliente.objects.get_or_create(
                    escritorio=escritorio,
                    cnpj=cnpj,
                    defaults=self._cadastro(razao, cnae, indice, sorteio, hoje),
                )
                if not nova:
                    reaproveitadas += 1
                    continue
                criadas += 1
                self._perfil(cliente, indice)
                self._pessoas(cliente, indice, sorteio)
                self._sessao(cliente, indice, agora)
                self._certificado(cliente, indice, agora)
                self._notas(cliente, indice, sorteio, hoje, servicos)
                self._chamados(cliente, indice, sorteio)

        self.stdout.write(
            self.style.SUCCESS(
                f"{criadas} empresa(s) de demonstração criada(s) em {escritorio.nome}"
                + (f" ({reaproveitadas} já existiam)." if reaproveitadas else ".")
            )
        )
        self.stdout.write(
            "Todas marcadas com `demonstracao=True` e visíveis como tal na carteira.\n"
            "Para remover: python manage.py popular_carteira_demo --limpar"
        )

    def _nomes(self, sorteio, quantidade):
        combinacoes = [
            (f"{ramo} {sufixo}", cnae, servicos)
            for ramo, cnae, sufixos, servicos in RAMOS
            for sufixo in sufixos
        ]
        sorteio.shuffle(combinacoes)
        while len(combinacoes) < quantidade:
            ramo, cnae, _, servicos = sorteio.choice(RAMOS)
            combinacoes.append((f"{ramo} {sorteio.choice(SOBRENOMES)}", cnae, servicos))
        return combinacoes[:quantidade]

    def _cadastro(self, razao, cnae, indice, sorteio, hoje):
        # 3 em cada 4 são MEI — é a proporção da carteira que o produto atende,
        # e é o que faz o radar de teto ser a tela mais útil do Grimório.
        mei = indice % 4 != 3
        abertura = hoje - timedelta(days=sorteio.randint(120, 3200))
        return {
            "nome": f"{razao} {'MEI' if mei else 'Ltda'}",
            "email_contato": f"contato{indice}@exemplo.com.br",
            "cnae_padrao": cnae,
            "codigo_municipio_ibge": "3550308",
            "codigo_tributacao_nacional": "010101",
            "opcao_simples_nacional": (
                Cliente.OpcaoSimplesNacional.MEI if mei
                else Cliente.OpcaoSimplesNacional.ME_EPP
            ),
            "data_inicio_atividade": abertura,
            "aliquota_iss": Decimal("2.00"),
            "ativo": True,
            "demonstracao": True,
        }

    def _perfil(self, cliente, indice):
        Perfil.objects.create(
            cliente=cliente,
            persona="lumen",
            # Um em cada cinco tem ERP conectado — é a realidade de uma carteira
            # de micro, e é o que faz a diferença aparecer no catálogo de
            # ferramentas de cada um (ver `ferramentas.disponiveis_para`).
            ferramentas_habilitadas=(
                ["erp_mock", "nfse_mock"] if indice % 5 == 0 else ["nfse_mock"]
            ),
            tier_maximo=1,
        )

    def _pessoas(self, cliente, indice, sorteio):
        base = 5511900000000 + indice * 7
        cliente.vincular_usuario(
            str(base), principal=True, nome=f"{sorteio.choice(SOBRENOMES)}"
        )
        # A cada sete empresas, uma tem um segundo autorizado — e a cada treze,
        # esse segundo é o MESMO número de outra empresa. É o caso da DEC-03
        # (contador terceirizado, sócio de duas), e sem ele a tela nunca mostra
        # a coluna que existe justamente para isso.
        if indice % 7 == 3:
            compartilhado = "5511988887777" if indice % 13 == 3 else str(base + 1)
            cliente.vincular_usuario(
                compartilhado, papel="financeiro", nome="Financeiro"
            )

    def _sessao(self, cliente, indice, agora):
        # Uma em cada seis ainda não validou o WhatsApp — vira pendência no Hoje.
        if indice % 6 == 5:
            return
        SessaoWhatsapp.objects.create(
            cliente=cliente,
            wa_id=cliente.telefone_whatsapp,
            status=SessaoWhatsapp.Status.ATIVA,
            validado_em=agora - timedelta(days=3),
            expira_em=agora + timedelta(days=25),
        )

    def _certificado(self, cliente, indice, agora):
        """Certificado com validade variada — é o que enche a fila do Hoje.

        Sem blob nenhum: o modelo guarda referência de cofre, e o painel só lê
        `expira_em`. Emissão de verdade não acontece com empresa fictícia.
        """
        if indice % 5 == 4:
            return  # sem certificado
        if indice % 11 == 2:
            validade = agora - timedelta(days=indice % 9 + 1)  # vencido
        elif indice % 11 == 5:
            validade = agora + timedelta(days=indice % 20 + 3)  # vencendo
        else:
            validade = agora + timedelta(days=200 + indice)
        Credencial.objects.create(
            cliente=cliente,
            tipo=Credencial.Tipo.CERTIFICADO_PFX,
            integracao="nfse_nacional",
            referencia_cofre=f"demo://certificado/{cliente.pk}",
            expira_em=validade,
        )

    def _repartir(self, total: Decimal, partes: int, sorteio) -> list[Decimal]:
        """Divide `total` em `partes` valores diferentes que somam exatamente ele.

        Dividir por igual era o caminho curto e aparecia na tela: a mesma nota de
        R$ 620,97 repetida quinze vezes na lista de documentos. Aqui cada nota
        recebe um peso sorteado entre 0,5 e 1,8 do médio, e a última absorve o
        arredondamento — o faturamento do ano continua batendo com o alvo, que é
        o que faz o radar de teto contar a história certa.
        """
        pesos = [Decimal(sorteio.randint(50, 180)) for _ in range(partes)]
        soma = sum(pesos)
        valores = [
            (total * peso / soma).quantize(Decimal("0.01")) for peso in pesos[:-1]
        ]
        valores.append((total - sum(valores)).quantize(Decimal("0.01")))
        return valores

    def _notas(self, cliente, indice, sorteio, hoje, servicos):
        """Notas ao longo do ano, com faturamento que faz o radar de teto falar.

        A distribuição é escolhida, não aleatória: a carteira precisa conter os
        casos que a tela existe para mostrar — um estouro, dois em zona crítica,
        alguns em atenção e a maioria tranquila. Carteira só de casos tranquilos
        não prova que o alerta funciona.
        """
        if indice % 17 == 1:
            alvo = Decimal("84500")      # estourou o teto
        elif indice % 17 in (3, 9):
            alvo = Decimal("75000")      # crítico (>90%)
        elif indice % 5 == 2:
            alvo = Decimal("60000")      # atenção (>70%)
        else:
            alvo = Decimal(sorteio.randint(2000, 38000))

        # Ruído por empresa. Sem ele, as faixas escolhidas acima davam faturamento
        # IDÊNTICO em cinco linhas seguidas — e coluna de dinheiro com valores
        # repetidos denuncia dado fabricado antes de qualquer outra coisa na tela.
        # A faixa (±7%) é estreita de propósito: preserva quem está acima do teto
        # e quem está em zona crítica, que é o que a demonstração precisa mostrar.
        alvo = (alvo * (Decimal(sorteio.randint(930, 1070)) / 1000)).quantize(Decimal("0.01"))

        # Ticket médio também varia por ramo — padaria emite muita nota pequena,
        # consultoria emite poucas e grandes.
        ticket = Decimal(sorteio.choice([380, 620, 950, 1400, 2200, 3100]))
        quantidade = max(int(alvo / ticket), 1)
        valores = self._repartir(alvo, quantidade, sorteio)
        inicio_ano = date(hoje.year, 1, 1)
        dias = max((hoje - inicio_ano).days, 1)

        for n, valor in enumerate(valores):
            quando = inicio_ano + timedelta(days=int(dias * (n + 0.5) / quantidade))
            momento = timezone.make_aware(
                timezone.datetime.combine(quando, timezone.datetime.min.time())
            )
            nota = Intencao.objects.create(
                cliente=cliente,
                chave_idempotencia=f"demo-{cliente.pk}-{n}",
                tipo_acao="emitir_nfse",
                payload={
                    "cnpj_prestador": cliente.cnpj,
                    "cnae": cliente.cnae_padrao,
                    "valor": float(valor),
                    "descricao_servico": sorteio.choice(servicos),
                    "tomador": f"{sorteio.choice(SOBRENOMES)} {sorteio.choice(TOMADORES)}",
                },
                valor=valor,
                estado=Intencao.Estado.CONCLUIDO,
                protocolo=f"DEMO{cliente.pk:04d}{n:03d}",
            )
            # `auto_now` ignora o valor passado no create — a data precisa ser
            # gravada por update, senão toda nota da demonstração aparece como
            # emitida hoje e o gráfico do ano sai uma barra só.
            Intencao.objects.filter(pk=nota.pk).update(atualizado_em=momento)

        # Uma em cada nove tem nota parada esperando o contador; uma em cada
        # dezenove, uma rejeitada. São as duas linhas críticas do Hoje — e o
        # valor e o tomador variam pelo mesmo motivo do faturamento: doze linhas
        # idênticas na fila fazem o contador ler "lista de exemplo" em vez de
        # "trabalho a fazer", que é o oposto do que a tela quer provocar.
        if indice % 9 == 4:
            valor_pendente = Decimal(sorteio.randint(180, 4800))
            Intencao.objects.create(
                cliente=cliente,
                chave_idempotencia=f"demo-{cliente.pk}-pendente",
                tipo_acao="emitir_nfse",
                payload={
                    "cnpj_prestador": cliente.cnpj,
                    "cnae": cliente.cnae_padrao,
                    "valor": float(valor_pendente),
                    "descricao_servico": sorteio.choice(servicos),
                    "tomador": f"{sorteio.choice(SOBRENOMES)} {sorteio.choice(TOMADORES)}",
                },
                valor=valor_pendente,
                estado=Intencao.Estado.AGUARDANDO_APROVACAO,
            )
        if indice % 19 == 7:
            valor_rejeitado = Decimal(sorteio.randint(150, 2600))
            Intencao.objects.create(
                cliente=cliente,
                chave_idempotencia=f"demo-{cliente.pk}-rejeitada",
                tipo_acao="emitir_nfse",
                payload={
                    "cnpj_prestador": cliente.cnpj,
                    "cnae": cliente.cnae_padrao,
                    "valor": float(valor_rejeitado),
                    "descricao_servico": sorteio.choice(servicos),
                    "tomador": f"{sorteio.choice(SOBRENOMES)} {sorteio.choice(TOMADORES)}",
                },
                valor=valor_rejeitado,
                estado=Intencao.Estado.REJEITADO,
            )

    def _chamados(self, cliente, indice, sorteio):
        if indice % 8 != 2:
            return
        atendimento = indice % 16 == 10
        assunto = sorteio.choice(
            ASSUNTOS_ATENDIMENTO if atendimento else ASSUNTOS_CHAMADO
        )
        Solicitacao.objects.create(
            cliente=cliente,
            usuario=cliente.vinculos.first().usuario,
            tipo=(
                Solicitacao.Tipo.ATENDIMENTO if atendimento else Solicitacao.Tipo.CHAMADO
            ),
            assunto=assunto,
            descricao=assunto,
            protocolo=f"{'AT' if atendimento else 'CH'}-DEMO-{cliente.pk:05d}",
        )
