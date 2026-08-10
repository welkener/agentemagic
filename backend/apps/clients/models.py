"""Modelos de cliente e perfil (um perfil por cliente — princípio da arquitetura).

**Três níveis de tenancy** (DEC-03, `docs/hermes-contabil-decisoes.md`):

    Escritorio  →  Cliente  →  Usuario
    (o tenant)     (a EMPRESA)  (a PESSOA que fala no WhatsApp)

O telefone morava em `Cliente`, com unicidade por escritório: uma empresa, um
telefone. Numa carteira de 1.000 empresas isso não é simplificação — é erro de
modelagem. O sócio de duas empresas, o financeiro que responde por três lojas e
o contador terceirizado não são exceção, são rotina, e a constraint **impedia**
o cadastro. O caso falhava em silêncio no atendimento.

Agora o telefone é do `Usuario`, e `VinculoUsuarioCliente` diz de quais empresas
ele fala. Quando são várias, quem escolhe é o cliente — o agente pergunta e fixa
a resposta (`apps/core/desambiguacao.py`). Escolher em silêncio aqui é emitir
nota fiscal no CNPJ errado.
"""
import structlog
from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.clients import telefone as telefone_br

logger = structlog.get_logger(__name__)


class ClienteManager(models.Manager):
    """`create()` aceita `telefone_whatsapp` como atalho do caso comum.

    Uma empresa com um responsável é a esmagadora maioria dos cadastros, e
    obrigar duas chamadas para isso só produziria cadastro pela metade. O atalho
    escreve na fonte de verdade nova (`Usuario` + vínculo principal) — não é um
    segundo lugar onde o telefone mora.

    Não existe mais `Cliente.objects.por_telefone`: a pergunta "de quem é este
    número" passou a ter resposta possivelmente plural, e um método que devolve
    um cliente só esconderia justamente o caso que o DEC-03 veio resolver. Quem
    busca por telefone usa `Usuario.objects.por_telefone`.
    """

    def create(self, **kwargs):
        numero = kwargs.pop("telefone_whatsapp", None)
        with transaction.atomic():
            cliente = super().create(**kwargs)
            if numero:
                cliente.vincular_usuario(numero, principal=True)
        return cliente


class UsuarioManager(models.Manager):
    """A busca por telefone — o único caminho autorizado.

    Existe para que nenhum código novo volte a comparar o número como string
    crua: o WhatsApp entrega o número brasileiro ora com o nono dígito, ora sem
    (ver `apps/clients/telefone.py`), e a comparação ingênua faz o cliente
    receber "não te reconheço" sem que erro nenhum apareça no log.
    """

    def por_telefone(self, numero, escritorio=None):
        """Usuário ativo dono deste número, ou None.

        Casa qualquer grafia equivalente. O número é gravado na forma canônica
        (`Usuario.save`), então em base nova a variante só protege contra linha
        antiga — barato o bastante para valer como rede.
        """
        formas = telefone_br.variantes(numero)
        if not formas:
            return None

        qs = self.filter(telefone_whatsapp__in=formas, ativo=True)
        if escritorio is not None:
            qs = qs.filter(escritorio=escritorio)

        encontrados = list(qs[:2])
        if len(encontrados) > 1:
            # Só acontece com linha anterior à canonicalização. A grafia exata
            # vence e a ambiguidade vai para o log — nunca escolha silenciosa.
            logger.warning(
                "usuario_telefone_ambiguo",
                telefone=numero,
                usuarios=[u.pk for u in encontrados],
            )
            exato = next((u for u in encontrados if u.telefone_whatsapp == formas[0]), None)
            return exato or encontrados[0]
        return encontrados[0] if encontrados else None


