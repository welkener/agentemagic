"""
O teste que a especificação Hermes chama de mais importante do sistema.

Não testa "a feature funciona" — testa que **nenhum schema exposto ao LLM
contém identificador de escopo**. É isso que impede "me mostra o faturamento do
concorrente": sem campo no schema, não há valor para o modelo preencher, e
nenhuma injeção de prompt inventa um.

A regra já valia por construção antes deste arquivo. O que faltava era alguém
verificar — e é a verificação, não a regra, que sobrevive à décima tool escrita
por outra pessoa em outro mês.

Três frentes, de propósito:
1. Os schemas que existem hoje estão limpos.
2. O guarda **pega** um schema sujo — inclusive por alias e aninhamento. Sem
   isto, um guarda quebrado passaria como se estivesse protegendo.
3. Ninguém consegue desviar do guarda (`criar_agente` é o único caminho).
"""
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

# Importar o orquestrador é o que popula SCHEMAS_EXPOSTOS — o registro acontece
# no import, pelo decorador.
from apps.core import orchestrator
from apps.agents.registry import (
    CAMPOS_DE_ESCOPO,
    SCHEMAS_EXPOSTOS,
    EscopoNoSchemaError,
    campos_de_escopo_em,
    criar_agente,
    exposto_ao_modelo,
)

APPS = Path(__file__).resolve().parent.parent / "apps"


# ---------------------------------------------------------------------------
# 1. O estado atual está limpo
# ---------------------------------------------------------------------------
def test_nenhum_schema_exposto_ao_modelo_contem_identificador_de_escopo():
    """O critério de aceite nº 2 da especificação, verificado.

    Percorre TODO schema registrado — não uma lista escrita à mão aqui, que
    envelheceria no primeiro schema novo.
    """
    sujos = {
        schema.__name__: campos_de_escopo_em(schema.model_json_schema())
        for schema in SCHEMAS_EXPOSTOS
    }
    sujos = {nome: campos for nome, campos in sujos.items() if campos}

    assert not sujos, (
        "Schema exposto ao LLM com identificador de escopo: "
        f"{sujos}. Escopo entra por `ctx: SessionContext`, resolvido no webhook."
    )


def test_o_registro_nao_esta_vazio():
    """Sem esta asserção, apagar o decorador de todo mundo deixaria o teste acima
    verde — um registro vazio não tem campo sujo nenhum. Verde por ausência de
    dado é o modo mais silencioso de um teste de segurança falhar."""
    assert len(SCHEMAS_EXPOSTOS) >= 2, SCHEMAS_EXPOSTOS


def test_os_schemas_do_orquestrador_estao_registrados():
    """Os dois que hoje chegam ao modelo: roteamento de intenção e extração
    de campos da nota."""
    registrados = {schema.__name__ for schema in SCHEMAS_EXPOSTOS}
    assert {"IntencaoClassificada", "DadosNotaExtraidos"} <= registrados


def test_extracao_da_nota_nao_pede_cnpj_nem_cnae_ao_modelo():
    """Guard de saída da emissão, agora verificado no schema.

    `cnpj_prestador` e `cnae` são montados pelo núcleo a partir do cadastro
    (`_iniciar_emissao`). Se algum dia virarem campo do schema, o modelo passa a
    escolher em nome de quem a nota sai — e é o tipo de mudança que parece
    conveniente na hora de escrever.
    """
    propriedades = orchestrator.DadosNotaExtraidos.model_json_schema()["properties"]
    assert set(propriedades) == {"tomador", "valor", "descricao_servico"}


# ---------------------------------------------------------------------------
# 2. O guarda pega schema sujo
# ---------------------------------------------------------------------------
def test_registro_recusa_campo_de_escopo_no_primeiro_nivel():
    with pytest.raises(EscopoNoSchemaError, match="cliente_id"):

        @exposto_ao_modelo
        class ToolMalEscrita(BaseModel):
            cliente_id: int
            competencia: str


@pytest.mark.parametrize(
    "nome_do_campo",
    ["tenant_id", "tenantId", "TENANT_ID", "empresa_id", "escritorio_id", "cnpj"],
)
def test_variacoes_de_grafia_do_mesmo_campo_sao_pegas(nome_do_campo):
    """`tenant_id` e `tenantId` são o mesmo buraco. O guarda normaliza antes de
    comparar — senão bastaria trocar o estilo de nomenclatura para escapar."""
    modelo = type(
        "ToolVariante",
        (BaseModel,),
        {"__annotations__": {nome_do_campo: str}},
    )
    with pytest.raises(EscopoNoSchemaError):
        exposto_ao_modelo(modelo)


