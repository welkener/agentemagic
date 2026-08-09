"""
Catálogo de ferramentas e `SessionContext` — o Sprint 2 (DEC-05 e DEC-08).

O que estes testes protegem, em ordem de importância:

1. **Escopo entra por `ctx` e por mais nada.** O registry já recusava campo de
   escopo no schema; agora existe o outro lado — a assinatura das ferramentas.
   Um handler que aceitasse `cliente_id` reabriria a porta que a DEC-05 fechou.
2. **Um tier, um lugar.** O `if/elif` conferia tier por handler, e o catálogo já
   divergiu do orquestrador uma vez em produção. Aqui a conferência é única e a
   exceção (cancelamento) é explícita e testada.
3. **O catálogo e o prompt vêm da mesma lista.** Ferramenta que entrasse só num
   dos dois viraria capacidade que o modelo pode devolver e não sabe usar.
"""
import inspect

import pytest

from apps.agents import ferramentas, prompt as prompt_tenant
from apps.agents.contexto import SessionContext
from apps.audit.models import Auditoria
from apps.clients.models import Cliente, Perfil
from apps.governance.tiers import CATALOGO_TIERS
from apps.tenants import rls
from apps.tenants.models import Escritorio


def ctx_de(cliente, **kwargs):
    return SessionContext.da_conversa(cliente=cliente, **kwargs)


# ---------------------------------------------------------------------------
# SessionContext
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestSessionContext:
    def test_deduz_o_escritorio_do_cliente(self, cliente):
        ctx = ctx_de(cliente)
        assert ctx.escritorio_id == cliente.escritorio_id
        assert ctx.perfil == cliente.perfil

    def test_sem_cliente_e_sem_usuario_recusa_montar(self):
        with pytest.raises(ValueError, match="exige escritório"):
            SessionContext.da_conversa()

    def test_cliente_de_outro_escritorio_e_erro_na_construcao(self, cliente):
        """Fronteira de tenant conferida onde o contexto nasce.

        Se passasse, o erro apareceria três camadas adiante como resultado
        errado — que é a forma cara de descobrir um vazamento.
        """
        with rls.escopo_irrestrito():
            outro = Escritorio.objects.create(nome="Contábil Vizinha", slug="vizinha")
        with pytest.raises(ValueError, match="incoerente"):
            SessionContext(escritorio=outro, cliente=cliente)

    def test_com_cliente_devolve_contexto_novo_e_reconfere(self, cliente):
        ctx = SessionContext(escritorio=cliente.escritorio)
        novo = ctx.com_cliente(cliente)
        assert novo is not ctx
        assert novo.cliente_id == cliente.pk

    def test_telefone_de_quem_escreve_prefere_o_wa_id(self, cliente):
        """Sob DEC-03 o telefone da empresa é o de *um* dos autorizados; quem
        responde "quem autorizou" é o número de quem escreveu."""
        ctx = ctx_de(cliente, wa_id="5511911112222")
        assert ctx.telefone_de_quem_escreve == "5511911112222"
        assert ctx_de(cliente).telefone_de_quem_escreve == cliente.telefone_whatsapp

    def test_trilha_nao_leva_conteudo_de_mensagem(self, cliente):
        """A trilha já guarda o conteúdo cifrado por titular. Uma segunda cópia
        aqui ficaria fora do alcance da eliminação por LGPD."""
        dados = ctx_de(cliente, wa_id="5511999998888").para_trilha()
        assert set(dados) == {"escritorio_id", "cliente_id", "usuario_id", "canal", "wa_id"}


