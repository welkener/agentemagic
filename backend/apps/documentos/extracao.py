"""
O que dá para saber de um documento sem perguntar a ninguém.

**A escada, do mais confiável para o menos** — e o critério de ordem não é
sofisticação, é *verificabilidade*:

1. **XML da NF-e** (100). O documento fiscal em si, assinado digitalmente pela
   SEFAZ. Não há leitura: há campo.
2. **Chave de acesso ou linha digitável na camada de texto do PDF** (95). Os
   dígitos são bytes exatos, e ainda passam pelo verificador.
3. **Código de barras ou QR Code na imagem** (95). O DANFE imprime a chave em
   barras, o boleto imprime os 44 dígitos em Intercalado 2 de 5, a NFC-e traz um
   QR. Não é adivinhação: um borrão faz a decodificação *falhar*, não devolver
   outro número — e depois disso o dígito verificador ainda confere.
4. **OCR da imagem** (85 — **abaixo do limiar, de propósito**). Ver adiante.
5. **Nada** (0). Vai para a fila do contador.

**Por que o OCR fica abaixo da linha, mesmo achando um número que confere.** O
módulo 11 pega todo erro de um dígito e toda troca de posição — é para isso que
ele existe. O que ele não pega é erro múltiplo: quando dois ou mais dígitos saem
errados, cerca de uma em onze sequências ainda fecha a conta. Para digitação
humana isso é irrelevante, porque errar dois dígitos é raro. Para OCR não é: o
OCR erra em rajada — a mesma sombra que derruba o 8 derruba o 3 ao lado. Uma
chance em onze de aprovar uma chave errada é alta demais para virar lançamento
sozinha, e baixa o suficiente para valer como palpite bem-educado. Então o
resultado do OCR fica guardado, aparece na fila como contexto para quem revisa, e
**não** preenche o tipo do documento nem tira ninguém da fila.

Isso também é o que separa este degrau do anterior. Código de barras não
adivinha; OCR sempre devolve alguma coisa.

**O que este módulo se recusa a extrair:** o valor de uma nota a partir do texto
do DANFE. Ele está impresso lá, e um regex em "VALOR TOTAL DA NOTA" acertaria na
maioria dos layouts. "Maioria" é o problema: o erro não avisa, e valor errado em
lançamento contábil vira imposto errado. Do PDF sai o que a aritmética confirma —
chave, emitente, competência, número. O valor vem do XML, do código de barras do
boleto, ou do humano.

**XML é entrada hostil.** O arquivo chega de fora, por WhatsApp, de quem
qualquer um pode ser. Um parser XML no padrão resolve entidades externas e
expande entidades aninhadas — o que transforma um anexo de 1 KB em leitura de
arquivo do servidor ou em estouro de memória. O parser daqui vem com isso
desligado explicitamente. Imagem tem o problema equivalente, tratado em
`imagem.py`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import structlog

from apps.documentos import boleto as boleto_mod
from apps.documentos import chave_nfe, imagem as imagem_mod

logger = structlog.get_logger(__name__)

# Acima deste ponto o documento não precisa passar pela fila. O número é alto de
# propósito: só chegam aqui leituras conferidas por assinatura ou por dígito
# verificador sobre dígitos que ninguém adivinhou. Não é "muito provável" — é
# "confere".
LIMIAR_SEM_REVISAO = 90

CONFIANCA_XML = 100
CONFIANCA_TEXTO = 95
CONFIANCA_CODIGO = 95
# Um ponto abaixo do limiar seria arbitrário; 85 diz que a leitura é boa e não
# é prova. O que importa é o lado da linha, e ele é o mesmo desde o primeiro
# commit da fase 2: sem prova, quem decide é gente.
CONFIANCA_OCR = 85


@dataclass(frozen=True)
class Extracao:
    """O que se soube do documento, com quanta certeza e por qual caminho."""

    tipo: str
    confianca: int = 0
    metodo: str = "nenhum"
    dados: dict = field(default_factory=dict)
    resumo: str = ""

    @property
    def dispensa_revisao(self) -> bool:
        return self.confianca >= LIMIAR_SEM_REVISAO

    def como_dados(self) -> dict:
        """O que fica gravado no documento.

        O `resumo` vai junto de propósito, e não é redundância de apresentação:
        é a frase que o cliente lê no WhatsApp e a que o contador lê na fila —
        as duas precisam ser a mesma. Se cada ponta montasse a sua, elas
        divergiriam no primeiro ajuste, e o suporte passaria a comparar duas
        descrições do mesmo arquivo.
        """
        return {
            **self.dados,
            "metodo": self.metodo,
            "confianca": self.confianca,
            "resumo": self.resumo,
        }


# Nada aqui é chute: é o estado de quem não conseguiu ler.
NADA = Extracao(tipo="desconhecido")


def _parser_seguro():
    """Parser lxml sem entidade externa, sem rede e sem DTD.

    Cada um desses desligamentos fecha um ataque conhecido: entidade externa lê
    arquivo do servidor (XXE), rede busca conteúdo remoto, DTD com entidades
    aninhadas estoura a memória ("billion laughs"). O padrão do lxml deixa os
    três ligados.
    """
    from lxml import etree

    return etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )


def _local(elemento) -> str:
    from lxml import etree

    return etree.QName(elemento).localname


def _primeiro(raiz, *nomes: str):
    """Primeiro elemento cujo caminho de nomes locais bate — ignora namespace.

    Ignorar namespace é deliberado: NF-e, NFS-e nacional e os padrões municipais
    usam URIs diferentes para árvores parecidas, e amarrar no namespace faria o
    módulo recusar arquivos válidos por um detalhe que não muda o significado.
    """
    atual = [raiz]
    for nome in nomes:
        proximos = []
        for elemento in atual:
            proximos.extend(f for f in elemento.iterdescendants() if _local(f) == nome)
        if not proximos:
            return None
        atual = proximos
    return atual[0]


def _texto(raiz, *nomes: str) -> str:
    elemento = _primeiro(raiz, *nomes)
    return (elemento.text or "").strip() if elemento is not None else ""


def _decimal(bruto: str) -> Decimal | None:
    try:
        return Decimal(bruto) if bruto else None
    except InvalidOperation:
        return None


def _do_xml(conteudo: bytes, cliente) -> Extracao:
    from lxml import etree

    try:
        raiz = etree.fromstring(conteudo, parser=_parser_seguro())
    except etree.XMLSyntaxError:
        return NADA

    # A chave vem do atributo Id (`NFe` + 44 dígitos) e é conferida na
    # aritmética como qualquer outra — XML assinado também pode vir adulterado,
    # e o dígito verificador custa nada.
    inf = _primeiro(raiz, "infNFe")
    identificador = (inf.get("Id") if inf is not None else "") or ""
    chave = chave_nfe.interpretar(identificador)
    if chave is None:
        return NADA

    valor = _decimal(_texto(raiz, "ICMSTot", "vNF")) or _decimal(_texto(raiz, "vNF"))
    emitente = _texto(raiz, "emit", "xNome")
    emissao = _texto(raiz, "ide", "dhEmi")[:10]

    dados = {
        **chave.como_dados(),
        "nome_emitente": emitente,
        "valor": str(valor) if valor is not None else None,
        "emissao": emissao,
    }
    return Extracao(
        tipo=_tipo_da_nota(chave, cliente),
        confianca=CONFIANCA_XML,
        metodo="xml_nfe",
        dados=dados,
        resumo=_resumo_da_nota(chave, cliente, emitente, valor),
    )


def _tipo_da_nota(chave, cliente) -> str:
    """Entrada ou saída — pela comparação de CNPJ, não por palpite.

    Quem emitiu a nota está dentro da chave. Se foi o próprio cliente, ele
    vendeu; se foi outro, ele comprou. É a diferença entre receita e despesa, e
    ela sai daqui resolvida por igualdade de string.
    """
    return "nota_saida" if chave.emitida_pelo_cliente(cliente) else "nota_entrada"


def _cnpj_legivel(cnpj: str) -> str:
    if len(cnpj) != 14:
        return cnpj
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def _reais(valor: Decimal) -> str:
    inteiro, _, centavos = f"{valor:.2f}".partition(".")
    with_pontos = re.sub(r"(?<=\d)(?=(\d{3})+$)", ".", inteiro)
    return f"R$ {with_pontos},{centavos}"


def _resumo_da_nota(chave, cliente, emitente: str, valor: Decimal | None) -> str:
    quem = emitente or _cnpj_legivel(chave.cnpj_emitente)
    if chave.emitida_pelo_cliente(cliente):
        frase = f"{chave.modelo_legivel} {chave.numero} que você emitiu"
    else:
        frase = f"{chave.modelo_legivel} {chave.numero} de {quem}"
    if valor is not None:
        frase += f", {_reais(valor)}"
    return frase


def _do_texto(
    texto: str, cliente, confianca: int = CONFIANCA_TEXTO, sufixo: str = ""
) -> Extracao:
    """Procura no texto o que se confere sozinho.

    `confianca` e `sufixo` existem porque o mesmo texto pode ter vindo da camada
    de texto do PDF (dígitos exatos) ou do OCR (dígitos adivinhados). A busca é a
    mesma; o peso do resultado não pode ser.
    """
    chave = chave_nfe.procurar_no_texto(texto)
    if chave is not None:
        return Extracao(
            tipo=_tipo_da_nota(chave, cliente),
            confianca=confianca,
            metodo=f"chave_no_texto{sufixo}",
            # Sem valor: ele não está na chave, e o texto do DANFE não confere
            # nada. Ver a docstring do módulo.
            dados=chave.como_dados(),
            resumo=_com_ressalva(
                _resumo_da_nota(chave, cliente, "", None), confianca
            ),
        )

    try:
        titulo = boleto_mod.procurar_no_texto(texto)
    except boleto_mod.BoletoNaoBancario:
        return NADA
    if titulo is not None:
        return Extracao(
            tipo="boleto",
            confianca=confianca,
            metodo=f"linha_digitavel{sufixo}",
            dados=titulo.como_dados(),
            resumo=_com_ressalva(_resumo_do_boleto(titulo), confianca),
        )

    return NADA


def _com_ressalva(frase: str, confianca: int) -> str:
    """Leitura que não dispensa revisão diz isso na própria frase.

    O resumo é lido por gente — na fila do contador e no WhatsApp do cliente — e
    quem lê não vê o número da confiança do lado. Uma frase afirmativa sobre um
    palpite é o começo de todo lançamento errado que ninguém questionou.
    """
    if confianca >= LIMIAR_SEM_REVISAO:
        return frase
    return f"parece {frase} — lido da imagem, ainda não confirmado"


def _resumo_do_boleto(titulo) -> str:
    partes = ["boleto"]
    if titulo.valor is not None:
        partes.append(f"de {_reais(titulo.valor)}")
    if titulo.vencimento is not None:
        partes.append(f"com vencimento em {titulo.vencimento:%d/%m/%Y}")
    return " ".join(partes)


def _do_codigos(payloads: list[str], cliente) -> Extracao:
    """O que os símbolos decodificados na imagem provam.

    O payload chega cru e pode ser qualquer coisa: 44 dígitos secos do DANFE, a
    URL de consulta da NFC-e com a chave no meio, o código de barras do boleto.
    Não se pergunta ao símbolo o que ele é — tenta-se cada leitura e vale a que
    a aritmética aprovar. Um QR de propaganda impresso na mesma folha
    simplesmente não passa em nenhuma.
    """
    for payload in payloads:
        chave = chave_nfe.procurar_no_texto(payload)
        if chave is not None:
            return Extracao(
                tipo=_tipo_da_nota(chave, cliente),
                confianca=CONFIANCA_CODIGO,
                metodo="codigo_de_barras",
                dados=chave.como_dados(),
                resumo=_resumo_da_nota(chave, cliente, "", None),
            )

    for payload in payloads:
        digitos = boleto_mod.apenas_digitos(payload)
        titulo = boleto_mod.interpretar_barra(digitos)
        if titulo is None:
            titulo = boleto_mod.interpretar(digitos) if len(digitos) == 47 else None
        if titulo is not None:
            return Extracao(
                tipo="boleto",
                confianca=CONFIANCA_CODIGO,
                metodo="codigo_de_barras",
                dados=titulo.como_dados(),
                resumo=_resumo_do_boleto(titulo),
            )

    return NADA


def _de_imagens(imagens: list, cliente) -> Extracao:
    """Códigos de barras primeiro, OCR só se eles falharem.

    A ordem é economia e é correção ao mesmo tempo: o zbar custa milissegundos e
    devolve prova; o tesseract custa segundos e devolve palpite. Rodar o caro
    para chegar ao resultado pior seria errado nas duas contas.
    """
    for imagem in imagens:
        lido = _do_codigos(imagem_mod.codigos(imagem), cliente)
        if lido is not NADA:
            return lido

    # Só a primeira página vai para o OCR. A chave de acesso e a linha digitável
    # estão na frente do documento, e cada página a mais é mais um segundo com o
    # cliente esperando o recibo — num servidor que não é só nosso.
    for imagem in imagens[:1]:
        lido = _do_texto(
            imagem_mod.texto_por_ocr(imagem), cliente,
            confianca=CONFIANCA_OCR, sufixo="_ocr",
        )
        if lido is not NADA:
            return lido

    return NADA


def _texto_do_pdf(conteudo: bytes) -> str:
    """Camada de texto do PDF, se houver. PDF escaneado devolve vazio — e é o
    caso que continua indo para a fila, exatamente como antes."""
    import io

    try:
        from pypdf import PdfReader

        leitor = PdfReader(io.BytesIO(conteudo))
        # Cinco páginas bastam: chave de acesso e linha digitável ficam na
        # primeira; ler um PDF de duzentas páginas inteiro para achar o que está
        # no começo é gasto de memória num servidor compartilhado.
        return "\n".join((p.extract_text() or "") for p in leitor.pages[:5])
    except Exception as erro:  # noqa: BLE001 — PDF quebrado não derruba recebimento
        logger.warning("pdf_sem_texto_legivel", erro=str(erro))
        return ""


EXTENSOES_DE_IMAGEM = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic")


def _do_pdf(conteudo: bytes, cliente) -> Extracao:
    """Camada de texto primeiro; se não houver, rasteriza e trata como imagem.

    Os dois casos existem de verdade e são bem diferentes. O PDF emitido por
    sistema traz o texto dentro, e ler é exato. O PDF que saiu do scanner ou do
    aplicativo de digitalizar do celular é uma foto embrulhada — e aí vale a
    mesma escada da foto, com o mesmo teto de confiança.
    """
    lido = _do_texto(_texto_do_pdf(conteudo), cliente)
    if lido is not NADA:
        return lido
    return _de_imagens(imagem_mod.paginas_do_pdf(conteudo), cliente)


def extrair(conteudo: bytes, nome_arquivo: str, tipo_mime: str, cliente) -> Extracao:
    """O degrau mais alto que este documento alcança.

    Nunca levanta: documento ilegível não é erro de recebimento, é documento que
    vai para a fila. Quem chama já guardou o arquivo — falhar aqui perderia o
    que já está a salvo.
    """
    nome = (nome_arquivo or "").lower()
    mime = (tipo_mime or "").lower()

    try:
        if nome.endswith(".xml") or "xml" in mime:
            return _do_xml(conteudo, cliente)
        if nome.endswith(".pdf") or "pdf" in mime:
            return _do_pdf(conteudo, cliente)
        if nome.endswith(EXTENSOES_DE_IMAGEM) or mime.startswith("image/"):
            aberta = imagem_mod.abrir(conteudo)
            return _de_imagens([aberta] if aberta is not None else [], cliente)
    except Exception as erro:  # noqa: BLE001
        logger.warning("extracao_falhou", arquivo=nome, erro=str(erro))

    return NADA
