"""
Número de WhatsApp brasileiro — o problema do nono dígito.

Descoberto em produção (27/jul/2026), testando com número real: o contador
cadastra o cliente como `5599991332604` (o que ele tem na agenda), o cliente
manda mensagem, e o WhatsApp entrega o JID como `559991332604` — **sem** o nono
dígito. A comparação de string crua não bate, o gate de sessão não reconhece o
número e o cliente recebe "não te reconheço" sem ninguém entender por quê.

Não é caso de borda: o Brasil adicionou o nono dígito aos celulares por etapas
(2010–2016) e o WhatsApp convive com as duas formas até hoje, variando por DDD
e por quando a conta foi criada. Qualquer carteira real terá as duas.

A escolha aqui é **não** tentar reproduzir a regra de qual DDD usa qual forma —
essa regra é histórica, mal documentada e mudaria de novo. Em vez disso,
geram-se as duas variantes possíveis e aceita-se qualquer uma. É mais robusto e
não depende de uma tabela que envelhece.

Limite deliberado do escopo: só mexe em número **brasileiro de celular**
(`55` + DDD + 8 ou 9 dígitos, parte local começando em 6–9). Número
internacional passa intacto, e telefone fixo (parte local começando em 2–5)
nunca ganha um nono dígito que o transformaria num celular que não existe.

⚠ Isto **não** afrouxa a anticlonagem: continua sendo igualdade estrita, só que
sobre a forma canônica. Duas grafias do mesmo assinante passam a ser o mesmo
assinante — que é o fato. Números diferentes seguem diferentes.
"""

PREFIXO_BR = "55"
TAMANHO_COM_NONO = 13  # 55 + DDD(2) + 9 dígitos
TAMANHO_SEM_NONO = 12  # 55 + DDD(2) + 8 dígitos
PRIMEIROS_DIGITOS_DE_CELULAR = "6789"


def so_digitos(valor) -> str:
    """Remove `+`, espaço, parênteses, hífen e o sufixo `@s.whatsapp.net`."""
    return "".join(c for c in str(valor or "") if c.isdigit())


def _e_celular_br_com_nono(digitos: str) -> bool:
    return (
        len(digitos) == TAMANHO_COM_NONO
        and digitos.startswith(PREFIXO_BR)
        and digitos[4] == "9"  # 55 DD [9] xxxxxxxx
    )


def _e_celular_br_sem_nono(digitos: str) -> bool:
    return (
        len(digitos) == TAMANHO_SEM_NONO
        and digitos.startswith(PREFIXO_BR)
        and digitos[4] in PRIMEIROS_DIGITOS_DE_CELULAR
    )


def variantes(numero) -> list[str]:
    """As grafias equivalentes do número, a mais provável primeiro.

    Sempre inclui a forma como veio — mesmo que não seja brasileira —, então o
    chamador pode usar isto como lista de busca sem tratar exceção.
    """
    digitos = so_digitos(numero)
    if not digitos:
        return []

    if _e_celular_br_com_nono(digitos):
        return [digitos, digitos[:4] + digitos[5:]]  # tira o 9 depois do DDD
    if _e_celular_br_sem_nono(digitos):
        return [digitos, digitos[:4] + "9" + digitos[4:]]  # devolve o 9
    return [digitos]


def canonico(numero) -> str:
    """Forma preferida para gravar: celular brasileiro **com** o nono dígito.

    É o formato que o contador reconhece e o que a Receita/agenda usam. Note que
    a comparação nunca depende disto — quem compara é `mesmo_numero`.
    """
    digitos = so_digitos(numero)
    if _e_celular_br_sem_nono(digitos):
        return digitos[:4] + "9" + digitos[4:]
    return digitos


def mesmo_numero(a, b) -> bool:
    """True se as duas grafias identificam o mesmo assinante."""
    if not a or not b:
        return False
    return canonico(a) == canonico(b)