# ---------------------------------------------------------------------------
# O contrato do catálogo
# ---------------------------------------------------------------------------
class TestContratoDoCatalogo:
    def test_nenhuma_ferramenta_recebe_escopo_por_parametro(self):
        """A metade prática da DEC-05.

        O registry impede `cliente_id` de existir no schema que o modelo
        preenche. Isto impede que ele apareça por outro caminho — um parâmetro
        do handler que alguém preencheria a partir da mensagem.
        """
        proibidos = {"cliente", "cliente_id", "cnpj", "escritorio", "escritorio_id", "tenant_id"}
        for nome, ferramenta in ferramentas.FERRAMENTAS.items():
            parametros = [
                p
                for p in inspect.signature(ferramenta.executar).parameters
                # Parâmetro com valor padrão fechando sobre a tabela do módulo
                # (ver `ferramentas/erp.py`) não vem de fora e não é escopo.
                if not p.startswith("_")
            ]
            assert parametros == ["ctx", "mensagem"], (
                f"{nome} deveria receber apenas (ctx, mensagem); recebeu {parametros}"
            )
            assert not proibidos & set(parametros)

    def test_toda_ferramenta_tem_tier_explicito_no_catalogo(self):
        """Nome ausente do catálogo cai no fail-safe Tier 3 e é recusado como se
        fosse destrutivo — foi o defeito de 26/jul/2026, agora impossível de
        repetir em silêncio."""
        faltando = sorted(set(ferramentas.nomes()) - set(CATALOGO_TIERS))
        assert not faltando, f"ferramentas sem tier declarado: {faltando}"

    def test_toda_ferramenta_tem_descricao_na_voz_do_produto(self):
        """A descrição vai literalmente para o prompt do tenant — o modelo roteia
        por ela. Vazia significaria uma capacidade que ele nunca escolhe."""
        for nome, ferramenta in ferramentas.FERRAMENTAS.items():
            assert ferramenta.descricao.strip(), f"{nome} sem descrição"
            assert not ferramenta.descricao.endswith("."), (
                f"{nome}: a descrição é encaixada numa frase, o ponto é do prompt"
            )

    def test_nome_repetido_e_erro_no_registro(self):
        """Duas capacidades disputando um nome significa que uma nunca roda."""
        with pytest.raises(ValueError, match="já registrada"):
            ferramentas.registrar_ferramenta("emitir_nota", descricao="duplicata")(
                lambda ctx, mensagem: ""
            )


# ---------------------------------------------------------------------------
# O que cada cliente enxerga
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestDisponibilidade:
    def test_sem_erp_conectado_as_consultas_de_erp_somem_do_catalogo(self, cliente):
        """Oferecer no prompt o que a execução vai recusar ensina o cliente a
        pedir o que não pode ter."""
        cliente.perfil.ferramentas_habilitadas = ["nfse_mock"]
        cliente.perfil.save()

        nomes = {f.nome for f in ferramentas.disponiveis_para(ctx_de(cliente))}
        assert "consultar_estoque" not in nomes
        assert "emitir_nota" in nomes  # não depende de ERP

    def test_com_erp_as_consultas_aparecem(self, cliente):
        nomes = {f.nome for f in ferramentas.disponiveis_para(ctx_de(cliente))}
        assert "consultar_estoque" in nomes

    def test_perfil_tier_zero_nao_ve_emissao(self, cliente):
        cliente.perfil.tier_maximo = 0
        cliente.perfil.save()

        nomes = {f.nome for f in ferramentas.disponiveis_para(ctx_de(cliente))}
        assert "emitir_nota" not in nomes
        assert "consultar_nota" in nomes

    def test_sem_empresa_em_foco_nao_ha_ferramenta_nenhuma(self, escritorio):
        ctx = SessionContext(escritorio=escritorio)
        assert ferramentas.disponiveis_para(ctx) == []


# ---------------------------------------------------------------------------
# Execução: tier, recusa e trilha
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestExecucao:
    def test_nome_desconhecido_devolve_none(self, cliente):
        assert ferramentas.executar("inventada", ctx_de(cliente)) is None

    def test_recusa_por_tier_usa_o_texto_da_propria_ferramenta(self, cliente):
        """"Consulta de notas não liberada" e "emissão não liberada" levam o
        cliente a pedidos diferentes ao contador — a mensagem genérica perderia
        essa diferença."""
        cliente.perfil.tier_maximo = 0
        cliente.perfil.save()

        resposta = ferramentas.executar("emitir_nota", ctx_de(cliente), "emite uma nota")
        assert "Emissão de nota fiscal" in resposta

    def test_recusa_por_tier_fica_na_trilha(self, cliente):
        cliente.perfil.tier_maximo = 0
        cliente.perfil.save()
        ferramentas.executar("emitir_nota", ctx_de(cliente), "emite")

        evento = Auditoria.objects.filter(evento="ferramenta_recusada_por_tier").first()
        assert evento is not None
        assert evento.dados["ferramenta"] == "emitir_nota"

    def test_execucao_bem_sucedida_registra_latencia(self, cliente):
        ferramentas.executar("consultar_nota", ctx_de(cliente), "minhas notas")

        evento = Auditoria.objects.get(evento="ferramenta_executada")
        assert evento.dados["ferramenta"] == "consultar_nota"
        assert isinstance(evento.dados["latencia_ms"], int)

    def test_pedido_de_cancelamento_nao_e_travado_pelo_tier(self, cliente):
        """Regressão do Sprint 2.

        `cancelar_nota` é Tier 3 no catálogo — e continua certo, porque *cancelar*
        é destrutivo. Mas esta ferramenta não cancela: ela abre pedido para o
        contador. Centralizar a conferência de tier travou isso por um momento, e
        o efeito seria o perfil comum ouvindo "não liberado" ao tentar avisar que
        uma nota saiu errada. A trava do cancelamento é do fluxo, não do tier.
        """
        assert CATALOGO_TIERS["cancelar_nota"] == 3
        assert cliente.perfil.tier_maximo == 1

        resposta = ferramentas.executar("cancelar_nota", ctx_de(cliente), "cancela")
        assert "não está liberada" not in resposta