class Cliente(models.Model):
    """Empresa atendida por um escritório contábil parceiro (MEI/ME/EPP).

    `escritorio` é a raiz de multi-tenancy: tudo que pendura no cliente
    (perfil, credenciais, intenções, auditoria, sessão) herda o tenant por
    aqui. Ver `apps/tenants/models.py`.
    """

    escritorio = models.ForeignKey(
        "tenants.Escritorio",
        on_delete=models.PROTECT,  # nunca apagar escritório levando junto dado fiscal
        related_name="clientes",
        help_text="Escritório contábil dono desta carteira.",
    )
    cnpj = models.CharField(max_length=14)
    nome = models.CharField(max_length=200)
    email_contato = models.EmailField(
        blank=True,
        default="",
        help_text=(
            "E-mail cadastrado na Receita/ERP — canal do Magic Link e do código 2FA. "
            "Nunca reaproveitar o WhatsApp como canal desses segredos (2º fator de canal)."
        ),
    )
    data_inicio_atividade = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "Data de abertura na Receita — preenchida pela consulta pública. "
            "Usada só para o teto proporcional do MEI no ano de abertura "
            "(quem abre em julho tem direito a meio teto, não ao teto cheio). "
            "Vazia = o radar de teto assume o ano inteiro e diz isso na tela."
        ),
    )
    cnae_padrao = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text=(
            "CNAE do serviço prestado, cadastrado pelo contador — nunca inferido "
            "pelo LLM (guard determinístico da emissão fiscal). ⚠ NÃO é o que vai "
            "na DPS: a NFS-e Nacional usa `codigo_tributacao_nacional` (cTribNac). "
            "CNAE é classificação de atividade econômica, serve pro cadastro."
        ),
    )

    # --- Campos exigidos pela DPS da NFS-e Nacional ------------------------
    # Confirmados contra o XSD oficial (nfelib.nfse.bindings.v1_0), não
    # inferidos: cada um é `required` no schema e tem pattern próprio. Sem eles
    # não existe DPS válida — ver apps/fiscal/dps.py::montar_dps, que recusa a
    # emissão listando exatamente o que falta em vez de mandar XML inválido.
    codigo_municipio_ibge = models.CharField(
        max_length=7,
        blank=True,
        default="",
        help_text="Código IBGE do município (7 dígitos) — `cLocEmi`/`cLocPrestacao` da DPS.",
    )
    inscricao_municipal = models.CharField(
        max_length=15,
        blank=True,
        default="",
        help_text="Inscrição municipal do prestador (`prest.IM`). Opcional em alguns municípios.",
    )
    codigo_tributacao_nacional = models.CharField(
        max_length=6,
        blank=True,
        default="",
        help_text=(
            "`cTribNac` — 6 dígitos da lista nacional de serviços (LC 116). "
            "É ESTE que vai na nota, não o CNAE. Cadastrado pelo contador, "
            "nunca inferido pelo LLM."
        ),
    )
    # `prest.regTrib` é um GRUPO obrigatório no XSD, não um campo só — e dentro
    # dele `opSimpNac` e `regEspTrib` também são obrigatórios. Descoberto
    # validando o XML contra o schema oficial, não lendo doc.
    class OpcaoSimplesNacional(models.IntegerChoices):
        NAO_OPTANTE = 1, "Não optante"
        MEI = 2, "Optante — MEI"
        ME_EPP = 3, "Optante — ME/EPP"

    opcao_simples_nacional = models.PositiveSmallIntegerField(
        choices=OpcaoSimplesNacional.choices,
        default=OpcaoSimplesNacional.NAO_OPTANTE,
        help_text="`regTrib.opSimpNac` — enquadramento no Simples Nacional.",
    )
    regime_especial_tributacao = models.PositiveSmallIntegerField(
        default=0,
        help_text="`regTrib.regEspTrib` — 0 = nenhum; 1 a 6 conforme tabela da NT vigente.",
    )

    class TributacaoIssqn(models.IntegerChoices):
        OPERACAO_TRIBUTAVEL = 1, "Operação tributável"
        EXPORTACAO = 2, "Exportação de serviço"
        NAO_INCIDENCIA = 3, "Não incidência / imunidade"
        EXIGIBILIDADE_SUSPENSA = 4, "Exigibilidade suspensa"

    class RetencaoIssqn(models.IntegerChoices):
        NAO_RETIDO = 1, "Não retido"
        RETIDO_TOMADOR = 2, "Retido pelo tomador"
        RETIDO_INTERMEDIARIO = 3, "Retido pelo intermediário"

    iss_tributacao = models.PositiveSmallIntegerField(
        choices=TributacaoIssqn.choices,
        default=TributacaoIssqn.OPERACAO_TRIBUTAVEL,
        help_text="`trib.tribMun.tribISSQN` — obrigatório na DPS.",
    )
    iss_retencao = models.PositiveSmallIntegerField(
        choices=RetencaoIssqn.choices,
        default=RetencaoIssqn.NAO_RETIDO,
        help_text="`trib.tribMun.tpRetISSQN` — obrigatório na DPS.",
    )
    aliquota_iss = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "`trib.tribMun.pAliq` em % (ex.: 2.00). Vazio = não declarada — "
            "cabível para MEI/Simples, onde o recolhimento não é por alíquota "
            "de ISS na nota. Nunca preencher com valor 'padrão' chutado."
        ),
    )
    serie_dps = models.CharField(
        max_length=5,
        blank=True,
        default="1",
        help_text="Série da DPS (`serie`). Numeração sequencial é por prestador+série.",
    )
    ativo = models.BooleanField(default=True)
    # Empresa criada por `popular_carteira_demo` para demonstração.
    #
    # Existe porque dado de demonstração e dado real vivem no MESMO escritório —
    # e precisam viver, já que o contador só enxerga a carteira dele e um
    # segundo tenant ativo quebraria o roteamento do webhook por número. Sem uma
    # marca, "é de verdade?" viraria pergunta sem resposta no dia da reunião, e
    # a remoção teria que adivinhar quais linhas apagar.
    demonstracao = models.BooleanField(
        default=False,
        help_text="Empresa fictícia, criada para demonstração. Aparece marcada na carteira.",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    objects = ClienteManager()

    class Meta:
        verbose_name = "cliente"
        constraints = [
            # Unicidade por ESCRITÓRIO, não global: dois escritórios podem ter
            # o mesmo CNPJ/telefone na carteira (cliente que troca de contador,
            # ou que tem contador fiscal e trabalhista separados). O que não
            # pode é o mesmo escritório duplicar o cliente.
            models.UniqueConstraint(
                fields=["escritorio", "cnpj"], name="cliente_cnpj_unico_por_escritorio"
            ),
            # A unicidade de telefone saiu daqui de propósito (DEC-03): era ela
            # que impedia o sócio de duas empresas de existir no cadastro. A
            # unicidade que faz sentido — um número, uma pessoa — vive agora em
            # `Usuario`, e lá vale de verdade, porque o número é canonicalizado
            # antes de gravar (aqui, "5511999998888" e "551199998888" eram duas
            # strings diferentes e passavam pela constraint).
        ]

    def __str__(self):
        return f"{self.nome} ({self.cnpj})"

    # ------------------------------------------------------------------
    # Quem fala por esta empresa
    # ------------------------------------------------------------------
    @property
    def telefone_whatsapp(self) -> str:
        """Telefone do responsável principal — leitura, e só.

        Sobrevive como conveniência de exibição e de log, onde "o número da
        empresa" continua sendo o que a pessoa espera ler. Atribuir levanta
        `AttributeError`, que é o comportamento certo: uma empresa pode ter
        vários números, e um `cliente.telefone_whatsapp = x` silencioso
        reintroduziria a fonte de verdade paralela que o DEC-03 removeu.
        """
        vinculo = (
            self.vinculos.filter(ativo=True, usuario__ativo=True)
            .select_related("usuario")
            .order_by("-principal", "pk")
            .first()
        )
        return vinculo.usuario.telefone_whatsapp if vinculo else ""

    def vincular_usuario(self, numero, *, papel=None, principal=False, nome=""):
        """Liga um telefone a esta empresa, criando o `Usuario` se preciso.

        Idempotente: chamar de novo com o mesmo número atualiza o vínculo em vez
        de duplicar — importa porque `cadastrar_cliente` roda `update_or_create`
        e é normal reprocessar o mesmo cadastro.
        """
        canonico = telefone_br.canonico(numero)
        if not canonico:
            raise ValidationError(f"Telefone inválido: {numero!r}")

        with transaction.atomic():
            usuario, _ = Usuario.objects.get_or_create(
                escritorio=self.escritorio,
                telefone_whatsapp=canonico,
                defaults={"nome": nome},
            )
            if nome and not usuario.nome:
                usuario.nome = nome
                usuario.save(update_fields=["nome"])

            vinculo, _ = VinculoUsuarioCliente.objects.update_or_create(
                usuario=usuario,
                cliente=self,
                defaults={
                    "papel": papel or VinculoUsuarioCliente.Papel.RESPONSAVEL,
                    "principal": principal,
                    "ativo": True,
                },
            )
            if principal:
                # Um principal por empresa. Sem isto, dois principais fazem
                # `telefone_whatsapp` depender da ordem de inserção.
                self.vinculos.exclude(pk=vinculo.pk).filter(principal=True).update(
                    principal=False
                )
        return vinculo


class Usuario(models.Model):
    """A pessoa que fala pelo WhatsApp — sócio, financeiro, RH, contador.

    Pertence ao escritório, não à empresa: é o mesmo número atendendo várias
    empresas da carteira que motiva o nível existir. O vínculo com cada empresa
    é `VinculoUsuarioCliente`.

    **Não é `auth.User`.** Aquele é quem faz login no Grimório (o contador);
    este é quem manda mensagem no WhatsApp (o cliente final), e nunca tem senha
    nem sessão de admin. Manter os dois separados evita que uma mudança na
    autenticação do painel toque no atendimento — e vice-versa.
    """

    escritorio = models.ForeignKey(
        "tenants.Escritorio",
        on_delete=models.PROTECT,
        related_name="usuarios_whatsapp",
    )
    telefone_whatsapp = models.CharField(
        max_length=20,
        help_text=(
            "Número no formato internacional, ex.: 5511999998888. Gravado sempre "
            "na forma canônica (celular brasileiro COM o nono dígito) — é o que "
            "faz a unicidade valer de fato."
        ),
    )
    nome = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Como esta pessoa é chamada. Opcional — o número é que identifica.",
    )
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    clientes = models.ManyToManyField(
        Cliente, through="VinculoUsuarioCliente", related_name="usuarios"
    )

    objects = UsuarioManager()

    class Meta:
        verbose_name = "usuário do WhatsApp"
        verbose_name_plural = "usuários do WhatsApp"
        constraints = [
            models.UniqueConstraint(
                fields=["escritorio", "telefone_whatsapp"],
                name="usuario_telefone_unico_por_escritorio",
            ),
        ]

    def __str__(self):
        return f"{self.nome or 'sem nome'} ({self.telefone_whatsapp})"

    def save(self, *args, **kwargs):
        # Canonicalizar na gravação, não na leitura: é o que transforma a
        # constraint de unicidade em garantia real. Número não-brasileiro passa
        # intacto — `canonico` só mexe em celular do Brasil.
        canonico = telefone_br.canonico(self.telefone_whatsapp)
        if canonico and canonico != self.telefone_whatsapp:
            self.telefone_whatsapp = canonico
            campos = kwargs.get("update_fields")
            if campos is not None and "telefone_whatsapp" not in campos:
                kwargs["update_fields"] = [*campos, "telefone_whatsapp"]
        super().save(*args, **kwargs)

    def clientes_ativos(self):
        """Empresas de que esta pessoa fala, em ordem estável.

        Ordem por nome (é como o menu de desambiguação aparece para o cliente) e
        `pk` como desempate, para que a mesma lista saia igual entre duas
        mensagens — o número que a pessoa digita no menu depende disso.
        """
        return list(
            Cliente.objects.filter(
                vinculos__usuario=self, vinculos__ativo=True, ativo=True
            ).order_by("nome", "pk")
        )


