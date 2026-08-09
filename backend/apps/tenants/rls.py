"""
Row Level Security — a rede embaixo do escopo de aplicação (DEC-04).

O isolamento entre escritórios já existia em duas camadas: o admin filtra por
`EscopoEscritorioMixin` e o webhook resolve o tenant pelo número que recebeu a
mensagem. As duas funcionam — e as duas dependem de todo código futuro lembrar
de filtrar. Um `.objects.filter()` sem tenant em código novo devolve a carteira
do escritório vizinho, e nada acusa.

Com RLS o mesmo esquecimento devolve **zero linhas**. É proposital que a falha
seja "não vejo nada" em vez de "vejo demais": a primeira aparece no primeiro
teste, a segunda aparece num vazamento.

**Como o Postgres decide.** A policy compara o dono da linha com
`current_setting('app.tenant_id')`, setado por quem entra no sistema:

| Entrada | Quem seta | Onde |
|---|---|---|
| Requisição HTTP | middleware | `apps/tenants/middleware.py` |
| Task Celery | sinal `task_prerun` | `config/celery.py` |
| Equipe Magic BI (superuser) | `app.irrestrito = on` | middleware |
| Fixture de teste / migração de dados | `escopo_irrestrito()` | explícito, no código |

**Por que `SET ROLE` e não um usuário de banco separado.** Superuser do Postgres
ignora RLS — sempre, inclusive com `FORCE ROW LEVEL SECURITY` (verificado, não
deduzido). Django usa uma conexão só para migrar e para consultar, então um
usuário restrito no `DATABASE_URL` não conseguiria rodar as migrações. `SET
LOCAL ROLE` resolve os dois lados: a conexão autentica como dono (migra), e cada
requisição/teste passa a valer como `magicbi_app`, que não tem `BYPASSRLS`. No
fim da transação o papel volta sozinho.

**Custo conhecido.** As tabelas que penduram no cliente checam
`cliente_id IN (SELECT ... WHERE escritorio_id = ...)`. O Postgres resolve como
semi-join sobre o índice de `escritorio_id`; no volume alvo (1.000 empresas por
escritório) é barato. Se um dia doer, o caminho é desnormalizar `escritorio_id`
nas tabelas-filhas — não afrouxar a policy.
"""
from __future__ import annotations

from contextlib import contextmanager

from django.db import connection

# Papel sem BYPASSRLS que a aplicação assume. Criado pela migração — nunca à mão,
# senão o servidor sobe com RLS inerte e ninguém percebe.
PAPEL_APLICACAO = "magicbi_app"

# `true` no segundo argumento = "não exploda se a variável nunca foi setada".
# Sem ele, uma conexão limpa levantaria erro em vez de simplesmente não ver nada.
_TENANT = "nullif(current_setting('app.tenant_id', true), '')::bigint"
_IRRESTRITO = "current_setting('app.irrestrito', true) = 'on'"

# Tabelas de domínio → como cada uma prova a quem pertence.
#
# Escrito à mão, e não descoberto por introspecção, de propósito: uma tabela nova
# tem que ser uma DECISÃO, não um efeito colateral. `tests/test_rls.py` falha
# quando aparece tabela nova que não está nem aqui nem na lista de exceções.
_POR_ESCRITORIO = "escritorio_id = {tenant}"
_POR_CLIENTE = (
    "cliente_id IN (SELECT id FROM clients_cliente WHERE escritorio_id = {tenant})"
)
# O nível `usuario` (DEC-03) pendura no escritório, não no cliente — é o mesmo
# número podendo falar por várias empresas que motiva o nível existir.
_POR_USUARIO = (
    "usuario_id IN (SELECT id FROM clients_usuario WHERE escritorio_id = {tenant})"
)

