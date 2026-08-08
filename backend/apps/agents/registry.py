"""
Registro dos schemas que o LLM enxerga — e o guarda que os inspeciona.

**A regra que este módulo protege** (DEC-05, `docs/hermes-contabil-decisoes.md`):

    Nenhuma tool recebe escopo como parâmetro do modelo.

    # ERRADO
    def consultar_das(cnpj: str, competencia: str): ...

    # CERTO
    def consultar_das(competencia: str, *, ctx: SessionContext): ...

`tenant_id`, `cliente_id`, `cnpj` e `empresa_id` entram por `ctx`, resolvido no
webhook a partir do número que recebeu a mensagem. Nunca pelo modelo. É isso que
impede "me mostra o faturamento do concorrente": se o campo não existe no schema,
não há valor para o modelo preencher, e nenhuma injeção de prompt cria um.

Até 08/ago/2026 a regra valia **por construção** — o único schema exposto tinha
tomador/valor/descrição, e o CNPJ vinha do cadastro. Mas nada verificava, e
"verdade por sorte de projeto" não sobrevive à décima tool escrita em outro mês.

Duas travas, de propósito:

1. `@exposto_ao_modelo` valida **no import**. Um schema com campo de escopo
   derruba o processo ao subir, não numa requisição em produção.
2. `criar_agente()` recusa `output_type` não registrado. Isso fecha o desvio
   óbvio — declarar um schema sem o decorador e mandar direto pro modelo.

O que este módulo NÃO faz: inspecionar `description` dos campos. Uma descrição
que mencione CNPJ é inofensiva — o modelo não tem onde escrever. O que importa é
o nome do campo, porque é ele que vira chave no JSON que o modelo devolve.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

__all__ = [
    "CAMPOS_DE_ESCOPO",
    "EscopoNoSchemaError",
    "SCHEMAS_EXPOSTOS",
    "campos_de_escopo_em",
    "criar_agente",
    "exposto_ao_modelo",
]


# Nomes normalizados (minúsculo, sem `_`/`-`) que identificam ESCOPO — quem é o
# tenant, quem é a empresa. Um campo com qualquer um destes nomes deixaria o
# modelo escolher de quem é o dado que ele vai ler.
#
# `cnpjtomador` NÃO está aqui, e a ausência é deliberada: o CNPJ de quem RECEBE o
# serviço é dado que o cliente informa na conversa ("emite pro CNPJ tal"), não é
# escopo. Já `cnpjprestador` é a própria empresa — vem do cadastro, nunca do
# modelo (ver `orchestrator._iniciar_emissao`, que monta esse campo no núcleo).
#
# Acrescentar nome a esta lista é barato e não quebra nada existente. Preferir
# errar para o lado de incluir.
CAMPOS_DE_ESCOPO = frozenset(
    {
        "tenantid",
        "clienteid",
        "empresaid",
        "escritorioid",
        "idtenant",
        "idcliente",
        "idempresa",
        "idescritorio",
        "cnpj",
        "cnpjprestador",
        "cnpjemitente",
        "cpfcnpj",
        "inscricaomunicipal",
        "tenant",
        "escritorio",
    }
)

# Chaves do JSON Schema cujo valor é um sub-schema (ou lista deles). Percorrer
# todas importa: um campo de escopo escondido dentro de um item de lista ou de um
# `$defs` de modelo aninhado vaza igual a um de primeiro nível.
_CHAVES_ANINHADAS = ("items", "additionalProperties", "not", "contains")
_CHAVES_LISTA = ("anyOf", "oneOf", "allOf", "prefixItems")


class EscopoNoSchemaError(RuntimeError):
    """Schema exposto ao modelo contém identificador de escopo."""


def _normalizar(nome: str) -> str:
    """`tenant_id`, `tenantId` e `TENANT-ID` são o mesmo campo para este guarda."""
    return re.sub(r"[^a-z0-9]", "", nome.lower())


def campos_de_escopo_em(schema: dict, _caminho: str = "", _vistos: set | None = None) -> list[str]:
    """Caminhos dos campos de escopo encontrados no JSON Schema. Vazio = limpo.

    Trabalha sobre `model_json_schema()` e não sobre `model_fields` de propósito:
    o que chega ao modelo é o JSON Schema, então **alias vence nome de atributo**.
    Um campo `escopo: str = Field(alias="cliente_id")` aparece no schema como
    `cliente_id` — e é essa forma que o modelo vê e preenche.
    """
    _vistos = _vistos if _vistos is not None else set()
    identidade = id(schema)
    if identidade in _vistos:
        # Schema recursivo (modelo que se referencia). Já visitado, não repetir.
        return []
    _vistos.add(identidade)

    achados: list[str] = []

    for nome, subschema in (schema.get("properties") or {}).items():
        caminho = f"{_caminho}.{nome}" if _caminho else nome
        if _normalizar(nome) in CAMPOS_DE_ESCOPO:
            achados.append(caminho)
        if isinstance(subschema, dict):
            achados.extend(campos_de_escopo_em(subschema, caminho, _vistos))

    # `$defs` guarda os modelos aninhados; sem descer aqui, um campo de escopo
    # dentro de um sub-modelo passa despercebido.
    for nome, subschema in (schema.get("$defs") or {}).items():
        if isinstance(subschema, dict):
            achados.extend(campos_de_escopo_em(subschema, f"{_caminho}[{nome}]", _vistos))

    for chave in _CHAVES_ANINHADAS:
        subschema = schema.get(chave)
        if isinstance(subschema, dict):
            achados.extend(campos_de_escopo_em(subschema, _caminho, _vistos))

    for chave in _CHAVES_LISTA:
        for subschema in schema.get(chave) or []:
            if isinstance(subschema, dict):
                achados.extend(campos_de_escopo_em(subschema, _caminho, _vistos))

    return achados


# Todo schema que o modelo enxerga. Preenchido pelo decorador, no import.
SCHEMAS_EXPOSTOS: list[type[BaseModel]] = []


def exposto_ao_modelo(cls: type[BaseModel]) -> type[BaseModel]:
    """Registra o schema e recusa, no import, qualquer campo de escopo.

    Falhar no import é escolha consciente: um schema com `cliente_id` é um
    defeito de isolamento, e sistema fiscal não pode subir "meio certo" nisso.
    O desenvolvedor vê o erro ao rodar o projeto, não o cliente em produção.
    """
    achados = campos_de_escopo_em(cls.model_json_schema())
    if achados:
        raise EscopoNoSchemaError(
            f"{cls.__name__} expõe identificador de escopo ao modelo: "
            f"{', '.join(achados)}. Escopo entra por `ctx: SessionContext`, "
            f"resolvido no webhook — nunca como campo que o LLM preenche. "
            f"Ver DEC-05 em docs/hermes-contabil-decisoes.md."
        )
    if cls not in SCHEMAS_EXPOSTOS:
        SCHEMAS_EXPOSTOS.append(cls)
    return cls


def criar_agente(modelo: str, *, output_type: type[BaseModel], system_prompt: str):
    """Único caminho autorizado para instanciar um agente Pydantic AI.

    Existe para fechar o desvio: sem isto, declarar um schema sem o decorador e
    passá-lo direto a `Agent(...)` colocaria um campo de escopo na frente do
    modelo sem passar por guarda nenhum. `tests/test_tool_registry.py` verifica
    que ninguém constrói `Agent(` fora daqui.

    O import de `pydantic_ai` é tardio de propósito — o orquestrador cai no
    roteamento determinístico quando não há Groq configurado, e nesse caminho a
    biblioteca não precisa nem ser carregada.
    """
    if output_type not in SCHEMAS_EXPOSTOS:
        raise EscopoNoSchemaError(
            f"{output_type.__name__} não está registrado como exposto ao modelo. "
            f"Decore-o com `@exposto_ao_modelo` (apps/agents/registry.py) — é o "
            f"que garante que ele foi inspecionado contra campos de escopo."
        )

    from pydantic_ai import Agent

    return Agent(modelo, output_type=output_type, system_prompt=system_prompt)
