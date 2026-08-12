"""
Abrir imagem que veio de fora, e tirar dela o que dá para conferir.

**Duas leituras muito diferentes moram aqui, e a diferença é o assunto do
módulo.** Um código de barras é *decodificado*: as barras têm largura definida,
o formato tem dígito verificador próprio, e um borrão faz a decodificação
falhar em vez de devolver outro número. O OCR é *adivinhado*: ele sempre
devolve alguma coisa, e o quanto essa coisa parece um número não diz nada sobre
ela estar certa. Por isso a escada em `extracao.py` põe os dois em degraus
diferentes, e só um deles dispensa o contador.

Vale para o Brasil em particular: o DANFE imprime a chave de acesso em código de
barras, o boleto imprime os 44 dígitos em Intercalado 2 de 5, e a NFC-e traz um
QR Code com a chave dentro da URL. A foto que o cliente tira do papel quase
sempre pega esses símbolos — e quando pega, a leitura vem de graça e conferida.

**Imagem vinda do WhatsApp é entrada hostil.** Qualquer um manda um anexo, e um
PNG de 40 KB pode declarar 60000×60000 pixels: descomprimir são 10 GB de RAM num
contêiner que divide o servidor com os outros projetos do usuário. Os limites
abaixo existem por isso, e são checados **antes** de qualquer decodificação.
"""
from __future__ import annotations

import io

import structlog

logger = structlog.get_logger(__name__)

# Teto de pixels da imagem já descomprimida. 40 megapixels cobre com folga
# qualquer foto de celular e qualquer digitalização de A4 em 300 dpi; acima
# disso é bomba de descompressão ou engano, e nos dois casos a resposta é a
# mesma: não abrir.
MAXIMO_DE_PIXELS = 40_000_000

# Lado máximo depois de reduzir. Acima disso o OCR fica mais lento sem ficar
# melhor — a resolução útil para texto de nota satura bem antes.
LADO_MAXIMO = 2400

# Tesseract preso por página. Não é chute: é o limite que impede uma imagem
# patológica de segurar um worker enquanto o cliente espera o recibo.
SEGUNDOS_DE_OCR = 20

# Páginas de PDF sem camada de texto que valem a pena rasterizar. Chave de
# acesso e linha digitável ficam na primeira; a segunda existe para o caso do
# scanner que produz uma capa em branco.
PAGINAS_RASTERIZADAS = 2


def _pillow():
    from PIL import Image, ImageOps

    # O padrão do Pillow já avisa sobre bomba de descompressão, mas com um teto
    # calculado para telas, não para o nosso caso. Aqui o teto é explícito.
    Image.MAX_IMAGE_PIXELS = MAXIMO_DE_PIXELS
    return Image, ImageOps


def abrir(conteudo: bytes):
    """A imagem pronta para leitura, ou `None` se não der para abrir com segurança.

    `None` é resposta legítima e frequente: arquivo que não é imagem, formato que
    o Pillow não conhece, imagem grande demais. Todos terminam na fila do
    contador, que é onde terminariam se este módulo não existisse.
    """
    Image, ImageOps = _pillow()

    try:
        imagem = Image.open(io.BytesIO(conteudo))
        largura, altura = imagem.size
        if largura * altura > MAXIMO_DE_PIXELS:
            logger.warning("imagem_grande_demais", pixels=largura * altura)
            return None
        imagem.load()
        # Foto de celular guarda a rotação nos metadados em vez de girar os
        # pixels. Sem isto, metade das fotos de nota chega deitada — e código de
        # barras deitado não decodifica.
        imagem = ImageOps.exif_transpose(imagem)
        # Tons de cinza: o zbar e o tesseract trabalham assim de qualquer jeito,
        # e converter uma vez aqui evita que cada um faça a sua conversão.
        imagem = imagem.convert("L")
        if max(imagem.size) > LADO_MAXIMO:
            imagem.thumbnail((LADO_MAXIMO, LADO_MAXIMO), Image.LANCZOS)
        return imagem
    except Exception as erro:  # noqa: BLE001 — arquivo de fora, qualquer coisa pode vir
        logger.warning("imagem_nao_abriu", erro=str(erro))
        return None


