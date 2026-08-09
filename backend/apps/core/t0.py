"""
T0 — a camada determinística, agora na frente do LLM (DEC-08).

Até aqui a ordem era: o roteador Groq atendia primeiro, e o determinístico por
palavra-chave era rede de indisponibilidade. Inverte-se por dois motivos que
apontam para o mesmo lado:

- **Custo e latência.** No pico dos dias 5–10, o que domina o volume é mensagem
  repetitiva — "menu", "oi", "obrigado", "quais notas eu emiti". Resposta
  determinística é instantânea e grátis; passar isso por um LLM é pagar e
  esperar por nada.
- **Previsibilidade.** O que o T0 responde, responde sempre igual. Num
  assistente fiscal isso vale por si.

**A regra que faz a inversão ser segura.** O classificador do T0 é *estrito*:
só devolve intenção quando a evidência é inequívoca, e devolve `None` no resto.
O classificador de fallback (`orchestrator._classificar_por_palavra_chave`)
continua permissivo — inclusive com o velho "falou em nota sem verbo claro →
emitir" —, porque ele só entra quando o Groq caiu, e aí chutar é melhor que
recusar. Se o estrito herdasse esse chute, mensagem ambígua sobre nota passaria
a emitir sem o LLM ver, e emitir por engano tem custo real para o cliente.

O que o T0 **não** faz: 2ª via de guia e status de obrigação, previstos no
DEC-08. Dependem dos models de rotina contábil, que são do Sprint 3. Ficam de
fora com nome — não silenciosamente ausentes.
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------
def normalizar(texto: str) -> str:
    """Minúsculas, sem acento, sem pontuação de borda.

    "Não", "nao" e "NÃO!" precisam ser a mesma coisa: teclado de celular,
    corretor automático e pressa produzem as três o dia inteiro.
    """
    if not texto:
        return ""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sem_acento.lower()).strip(" .!?,;:\n\t")


# ---------------------------------------------------------------------------
# Respostas prontas
# ---------------------------------------------------------------------------
_MENU = (
    "Oi! Eu sou o Lumen, seu assistente no WhatsApp. 💫\n\n"
    "Posso te ajudar com:\n"
    "🧾 *emitir nota* — é só dizer o valor, o cliente e o serviço\n"
    "📋 *minhas notas* — as últimas notas que saíram\n"
    "📊 *estoque*, *pedidos*, *contas a pagar*, *contas a receber*, *fluxo de caixa*\n\n"
    "É só escrever normalmente — ou mandar áudio, que eu escuto. 😉"
)

_SAUDACOES = {
    "oi", "ola", "opa", "eai", "e ai", "eae", "bom dia", "boa tarde", "boa noite",
    "oi tudo bem", "ola tudo bem", "tudo bem", "oi bom dia", "oi boa tarde",
    "oi boa noite", "salve", "hey", "hello",
}
_PEDIDOS_DE_MENU = {
    "menu", "ajuda", "help", "opcoes", "opcao", "comandos", "socorro",
    "o que voce faz", "oq voce faz", "o que vc faz", "oq vc faz",
    "como funciona", "o que voce pode fazer", "o que vc pode fazer",
    "quem e voce", "quem e vc", "0",
}
_AGRADECIMENTOS = {
    "obrigado", "obrigada", "obg", "vlw", "valeu", "brigado", "brigada",
    "muito obrigado", "muito obrigada", "show", "beleza", "blz", "top",
    "perfeito", "otimo", "legal", "maravilha", "👍", "🙏", "❤️",
}
_DESPEDIDAS = {
    "tchau", "ate mais", "ate logo", "falou", "flw", "abraco", "abracos",
    "boa noite entao", "por hoje e so", "ate amanha",
}

_RESPOSTA_AGRADECIMENTO = "Que bom ter ajudado! 😊 Se precisar de mais alguma coisa, é só chamar."
_RESPOSTA_DESPEDIDA = "Até mais! Estou por aqui quando precisar. 👋"


def responder(mensagem: str) -> str | None:
    """Resposta pronta, ou None se esta mensagem precisa de mais do que texto fixo.

    Só entra aqui o que não depende de dado do cliente. Qualquer coisa que
    precise consultar nota, saldo ou cadastro passa pela intenção — porque aí a
    resposta é escopada por tenant e auditada, e não pode nascer de um `dict`.
    """
    texto = normalizar(mensagem)
    if not texto:
        return None
    if texto in _PEDIDOS_DE_MENU or texto in _SAUDACOES:
        return _MENU
    if texto in _AGRADECIMENTOS:
        return _RESPOSTA_AGRADECIMENTO
    if texto in _DESPEDIDAS:
        return _RESPOSTA_DESPEDIDA
    return None


# ---------------------------------------------------------------------------
# Classificação estrita
# ---------------------------------------------------------------------------
# Verbos que decidem sozinhos qual das três intenções de nota é. A ordem importa
# e é a mesma do fallback: cancelar ganha de emitir, que ganha de consultar.
_RE_CANCELAR = re.compile(r"\b(cancelar|cancela|cancele|cancelamento|anular|anula)\b")
_RE_EMITIR = re.compile(r"\b(emitir|emite|emita|gerar|gera|gere|fazer|faz|faca|tirar|tira)\b")
_RE_CONSULTAR = re.compile(
    r"\b(quais|quantas|consultar|consulta|ver|listar|lista|mostrar|mostra|"
    r"emiti|emitida|emitidas|cade|ultimas|ultima)\b"
)
_RE_NOTA = re.compile(r"\b(nota|notas|nfse|nfs-e|nf|danfse)\b")

# Consultas de ERP. Cada tupla é (regex de evidência, intenção). São estritas
# porque a palavra sozinha já é o assunto: quem escreve "estoque" quer estoque.
_ERP_ESTRITO = [
    (re.compile(r"\bestoque\b"), "consultar_estoque"),
    (re.compile(r"\b(contas? a receber|a receber|recebiveis)\b"), "consultar_contas_receber"),
    (re.compile(r"\b(contas? a pagar|a pagar|boletos? a pagar)\b"), "consultar_contas_pagar"),
    (re.compile(r"\b(fluxo de caixa|saldo em caixa|meu caixa)\b"), "consultar_fluxo_caixa"),
    (re.compile(r"\b(pedidos?|vendas?)\b"), "consultar_pedido"),
]


def classificar(mensagem: str) -> str | None:
    """Intenção de alta confiança, ou None para o LLM decidir.

    `None` não é falha: é o T0 dizendo que não tem certeza suficiente. Toda
    dúvida sobe para o T1, que é onde o custo de errar se paga com um modelo
    melhor.
    """
    texto = normalizar(mensagem)
    if not texto:
        return None

    if _RE_NOTA.search(texto):
        # "cancela a nota" e "emite uma nota" são inequívocos. "minha nota",
        # sozinho, não é — pode ser consulta ou início de emissão —, e é
        # exatamente aí que o T0 se cala em vez de chutar.
        if _RE_CANCELAR.search(texto):
            return "cancelar_nota"
        if _RE_EMITIR.search(texto):
            return "emitir_nota"
        if _RE_CONSULTAR.search(texto):
            return "consultar_nota"
        return None

    for regex, intencao in _ERP_ESTRITO:
        if regex.search(texto):
            return intencao

    return None