TABELAS: dict[str, str] = {
    # O próprio tenant.
    "tenants_escritorio": "id = {tenant}",
    "tenants_membroescritorio": _POR_ESCRITORIO,
    # Raiz da carteira.
    "clients_cliente": _POR_ESCRITORIO,
    # Quem fala no WhatsApp, e por quais empresas (DEC-03). O vínculo prova o
    # tenant pelos DOIS lados; escolhi o cliente porque é o mesmo predicado do
    # resto da carteira e o par (usuario, cliente) já é obrigado a ser do mesmo
    # escritório em `VinculoUsuarioCliente.clean`.
    "clients_usuario": _POR_ESCRITORIO,
    "clients_vinculousuariocliente": _POR_CLIENTE,
    # Canal de teste: `escritorio_id` nulo é configuração da plataforma, visível
    # a todos de propósito (é o fallback que o `configuracao_ativa` já usa).
    "channel_evolution_configuracaoevolution": (
        "escritorio_id IS NULL OR escritorio_id = {tenant}"
    ),
    # Tudo que pendura no cliente.
    "clients_perfil": _POR_CLIENTE,
    "credentials_credencial": _POR_CLIENTE,
    "security_sessaowhatsapp": _POR_CLIENTE,
    "security_empresaemfoco": _POR_USUARIO,
    "security_tokenmagiclink": _POR_CLIENTE,
    "security_codigo2fa": _POR_CLIENTE,
    "fiscal_seriedps": _POR_CLIENTE,
    "agente_nf_intencao": _POR_CLIENTE,
    "atendimento_solicitacao": _POR_CLIENTE,
    "audit_chaveconteudo": _POR_CLIENTE,
    # Consumo de IA pendura no escritório, não no cliente: `cliente_id` é nulo
    # quando o roteador roda antes de a empresa estar resolvida, e quem paga a
    # conta é o tenant de qualquer forma.
    "observabilidade_consumollm": _POR_ESCRITORIO,
    # Auditoria aceita evento sem dono (`cliente_id` nulo) — número desconhecido
    # que escreveu para o canal, por exemplo. Esses são eventos de PLATAFORMA e
    # não podem carregar dado de tenant nenhum; quem chama `audit.registrar` sem
    # cliente está afirmando isso.
    "audit_auditoria": f"cliente_id IS NULL OR {_POR_CLIENTE}",
}

# Tabelas sem policy, com o motivo. Estar aqui é decisão registrada, não
# esquecimento — o teste exige que toda tabela esteja num dos dois lugares.
SEM_TENANT: dict[str, str] = {
    "core_exemplointencao": (
        "Corpus de exemplos do roteador — curado e despersonalizado, compartilhado "
        "por todos os tenants (ver decisão de 27/jul/2026 sobre RAG)."
    ),
    "credentials_aplicativointegracao": (
        "App OAuth de desenvolvedor da Magic BI, um só para a plataforma inteira."
    ),
    "channel_whatsapp_mensagemprocessada": (
        "Idempotência por `message_id` do Meta — o id é global e a checagem "
        "acontece ANTES de o tenant ser resolvido. Não contém dado do cliente."
    ),
}


def _predicado(tabela: str) -> str:
    return TABELAS[tabela].format(tenant=_TENANT)


def sql_para_tabela(tabela: str) -> list[str]:
    """SQL que liga a RLS numa tabela. Idempotente — pode rodar de novo."""
    predicado = f"({_IRRESTRITO}) OR ({_predicado(tabela)})"
    return [
        f"ALTER TABLE {tabela} ENABLE ROW LEVEL SECURITY;",
        f"DROP POLICY IF EXISTS isolamento_tenant ON {tabela};",
        # Sem `WITH CHECK` explícito o Postgres reaproveita o `USING` na escrita —
        # que é o comportamento desejado: ler e escrever obedecem à mesma regra,
        # então ninguém insere linha para o vizinho (verificado).
        f"CREATE POLICY isolamento_tenant ON {tabela} USING ({predicado});",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tabela} TO {PAPEL_APLICACAO};",
    ]


