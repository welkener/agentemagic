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
CATALOGO_TIERS: dict[str, int] = {
    # Tier 0 — consultas
    "consultar_estoque": 0,
    "consultar_pedido": 0,
    "consultar_contas": 0,
    "consultar_fluxo_caixa": 0,
    "consultar_nota": 0,
    # Tier 1 — rascunho e emissão
    "criar_rascunho": 1,
    "criar_rascunho_pedido": 1,
    "emitir_nota": 1,
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
