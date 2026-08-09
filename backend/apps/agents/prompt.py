"""
System prompt e schema de intenção montados **por tenant** (DEC-08 item 4).

Antes havia um prompt fixo no código, com as nove intenções escritas à mão. Isso
já divergia do catálogo real duas vezes ao longo do projeto, e ia divergir de
novo: quem acrescenta ferramenta lembra de registrá-la e esquece de contar ao
modelo que ela existe — e uma capacidade que o roteador nunca escolhe é uma
capacidade que não existe.

Aqui o prompt é **derivado** do catálogo, filtrado pelo que aquele cliente pode
de fato usar (`ferramentas.disponiveis_para`). Duas consequências que valem
dizer em voz alta:

- **O cliente sem ERP não vê consulta de estoque no prompt.** O modelo então não
  a escolhe, e a conversa não passa por "não consegui consultar" — ela cai em
  `desconhecida`, que responde o menu do que ele *pode* fazer.
- **O prompt encolhe com o tier.** Menos linha significa menos token de entrada
  em toda mensagem, e é aí que o prompt por tenant se paga.

**O que é cacheado e o que não é.** A parte derivada do catálogo é estável e fica
em cache por combinação de ferramentas — o número de combinações distintas é
pequeno (é uma função do tier e de ter ou não ERP), então o cache converge em
minutos. Os **exemplos de aprendizado** (`core.models.exemplos_para_prompt`) são
lidos do banco a cada chamada, de propósito: cadastrar um exemplo no admin
precisa valer na mensagem seguinte, sem deploy. Cachear os dois juntos
economizaria uma consulta barata e devolveria um comportamento que o time já
tinha resolvido.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel

from apps.agents import ferramentas
from apps.agents.registry import exposto_ao_modelo

# Regras que resolvem os casos ambíguos. Não dependem de tenant: são sobre a
# língua e sobre o custo de errar, não sobre o catálogo. Ficam fora da parte
# gerada para que acrescentar ferramenta não exija reescrevê-las.
_REGRAS = """\
Regras que resolvem os casos ambíguos:
1. As três intenções de nota usam quase as mesmas palavras. O que decide é o
   VERBO: criar → emitir_nota; ver/listar → consultar_nota; cancelar/anular →
   cancelar_nota. "quero emitir minha nota" é emitir_nota, apesar do "minha".
2. "receber" e "pagar" são intenções DIFERENTES — nunca junte as duas.
3. Na dúvida entre uma ação e uma consulta, escolha a CONSULTA: ler não muda
   nada, e emitir nota fiscal por engano tem custo real pro cliente.
4. Sem certeza razoável, responda desconhecida — o assistente pergunta de novo,
   o que é melhor que agir errado.
"""


@lru_cache(maxsize=64)
def _corpo(nomes: tuple[str, ...], escritorio: str) -> str:
    """Parte estável do prompt. Cacheada por combinação de ferramentas.

    `escritorio` entra na chave porque entra no texto — o nome do escritório dá
    ao modelo o enquadramento de quem ele atende. É dado do próprio tenant, não
    de outro, então dizê-lo ao modelo não é vazamento.
    """
    linhas = [
        f"Você classifica a intenção de mensagens de clientes do escritório "
        f"{escritorio}, atendidos por um assistente fiscal/financeiro no WhatsApp "
        f"(micro e pequenas empresas brasileiras).",
        "",
        "Responda com UMA intenção do schema:",
        "",
    ]
    for nome in nomes:
        ferramenta = ferramentas.obter(nome)
        if ferramenta is None:  # pragma: no cover - catálogo e nomes vêm juntos
            continue
        linha = f"- {nome}: {ferramenta.descricao}."
        if ferramenta.exemplos:
            linha += " Ex.: " + "; ".join(f'"{e}"' for e in ferramenta.exemplos) + "."
        linhas.append(linha)
    linhas.append("- desconhecida: nada acima, ou saudação/conversa solta.")
    linhas += ["", _REGRAS]
    return "\n".join(linhas)


def system_prompt_para(ctx) -> str:
    """Prompt do roteador para este cliente, com os exemplos aprendidos."""
    from apps.core.models import exemplos_para_prompt

    nomes = tuple(f.nome for f in ferramentas.disponiveis_para(ctx))
    escritorio = getattr(ctx.escritorio, "nome", "") or "seu escritório contábil"
    return _corpo(nomes, escritorio) + exemplos_para_prompt()


@lru_cache(maxsize=64)
def _schema(nomes: tuple[str, ...]) -> type[BaseModel]:
    """Schema de saída do roteador, restrito ao que este cliente pode usar.

    Gerado e não escrito à mão porque é a única forma de o schema não divergir
    do catálogo. E gerar não afrouxa a DEC-05: a classe passa por
    `@exposto_ao_modelo` como qualquer outra, então um nome de ferramenta que
    parecesse identificador de escopo derrubaria o processo aqui também.

    Restringir o `Literal` ao subconjunto do cliente é mais forte que pedir no
    prompt: o modelo fica **impedido** de devolver uma intenção que ele não pode
    executar, em vez de instruído a não tentar.
    """
    valores = (*nomes, "desconhecida")

    @exposto_ao_modelo
    class IntencaoClassificada(BaseModel):
        """Saída tipada do roteador — o núcleo decide o que fazer com cada valor."""

        intencao: Literal[valores]  # type: ignore[valid-type]

    return IntencaoClassificada


def schema_para(ctx) -> type[BaseModel]:
    return _schema(tuple(f.nome for f in ferramentas.disponiveis_para(ctx)))


def menu_de_capacidades(ctx) -> str:
    """O que o assistente responde quando não entendeu — na voz do produto.

    Lista o que este cliente pode, não o catálogo inteiro: oferecer consulta de
    estoque a quem não tem ERP conectado transforma a mensagem de "não entendi"
    numa promessa que a próxima mensagem quebra.
    """
    disponiveis = ferramentas.disponiveis_para(ctx)
    if not disponiveis:
        return (
            "Oi! Eu sou o Lumen, assistente da Magic BI. 💫\n"
            "Seu atendimento ainda está sendo configurado pelo seu contador — "
            "assim que ele liberar, eu te ajudo por aqui."
        )
    return (
        "Oi! Eu sou o Lumen, assistente da Magic BI. 💫\n"
        "Posso te ajudar com:\n"
        + "\n".join(f"• {f.descricao.capitalize()}" for f in disponiveis)
        + "\nÉ só perguntar!"
    )


def esquecer_cache() -> None:
    """Limpa os caches derivados do catálogo — para testes e para o shell."""
    _corpo.cache_clear()
    _schema.cache_clear()