class VinculoUsuarioCliente(models.Model):
    """Quem fala por qual empresa, e em que papel.

    O papel é informativo hoje — quem decide o que pode ser feito continua sendo
    `Perfil.tier_maximo` + `apps.governance`. Está aqui porque é a pergunta que
    o contador faz ao olhar a tela ("quem é esse número?"), e porque autorização
    por papel (o RH não emite nota) é evolução previsível: guardar o dado agora
    custa um campo, descobri-lo depois custa uma migração sem fonte.
    """

    class Papel(models.TextChoices):
        RESPONSAVEL = "responsavel", "Responsável"
        SOCIO = "socio", "Sócio"
        FINANCEIRO = "financeiro", "Financeiro"
        RH = "rh", "RH / Departamento pessoal"
        CONTADOR = "contador", "Contador"

    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="vinculos")
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="vinculos")
    papel = models.CharField(max_length=20, choices=Papel.choices, default=Papel.RESPONSAVEL)
    principal = models.BooleanField(
        default=False,
        help_text="Contato principal da empresa — é o número que aparece nas telas.",
    )
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "vínculo com empresa"
        verbose_name_plural = "vínculos com empresas"
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "cliente"], name="vinculo_usuario_cliente_unico"
            ),
        ]

    def __str__(self):
        return f"{self.usuario} → {self.cliente} ({self.get_papel_display()})"

    def clean(self):
        # Vínculo cruzando escritório seria vazamento de tenant pela porta do
        # cadastro — a RLS barraria a leitura, mas o erro apareceria longe daqui.
        if self.usuario.escritorio_id != self.cliente.escritorio_id:
            raise ValidationError(
                "Usuário e empresa precisam ser do mesmo escritório."
            )

    def save(self, *args, **kwargs):
        # `clean()` no `save()` foge do costume do Django, e é deliberado: aqui
        # ele não valida formulário, guarda fronteira de tenant. Deixar a
        # checagem só no ModelForm significaria que o admin protege e o shell,
        # o comando de cadastro e a migração de dados não.
        self.clean()
        super().save(*args, **kwargs)


class Perfil(models.Model):
    """Perfil de atendimento do cliente: persona, ferramentas e teto de tier.

    O motor de governança usa `tier_maximo` para recusar intenções acima do
    permitido (no piloto, ERP fica travado em Tier 0–1).
    """

    cliente = models.OneToOneField(
        Cliente, on_delete=models.CASCADE, related_name="perfil"
    )
    persona = models.CharField(max_length=40, default="lumen")
    ferramentas_habilitadas = models.JSONField(
        default=list,
        help_text='Adaptadores/ferramentas ativos, ex.: ["erp_mock", "nfse_mock"]',
    )
    tier_maximo = models.PositiveSmallIntegerField(default=1)
    valor_2fa_acima_de = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Emissões acima deste valor exigem código de 2FA por e-mail "
            "(apps/security). Vazio = 2FA desligado para este cliente."
        ),
    )

    class Meta:
        verbose_name = "perfil"
        verbose_name_plural = "perfis"

    def __str__(self):
        return f"Perfil de {self.cliente} (tier máx. {self.tier_maximo})"
