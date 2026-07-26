"""
Ponto de partida de permissões do grupo de um escritório.

Decisão 26/jul/2026: **não existem papéis fixos no código** — a equipe Magic BI
monta as permissões de cada parceiro caso a caso, no admin de Grupos. O que
está aqui é só a linha de largada, para o escritório novo não nascer com um
grupo vazio (contador loga e não vê nada) nem com permissão demais.

O que este baseline **nunca** inclui, e por quê:

- `auth.*` (User/Group/Permission) — quem edita User edita `is_superuser`.
  Seria escalada de privilégio disfarçada de "cadastrar colega". O convite de
  colega passa pelo formulário de `MembroEscritorio` (`apps/tenants/admin.py`),
  que cria o usuário já no formato certo e sem tocar em permissões.
- `credentials.aplicativointegracao` — é o app OAuth da própria Magic BI,
  compartilhado por todos os tenants.
- `tenants.membroescritorio` — o acesso a essa tela é decidido pelo bit
  `responsavel`, não por permissão de model (ver `MembroEscritorioAdmin`).
- `admin.logentry`, `contenttypes`, `sessions` — encanamento do Django.

Lembrete importante: permissão **não** é isolamento. Dar permissão demais aqui
amplia o que a pessoa faz dentro do escritório dela; nunca a faz alcançar o
escritório do vizinho (isso é `MembroEscritorio` + `escopo.py`).
"""
from django.contrib.auth.models import Group, Permission

# (app_label, model, [ações]) — "*" = add/change/delete/view.
BASELINE = [
    ("clients", "cliente", "*"),
    ("clients", "perfil", "*"),
    ("credentials", "credencial", "*"),
    ("channel_evolution", "configuracaoevolution", "*"),
    # Só leitura: estes admins já bloqueiam escrita direta por design (a fila
    # de aprovação muda estado pelas ações, e a auditoria é append-only).
    ("agente_nf", "intencao", ["view"]),
    ("audit", "auditoria", ["view"]),
    ("security", "sessaowhatsapp", ["view"]),
    ("security", "tokenmagiclink", ["view"]),
    ("security", "codigo2fa", ["view"]),
    # O próprio escritório: marca, cores e canal. Criar/apagar tenant é da
    # Magic BI e está bloqueado no `EscritorioAdmin`, independente disto.
    ("tenants", "escritorio", ["view", "change"]),
]

ACOES_COMPLETAS = ["add", "change", "delete", "view"]


def permissoes_base() -> list[Permission]:
    encontradas = []
    for app_label, model, acoes in BASELINE:
        for acao in ACOES_COMPLETAS if acoes == "*" else acoes:
            permissao = Permission.objects.filter(
                content_type__app_label=app_label, codename=f"{acao}_{model}"
            ).first()
            if permissao is not None:
                encontradas.append(permissao)
    return encontradas


def aplicar_permissoes_base(grupo: Group) -> int:
    """Substitui as permissões do grupo pelo baseline. Devolve quantas ficaram."""
    permissoes = permissoes_base()
    grupo.permissions.set(permissoes)
    return len(permissoes)
