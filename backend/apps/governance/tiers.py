"""
Motor de tiers (mínimo do MVP).

Escala de risco (arquitetura, seções 5 e 6):
- Tier 0: consultas (leitura)
- Tier 1: rascunhos e emissão de NFS-e (com aprovação da 1ª emissão)
- Tier 2: alterações em registros existentes
- Tier 3: ações destrutivas/financeiras (excluir, pagar, cancelar)

Intenções acima do `tier_maximo` do perfil do cliente são RECUSADAS —
sempre com explicação educada na camada de conversa.
"""

# Catálogo: nome da intenção → tier exigido.
#
# ⚠ Os nomes aqui TÊM que ser exatamente os que o orquestrador emite. Nome que
# não bate cai no fail-safe Tier 3 e a intenção é recusada como se fosse
# destrutiva. Foi o que aconteceu com contas a pagar/receber até 26/jul/2026:
# o catálogo dizia `consultar_contas`, o orquestrador emitia
# `consultar_contas_receber`/`_pagar`, e as duas consultas responderam
# "operação não liberada para o seu perfil" em produção. `tests/test_governance.py`
# agora cruza este dicionário com o orquestrador pra isso não repetir.
CATALOGO_TIERS: dict[str, int] = {
    # Tier 0 — consultas
    "consultar_estoque": 0,
    "consultar_pedido": 0,
    "consultar_contas_receber": 0,
    "consultar_contas_pagar": 0,
    "consultar_fluxo_caixa": 0,
    "consultar_nota": 0,
    # Leitura do próprio faturamento contra o teto do MEI. Tier 0 porque não
    # muda nada e o dado é do próprio cliente — e porque avisar cedo sobre
    # estouro de teto é justamente o que evita o desenquadramento retroativo.
    "consultar_faturamento_acumulado": 0,
    # Tier 1 — rascunho e emissão
    "criar_rascunho": 1,
    "criar_rascunho_pedido": 1,
    "emitir_nota": 1,
    # Escrevem, mas sem efeito fiscal nem financeiro, e o contador desfaz num
    # clique. Tier 1 e não 0 porque criam registro que alguém precisa tratar:
    # quem não pode nem abrir chamado é quem ainda está sendo configurado.
    "abrir_chamado": 1,
    "agendar_atendimento": 1,
    # Tier 2 — alterações
    "alterar_pedido": 2,
    "alterar_cadastro": 2,
    # Tier 3 — destrutivas/financeiras
    "excluir_pedido": 3,
    "cancelar_nota": 3,
    "pagar_conta": 3,
}

# Intenção desconhecida é tratada com o tier mais restritivo (fail-safe).
TIER_PADRAO_DESCONHECIDO = 3


def tier_da_intencao(nome_intencao: str) -> int:
    """Retorna o tier exigido pela intenção (desconhecida = tier 3)."""
    return CATALOGO_TIERS.get(nome_intencao, TIER_PADRAO_DESCONHECIDO)


def verificar_tier(intencao_tier: int, perfil) -> bool:
    """True se o perfil do cliente pode executar uma intenção desse tier."""
    if perfil is None:
        return False
    return intencao_tier <= perfil.tier_maximo
