"""
Catálogo de ferramentas do agente.

Importar este pacote é o que **registra** as ferramentas — os módulos abaixo
rodam seus decoradores no import. Por isso todo mundo importa
`apps.agents.ferramentas`, nunca `apps.agents.ferramentas.base` direto: quem
importasse só a base encontraria um catálogo vazio e concluiria que o cliente
não pode fazer nada.
"""
from apps.agents.ferramentas.base import (  # noqa: F401
    FERRAMENTAS,
    RECUSA_PADRAO,
    Ferramenta,
    disponiveis_para,
    executar,
    nomes,
    obter,
    registrar_ferramenta,
)

# A ordem destes imports é a ordem em que as ferramentas aparecem no system
# prompt do tenant. Assunto por assunto ajuda um modelo de 8B mais do que
# ordem alfabética — as três de nota ficam juntas, e é entre elas que ele erra.
from apps.agents.ferramentas import fiscais  # noqa: F401,E402
from apps.agents.ferramentas import erp  # noqa: F401,E402
from apps.agents.ferramentas import rotina  # noqa: F401,E402
from apps.agents.ferramentas import atendimento  # noqa: F401,E402

__all__ = [
    "FERRAMENTAS",
    "RECUSA_PADRAO",
    "Ferramenta",
    "disponiveis_para",
    "executar",
    "nomes",
    "obter",
    "registrar_ferramenta",
]