def sql_completo(tabelas=None) -> str:
    """Migração inteira: papel, grants e policies.

    `tabelas` existe porque migração é registro histórico, não estado atual.
    Renderizar sempre o mapa vivo funcionou enquanto havia uma migração de RLS
    só; na segunda (`0004_rls_nivel_usuario`) a tabela nova apareceu no SQL da
    **primeira**, que roda antes de ela existir, e o `migrate` quebrou num banco
    limpo — sem sintoma nenhum no ambiente onde tudo já estava criado. Cada
    migração passa a congelar a lista que existia no dia dela; quem garante que
    nenhuma tabela ficou de fora é `tests/test_rls.py`, comparando o mapa vivo
    com o que o banco tem de policy.
    """
    tabelas = list(TABELAS) if tabelas is None else list(tabelas)
    linhas = [
        # NOLOGIN: ninguém conecta como ele. O acesso vem por `SET ROLE` a partir
        # da conexão do Django, o que mantém uma credencial só para administrar.
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{PAPEL_APLICACAO}') THEN
                CREATE ROLE {PAPEL_APLICACAO} NOLOGIN;
            END IF;
        END $$;
        """,
        f"GRANT USAGE ON SCHEMA public TO {PAPEL_APLICACAO};",
        # Permissão em TUDO, inclusive nas tabelas do próprio Django (`auth_user`,
        # sessão, permissões, log do admin). Não é contradição com o isolamento:
        # GRANT diz o que o papel pode OPERAR, policy diz quais LINHAS ele
        # enxerga. Sem o grant amplo, o papel nem consegue autenticar alguém — e
        # o erro sai como "permissão negada para tabela auth_user", longe da
        # causa. Quem isola são as policies abaixo, e só elas.
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {PAPEL_APLICACAO};",
        # Sequências: sem isto o papel lê e filtra certo, mas não consegue INSERT
        # (não gera id) — e o erro também aparece longe da causa.
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {PAPEL_APLICACAO};",
        # `ALL TABLES` só alcança o que existe agora. Toda migração futura cria
        # tabela sem grant nenhum, e o sintoma apareceria semanas depois, num
        # deploy, como "permissão negada" numa tela que nunca foi tocada.
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {PAPEL_APLICACAO};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {PAPEL_APLICACAO};",
    ]
    for tabela in tabelas:
        linhas.extend(sql_para_tabela(tabela))
    return "\n".join(linhas)


def sql_reverso(tabelas=None) -> str:
    """Desfaz as policies. O papel fica — apagar papel com grants pendurados
    falharia, e um papel órfão não faz mal."""
    tabelas = list(TABELAS) if tabelas is None else list(tabelas)
    linhas = []
    for tabela in tabelas:
        linhas.append(f"DROP POLICY IF EXISTS isolamento_tenant ON {tabela};")
        linhas.append(f"ALTER TABLE {tabela} DISABLE ROW LEVEL SECURITY;")
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Quem entra no sistema declara o escopo por aqui
# ---------------------------------------------------------------------------
def _local() -> str:
    """`SET LOCAL` dentro de transação, `SET` fora dela.

    `SET LOCAL` é o que se quer sempre: volta sozinho no commit ou no rollback,
    então nem um erro no meio da requisição deixa o papel e o tenant vazando
    para a próxima que reusar a conexão. Mas fora de transação ele **não faz
    nada** — o Postgres só emite um aviso, e o resultado seria RLS ligada com
    tenant nunca setado, ou seja, tela vazia sem explicação.

    Requisição HTTP roda dentro de `transaction.atomic` (ver `middleware.py`);
    task do Celery não, e por isso lá o reset é explícito no `task_postrun`.
    """
    return "SET LOCAL" if connection.in_atomic_block else "SET"


def _executar(sql: str, *params) -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql, params or None)


def _definir(variavel: str, valor: str) -> None:
    # Nome de GUC não pode ser placeholder; o valor pode e deve.
    _executar(f"{_local()} {variavel} = %s", valor)


def assumir_papel_restrito() -> None:
    """Passa a valer como `magicbi_app`, que não tem `BYPASSRLS`.

    Enquanto a conexão for do dono das tabelas, a RLS é decorativa. É esta
    chamada que a torna real — e é por isso que ela mora nas duas portas de
    entrada (middleware e `task_prerun`), não espalhada pelo código.
    """
    _executar(f"{_local()} ROLE {PAPEL_APLICACAO}")


def definir_tenant(escritorio_id: int) -> None:
    """Fixa o tenant até o fim da transação (ou até `limpar_escopo`).

    Versão sem bloco para quem não tem um: o `task_prerun` do Celery abre o
    escopo e o `task_postrun` fecha, porque entre os dois está a task inteira.
    """
    _definir("app.tenant_id", str(escritorio_id))


def definir_irrestrito(ligado: bool) -> None:
    """Liga/desliga o irrestrito sem bloco — para as portas de entrada."""
    _definir("app.irrestrito", "on" if ligado else "off")


def escopo_atual() -> tuple[int | None, str]:
    """Fotografia do escopo, para devolver depois (`restaurar_escopo`)."""
    return tenant_atual(), _irrestrito_atual()


def restaurar_escopo(anterior: "tuple[int | None, str] | None") -> None:
    """Devolve o escopo fotografado por `escopo_atual`.

    Restaurar, e não `RESET`: dentro de uma transação (Celery eager num teste) o
    reset apagaria o escopo de quem chamou, e as consultas seguintes voltariam
    vazias sem explicação. Fora de transação o anterior é vazio, e restaurar dá
    no mesmo que resetar — que é o que o worker precisa entre uma task e outra.

    O papel só é devolvido fora de transação: dentro dela o `SET LOCAL ROLE`
    volta sozinho no fim, e um `RESET ROLE` aqui devolveria poder de dono ao
    resto do teste — justamente o que tornaria a RLS decorativa de novo.
    """
    tenant, irrestrito = anterior or (None, "off")
    if not connection.in_atomic_block:
        _executar("RESET ROLE")
    _definir("app.tenant_id", str(tenant) if tenant else "")
    _definir("app.irrestrito", irrestrito)


@contextmanager
def escopo_de_tenant(escritorio_id: int):
    """Delimita as consultas ao escritório informado.

    Desliga `app.irrestrito` junto, e isso não é detalhe: "estou operando em nome
    do escritório X" e "enxergo a plataforma toda" são afirmações contraditórias.
    Quem entra num escopo de tenant sai do irrestrito, mesmo que quem chamou
    estivesse nele — é o que faz uma requisição dentro de um teste valer como
    requisição de verdade, e não herdar a permissão que montou o cenário.
    """
    anterior_tenant = tenant_atual()
    anterior_irrestrito = _irrestrito_atual()
    _definir("app.tenant_id", str(escritorio_id))
    _definir("app.irrestrito", "off")
    try:
        yield
    finally:
        _definir("app.tenant_id", str(anterior_tenant) if anterior_tenant else "")
        _definir("app.irrestrito", anterior_irrestrito)


def _irrestrito_atual() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.irrestrito', true)")
        linha = cursor.fetchone()
    return (linha[0] if linha else None) or "off"


@contextmanager
def escopo_irrestrito():
    """Suspende a RLS para este bloco. Use com parcimônia e por motivo escrito.

    Quatro usos legítimos, e só eles:
    - equipe Magic BI (superuser) enxergando a plataforma inteira;
    - migração de dados, que roda antes de haver tenant no contexto;
    - fixture de teste montando o cenário de dois escritórios;
    - resolver o tenant, ou checar colisão global, antes de haver escopo — o
      webhook descobrindo de quem é a mensagem pelo número que recebeu, o
      convite conferindo se o usuário já pertence a algum escritório. Nos dois
      a pergunta é de plataforma, e a resposta devolvida ao tenant não diz de
      quem é o dado, só que ele existe.

    Não é atalho para "a consulta não achou nada": se uma tela ficou vazia, o que
    falta é o tenant no contexto, e é isso que precisa ser corrigido.

    Restaura o valor anterior em vez de desligar no fim: aninhar dois blocos
    destes — fixture dentro de fixture — não pode deixar o de fora sem escopo.
    """
    anterior = _irrestrito_atual()
    _definir("app.irrestrito", "on")
    try:
        yield
    finally:
        _definir("app.irrestrito", anterior)


def tenant_atual() -> int | None:
    """Tenant em vigor nesta conexão, ou None. Serve a log e diagnóstico."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT nullif(current_setting('app.tenant_id', true), '')")
        linha = cursor.fetchone()
    return int(linha[0]) if linha and linha[0] else None
