"""
Ferramentas de consulta ao ERP da empresa.

As cinco eram um laço sobre `_REGRAS_ERP` dentro do orquestrador: uma tabela de
palavras-chave que servia a dois propósitos ao mesmo tempo — classificar a
mensagem e descobrir qual recurso pedir ao adaptador. Separar os dois deixou
cada um no lugar certo: as palavras-chave viraram fallback determinístico
(`core/t0.py` e o classificador permissivo), e o recurso do adaptador virou o que
sempre foi, um detalhe da ferramenta.

Todas são Tier 0 (leitura) e todas dependem de integração conectada — daí
`exige_erp=True`, que as tira do catálogo de quem não tem ERP em vez de deixar o
cliente pedir e ouvir "não consegui consultar".
"""
from __future__ import annotations

from apps.agents.agente_erp.services import AgenteErp
from apps.agents.ferramentas.base import registrar_ferramenta

# nome da ferramenta → recurso no adaptador, com as frases que o cliente usa.
# "relatório"/"vendas" caem em pedidos — é a leitura mais próxima que o catálogo
# atual cobre pra um pedido genérico de relatório (ver
# docs/magicbi-ondas-desenvolvimento.md, achado do smoke test 25/jul/2026).
_CONSULTAS = (
    ("consultar_estoque", "estoque", "consultar o saldo de produtos em estoque",
     ("como está meu estoque?", "quantos produtos tenho?")),
    ("consultar_pedido", "pedidos", "consultar pedidos e vendas",
     ("quais os últimos pedidos?", "me manda o relatório de vendas")),
    ("consultar_contas_receber", "contas_receber", "consultar o que a empresa tem a receber",
     ("o que tenho a receber?", "quem me deve?")),
    ("consultar_contas_pagar", "contas_pagar", "consultar o que a empresa tem a pagar",
     ("o que tenho a pagar?", "quais contas vencem essa semana?")),
    ("consultar_fluxo_caixa", "fluxo_caixa", "consultar saldo e previsão de caixa",
     ("como está meu caixa?", "qual meu saldo?")),
)


def _registrar_consultas():
    """Registra as cinco de uma vez.

    Escrever cinco funções idênticas trocando uma string seria mais fácil de ler
    linha a linha e mais fácil de divergir: bastaria uma delas esquecer o
    `exige_erp` para o catálogo passar a prometer o que não entrega. A tabela
    acima é a fonte única, e é ela que o teste percorre.
    """
    for nome, recurso, descricao, exemplos in _CONSULTAS:

        def handler(ctx, mensagem: str, _recurso=recurso, _nome=nome) -> str:
            # `_recurso`/`_nome` como padrão do parâmetro, e não capturados do
            # laço: capturar deixaria as cinco apontando para a última iteração,
            # e o cliente pediria estoque para receber fluxo de caixa.
            return AgenteErp().consultar(
                intencao=_nome,
                recurso=_recurso,
                filtros={},
                perfil=ctx.perfil,
                cliente=ctx.cliente,
            )

        handler.__name__ = nome
        registrar_ferramenta(
            nome, descricao=descricao, exige_erp=True, exemplos=exemplos
        )(handler)


_registrar_consultas()