# ---------------------------------------------------------------------------
# Prompt por tenant
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestPromptPorTenant:
    def test_prompt_lista_exatamente_o_que_o_cliente_pode(self, cliente):
        cliente.perfil.ferramentas_habilitadas = ["nfse_mock"]
        cliente.perfil.save()

        texto = prompt_tenant.system_prompt_para(ctx_de(cliente))
        assert "consultar_estoque" not in texto
        assert "emitir_nota" in texto
        assert "desconhecida" in texto  # a saída sempre existe

    def test_prompt_carrega_o_nome_do_escritorio(self, cliente):
        """Enquadramento para o modelo, e é dado do próprio tenant — dizê-lo não
        é vazamento."""
        assert cliente.escritorio.nome in prompt_tenant.system_prompt_para(ctx_de(cliente))

    def test_schema_restringe_o_que_o_modelo_pode_devolver(self, cliente):
        """Mais forte que pedir no prompt: sem ERP, o modelo fica IMPEDIDO de
        devolver `consultar_estoque`, em vez de instruído a não tentar."""
        cliente.perfil.ferramentas_habilitadas = ["nfse_mock"]
        cliente.perfil.save()

        schema = prompt_tenant.schema_para(ctx_de(cliente))
        valores = set(schema.model_fields["intencao"].annotation.__args__)
        assert "consultar_estoque" not in valores
        assert {"emitir_nota", "desconhecida"} <= valores

    def test_dois_perfis_diferentes_recebem_catalogos_diferentes(self, cliente, escritorio):
        with rls.escopo_irrestrito():
            enxuto = Cliente.objects.create(
                escritorio=escritorio,
                cnpj="99887766000155",
                nome="Loja Enxuta",
                email_contato="l@example.com",
                cnae_padrao="4712-1/00",
                ativo=True,
            )
            Perfil.objects.create(cliente=enxuto, tier_maximo=0, ferramentas_habilitadas=[])

        assert prompt_tenant.schema_para(ctx_de(cliente)) is not prompt_tenant.schema_para(
            ctx_de(enxuto)
        )

    def test_mesmo_catalogo_reaproveita_o_schema(self, cliente):
        """O cache é o que faz "prompt por tenant" não custar uma classe nova a
        cada mensagem."""
        primeiro = prompt_tenant.schema_para(ctx_de(cliente))
        assert prompt_tenant.schema_para(ctx_de(cliente)) is primeiro

    def test_menu_lista_so_o_que_o_cliente_pode(self, cliente):
        cliente.perfil.ferramentas_habilitadas = ["nfse_mock"]
        cliente.perfil.save()

        menu = prompt_tenant.menu_de_capacidades(ctx_de(cliente))
        assert "estoque" not in menu.lower()
        assert "nota fiscal" in menu.lower()

    def test_cliente_sem_perfil_recebe_recado_de_configuracao(self, cliente, escritorio):
        """Cliente cadastrado e ainda sem perfil de atendimento é o estado real
        do meio do onboarding. O motor de tiers já trata perfil ausente como
        "nada liberado" (fail-safe), e a conversa precisa dizer isso em vez de
        oferecer um menu vazio."""
        with rls.escopo_irrestrito():
            sem_perfil = Cliente.objects.create(
                escritorio=escritorio,
                cnpj="11223344000199",
                nome="Empresa Recém-Cadastrada",
                email_contato="r@example.com",
                ativo=True,
            )

        assert ferramentas.disponiveis_para(ctx_de(sem_perfil)) == []
        assert "configurado" in prompt_tenant.menu_de_capacidades(ctx_de(sem_perfil))