def test_registro_recusa_campo_de_escopo_declarado_por_alias():
    """O modelo vê o JSON Schema, não o nome do atributo Python.

    Um campo `escopo` com `alias="cliente_id"` chega ao modelo como `cliente_id`
    — é essa forma que ele preenche. Por isso o guarda inspeciona o schema
    gerado, e não `model_fields`.
    """
    with pytest.raises(EscopoNoSchemaError, match="cliente_id"):

        @exposto_ao_modelo
        class ToolComAlias(BaseModel):
            escopo: str = Field(alias="cliente_id")


def test_registro_recusa_campo_de_escopo_aninhado():
    """Escondido dentro de um sub-modelo vaza igual a um de primeiro nível."""

    class Empresa(BaseModel):
        cnpj: str

    with pytest.raises(EscopoNoSchemaError):

        @exposto_ao_modelo
        class ToolAninhada(BaseModel):
            empresa: Empresa
            valor: float


def test_registro_recusa_campo_de_escopo_dentro_de_lista():
    class Item(BaseModel):
        empresa_id: int

    with pytest.raises(EscopoNoSchemaError):

        @exposto_ao_modelo
        class ToolComLista(BaseModel):
            itens: list[Item]


def test_schema_sujo_nao_entra_no_registro():
    """Recusar e registrar mesmo assim seria pior que não checar."""
    antes = list(SCHEMAS_EXPOSTOS)
    with pytest.raises(EscopoNoSchemaError):

        @exposto_ao_modelo
        class ToolRejeitada(BaseModel):
            tenant_id: int

    assert SCHEMAS_EXPOSTOS == antes


def test_cnpj_do_tomador_continua_permitido():
    """A ausência de `cnpj_tomador` na lista de proibidos é deliberada.

    O CNPJ de quem RECEBE o serviço é dado que o cliente informa na conversa
    ("emite pro CNPJ tal") — não é escopo. Um guarda que barrasse isso obrigaria
    a próxima pessoa a contorná-lo, e contorno de guarda de segurança vira norma.
    """

    @exposto_ao_modelo
    class NotaComTomador(BaseModel):
        cnpj_tomador: str
        valor: float

    assert NotaComTomador in SCHEMAS_EXPOSTOS
    SCHEMAS_EXPOSTOS.remove(NotaComTomador)  # não poluir os outros testes


def test_campos_de_escopo_aponta_o_caminho_do_achado():
    """A mensagem precisa dizer ONDE está o campo — num schema aninhado, saber
    só que "há um campo de escopo" não ajuda quem vai corrigir."""

    class Empresa(BaseModel):
        cnpj: str

    class Raiz(BaseModel):
        empresa: Empresa

    achados = campos_de_escopo_em(Raiz.model_json_schema())
    assert achados, "não achou o campo aninhado"
    assert any("cnpj" in caminho for caminho in achados)


# ---------------------------------------------------------------------------
# 3. Não há como desviar do guarda
# ---------------------------------------------------------------------------
def test_criar_agente_recusa_schema_nao_registrado():
    """Declarar o schema sem o decorador e mandar direto pro modelo é o desvio
    óbvio. `criar_agente` é a porta única, e ela confere o crachá."""

    class NuncaRegistrada(BaseModel):
        qualquer_coisa: str

    with pytest.raises(EscopoNoSchemaError, match="não está registrado"):
        criar_agente("groq:seja-la-qual-for", output_type=NuncaRegistrada, system_prompt="x")


def test_ninguem_constroi_agente_fora_do_registry():
    """Varre o código: `Agent(` de pydantic_ai só pode aparecer em registry.py.

    Sem esta varredura, a porta única não é única — bastaria importar
    `pydantic_ai.Agent` em qualquer módulo novo e o guarda deixaria de existir
    para aquele caminho, sem nenhum teste ficar vermelho.
    """
    padrao = re.compile(r"^\s*from\s+pydantic_ai\s+import\b|(?<![\w.])pydantic_ai\.Agent\(")
    infratores = []
    for arquivo in APPS.rglob("*.py"):
        if arquivo.name == "registry.py":
            continue
        for numero, linha in enumerate(
            arquivo.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if padrao.search(linha):
                infratores.append(f"{arquivo.relative_to(APPS.parent)}:{numero}")

    assert not infratores, (
        "pydantic_ai importado fora de apps/agents/registry.py — use "
        f"`criar_agente()`, que é o que confere o schema antes: {infratores}"
    )


def test_a_lista_de_campos_proibidos_cobre_o_que_a_spec_exige():
    """A especificação nomeia quatro. O guarda cobre esses e mais alguns —
    diminuir esta lista é decisão consciente, não descuido de refatoração."""
    from apps.agents.registry import _normalizar

    exigidos = {"tenant_id", "cliente_id", "cnpj", "empresa_id"}
    assert {_normalizar(nome) for nome in exigidos} <= CAMPOS_DE_ESCOPO