def codigos(imagem) -> list[str]:
    """Tudo que o zbar conseguir decodificar, como texto.

    Devolve o payload cru — para o DANFE são os 44 dígitos, para a NFC-e é a URL
    de consulta com a chave dentro. Quem chama procura o número lá dentro e o
    confere; aqui não se interpreta nada, só se decodifica.
    """
    try:
        from pyzbar import pyzbar
    except Exception as erro:  # noqa: BLE001 — biblioteca ausente não derruba nada
        logger.warning("zbar_indisponivel", erro=str(erro))
        return []

    try:
        achados = pyzbar.decode(imagem)
    except Exception as erro:  # noqa: BLE001
        logger.warning("zbar_falhou", erro=str(erro))
        return []

    lidos = []
    for achado in achados:
        try:
            lidos.append(achado.data.decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001, S110 - payload binário não interessa
            continue
    return lidos


def texto_por_ocr(imagem) -> str:
    """O que o tesseract acha que está escrito. Nunca levanta, nunca promete.

    Português primeiro porque é o idioma dos documentos; se o pacote de idioma
    não estiver instalado, cai no padrão em vez de falhar — para achar 44
    dígitos, o modelo de idioma importa pouco.
    """
    try:
        import pytesseract
    except Exception as erro:  # noqa: BLE001
        logger.warning("tesseract_indisponivel", erro=str(erro))
        return ""

    for idioma in ("por", None):
        try:
            return pytesseract.image_to_string(
                imagem, lang=idioma, timeout=SEGUNDOS_DE_OCR
            )
        except pytesseract.TesseractNotFoundError:
            logger.warning("tesseract_nao_instalado")
            return ""
        # A ordem destes dois `except` é a diferença entre cair no idioma padrão
        # e desistir calado: `TesseractError` herda de `RuntimeError`, então
        # capturar o genérico primeiro engoliria o "pacote `por` não instalado" —
        # e a segunda tentativa, que existe justamente para esse caso, nunca
        # aconteceria.
        except pytesseract.TesseractError as erro:
            logger.info("ocr_tentando_sem_idioma", idioma=idioma, erro=str(erro))
        except RuntimeError as erro:  # estouro do `timeout`
            logger.warning("ocr_demorou_demais", erro=str(erro))
            return ""
        except Exception as erro:  # noqa: BLE001 — imagem estranha não derruba nada
            logger.warning("ocr_falhou", erro=str(erro))
            return ""
    return ""


def paginas_do_pdf(conteudo: bytes, limite: int = PAGINAS_RASTERIZADAS) -> list:
    """Rasteriza as primeiras páginas de um PDF sem camada de texto.

    É o caso do documento digitalizado: o PDF existe, mas dentro dele há uma
    foto. Rasterizar devolve essa foto para os leitores de imagem — e a escala 2
    é o suficiente para o zbar achar o código de barras sem dobrar o custo.
    """
    try:
        import pypdfium2 as pdfium
    except Exception as erro:  # noqa: BLE001
        logger.warning("pdfium_indisponivel", erro=str(erro))
        return []

    try:
        documento = pdfium.PdfDocument(conteudo)
        paginas = []
        for indice in range(min(len(documento), limite)):
            imagem = documento[indice].render(scale=2).to_pil().convert("L")
            if max(imagem.size) > LADO_MAXIMO:
                from PIL import Image

                imagem.thumbnail((LADO_MAXIMO, LADO_MAXIMO), Image.LANCZOS)
            paginas.append(imagem)
        return paginas
    except Exception as erro:  # noqa: BLE001 — PDF quebrado não derruba recebimento
        logger.warning("pdf_nao_rasterizou", erro=str(erro))
        return []
