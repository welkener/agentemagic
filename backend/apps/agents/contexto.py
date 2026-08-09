"""
`SessionContext` — o único caminho pelo qual escopo entra numa ferramenta.

**A regra que este módulo torna executável** (DEC-05): nenhuma tool recebe
`cnpj`, `cliente_id` ou `escritorio_id` como parâmetro do modelo. O registry
(`apps/agents/registry.py`) já recusa esses campos no schema; o que faltava era
o outro lado da frase — *por onde eles entram então*. Entram por aqui, montados
no webhook a partir do número que **recebeu** a mensagem (tenant) e do número
que a **escreveu** (pessoa), nunca a partir do texto.

A diferença importa mais do que parece. Se a ferramenta lê o CNPJ de um campo
que o modelo preencheu, "emite a nota pelo CNPJ 11.222.333/0001-44" é um pedido
válido, e a única defesa vira o prompt. Se ela lê de `ctx.cliente`, a mesma
mensagem é só texto — não existe caminho pelo qual ela mude de quem é o dado.

**Congelado de propósito.** Um contexto mutável convidaria a "corrigir o
cliente no meio do fluxo", que é exatamente a operação que a desambiguação
(`apps/core/desambiguacao.py`) resolve antes, com o cliente confirmando. Trocar
de empresa é uma decisão de quem escreve, não um efeito colateral de uma tool.

**O que ele deliberadamente NÃO carrega:** nada de request, sessão de HTTP ou
objeto de canal. O mesmo contexto vale para a Cloud API, para o canal de teste e
para uma execução dentro de um teste — se ele dependesse do transporte, cada
canal novo reabriria a discussão de escopo.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

__all__ = ["SessionContext"]


@dataclass(frozen=True)
class SessionContext:
    """Quem está falando, por qual empresa, em nome de qual escritório.

    `cliente` pode ser nulo enquanto a empresa não foi resolvida (número
    desconhecido, ou pessoa com carteira múltipla que ainda não escolheu). Nesse
    estado nenhuma ferramenta roda — quem barra é `ferramentas.executar`, e não
    cada tool lembrando de conferir.
    """

    escritorio: object
    cliente: object | None = None
    usuario: object | None = None
    canal: str = "whatsapp"
    # Número de **quem escreveu**, que sob DEC-03 não é o número da empresa: uma
    # empresa tem vários autorizados. É contra ele que a sessão é conferida e é
    # ele que responde "quem autorizou esta nota" na trilha.
    wa_id: str = ""
    message_id: str | None = None

    def __post_init__(self):
        # Fronteira de tenant conferida na construção, não no uso. Um contexto
        # montado com cliente de um escritório e usuário de outro é um defeito
        # de isolamento, e ele precisa aparecer onde foi criado — não três
        # camadas adiante, dentro de uma tool, como resultado errado.
        for nome in ("cliente", "usuario"):
            objeto = getattr(self, nome)
            if objeto is None:
                continue
            if objeto.escritorio_id != self.escritorio_id:
                raise ValueError(
                    f"SessionContext incoerente: {nome} pertence ao escritório "
                    f"{objeto.escritorio_id}, o contexto é do {self.escritorio_id}."
                )

    # ------------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------------
    @classmethod
    def da_conversa(
        cls,
        *,
        cliente=None,
        usuario=None,
        escritorio=None,
        canal: str = "whatsapp",
        wa_id: str = "",
        message_id: str | None = None,
    ) -> "SessionContext":
        """Monta o contexto a partir do que o canal resolveu.

        O escritório é deduzido de quem estiver em mãos porque as três origens
        concordam por construção: o cliente pertence ao escritório do canal, e o
        usuário só é procurado dentro dele (`pipeline.processar`). Passar
        `escritorio` explicitamente continua valendo e é o caminho do webhook,
        onde ele vem do número que recebeu — a fonte mais confiável das três,
        porque não depende de o remetente estar cadastrado.
        """
        if escritorio is None:
            dono = cliente if cliente is not None else usuario
            if dono is None:
                raise ValueError(
                    "SessionContext exige escritório: sem cliente e sem usuário "
                    "não há de quem deduzi-lo."
                )
            escritorio = dono.escritorio
        return cls(
            escritorio=escritorio,
            cliente=cliente,
            usuario=usuario,
            canal=canal,
            wa_id=wa_id,
            message_id=message_id,
        )

    def com_cliente(self, cliente) -> "SessionContext":
        """Mesmo contexto, outra empresa — usado quando a desambiguação decide.

        Devolve um contexto novo em vez de mutar: o `__post_init__` roda de novo
        e a troca volta a passar pela conferência de tenant.
        """
        return replace(self, cliente=cliente)

    # ------------------------------------------------------------------
    # Atalhos de leitura
    # ------------------------------------------------------------------
    @property
    def escritorio_id(self) -> int | None:
        return getattr(self.escritorio, "id", None)

    @property
    def cliente_id(self) -> int | None:
        return getattr(self.cliente, "id", None)

    @property
    def perfil(self):
        """Perfil de atendimento da empresa em foco, ou None.

        Lido por atributo e não por consulta para não transformar cada tool numa
        ida ao banco; o `select_related` de quem monta o contexto é que decide o
        custo. Ausente significa cliente sem perfil cadastrado, e o motor de
        tiers já trata isso como "nada liberado" (fail-safe).
        """
        return getattr(self.cliente, "perfil", None) if self.cliente is not None else None

    @property
    def telefone_de_quem_escreve(self) -> str:
        """`wa_id`, com o telefone do responsável principal como último recurso.

        O recurso existe para o caminho de teste e para chamadas internas que não
        vêm de mensagem nenhuma. Em produção o `wa_id` está sempre preenchido, e
        é ele que vale — o telefone da empresa é o de *um* dos autorizados.
        """
        if self.wa_id:
            return self.wa_id
        return getattr(self.cliente, "telefone_whatsapp", "") if self.cliente else ""

    def para_trilha(self) -> dict:
        """Identificação do contexto para auditoria e log estruturado.

        Sem conteúdo de mensagem e sem nome de pessoa: a trilha já guarda o
        conteúdo em campo cifrado por titular, e repeti-lo aqui criaria uma
        segunda cópia fora do alcance da eliminação por LGPD.
        """
        return {
            "escritorio_id": self.escritorio_id,
            "cliente_id": self.cliente_id,
            "usuario_id": getattr(self.usuario, "id", None),
            "canal": self.canal,
            "wa_id": self.wa_id,
        }
