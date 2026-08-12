"""
Leitura de documento — Sprint 4, segunda fase.

**O que está sob teste aqui não é "o sistema leu certo", é "o sistema recusa o
que não consegue provar".** Os dois primeiros degraus da escada (XML assinado,
número com dígito verificador) só produzem resultado quando a aritmética fecha, e
a maior parte deste arquivo é feita de leituras que *deveriam* falhar: dígito
trocado, algarismos transpostos, UF inexistente, PDF sem camada de texto, foto.

Cada uma dessas recusas vale mais que um acerto. Um acerto a mais encurta a fila
do contador; uma recusa a menos vira lançamento contábil errado, e lançamento
errado só aparece meses depois, na malha fina, quando ninguém mais lembra de qual
arquivo veio.

As chaves e a linha digitável usadas aqui são literais fixos, e é de propósito:
se fossem montadas pelas mesmas funções que o teste exercita, um erro na conta
apareceria dos dois lados e se cancelaria.
"""
import io
from datetime import date
from decimal import Decimal

import pytest

from apps.documentos import boleto as boleto_mod
from apps.documentos import chave_nfe, extracao

# Padaria Estrela (CNPJ 12345678000190) — a mesma da fixture `cliente`. Emitida
# em SP (35), competência 08/2026, modelo 55, série 1, número 447.
CHAVE_DO_CLIENTE = "35260812345678000190550010000004471000447111"
# Distribuidora qualquer — CNPJ diferente do cliente, e é isso que faz a nota
# ser de entrada em vez de saída.
CHAVE_DE_TERCEIRO = "35260899887766000155550010000012341000001232"

# Itaú (341), R$ 1.240,00, vencimento 15/08/2026 (fator 1539 — segunda era).
LINHA_DIGITAVEL = "34191234546789012345767890123457315390000124000"

HOJE = date(2026, 8, 12)


def trocar(numero: str, posicao: int) -> str:
    """Erra um algarismo — o erro de digitação e de OCR mais comum que existe."""
    novo = str((int(numero[posicao]) + 1) % 10)
    return numero[:posicao] + novo + numero[posicao + 1:]


def pdf_com_texto(*linhas: str) -> bytes:
    """Um PDF de verdade, com camada de texto, montado à mão.

    Sem biblioteca de geração: o que precisa estar sob teste é o caminho real —
    bytes de PDF entrando, texto saindo pelo pypdf —, e uma dependência a mais
    no `requirements.txt` só para produzir massa de teste é preço alto por nada.
    """
    corpo = ["BT /F1 11 Tf 1 0 0 1 40 750 Tm 14 TL"]
    for linha in linhas:
        texto = linha.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        corpo.append(f"({texto}) Tj T*")
    corpo.append("ET")
    fluxo = "\n".join(corpo).encode("latin-1")

    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(fluxo) + fluxo + b"\nendstream",
    ]

    saida = io.BytesIO()
    saida.write(b"%PDF-1.4\n")
    posicoes = []
    for numero, objeto in enumerate(objetos, start=1):
        posicoes.append(saida.tell())
        saida.write(b"%d 0 obj\n" % numero + objeto + b"\nendobj\n")
    xref = saida.tell()
    saida.write(b"xref\n0 %d\n" % (len(objetos) + 1))
    saida.write(b"0000000000 65535 f \n")
    for posicao in posicoes:
        saida.write(b"%010d 00000 n \n" % posicao)
    saida.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objetos) + 1, xref)
    )
    return saida.getvalue()


XML_NFE = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe><infNFe Id="NFe{chave}" versao="4.00">
    <ide><nNF>1234</nNF><dhEmi>2026-08-03T10:12:00-03:00</dhEmi></ide>
    <emit><CNPJ>{cnpj}</CNPJ><xNome>{nome}</xNome></emit>
    <total><ICMSTot><vNF>{valor}</vNF></ICMSTot></total>
  </infNFe></NFe>
</nfeProc>"""


def xml_de(chave: str, cnpj: str, nome: str = "Distribuidora Norte Ltda",
           valor: str = "1240.00") -> bytes:
    return XML_NFE.format(chave=chave, cnpj=cnpj, nome=nome, valor=valor).encode()


# ---------------------------------------------------------------------------
# A chave de acesso
# ---------------------------------------------------------------------------
class TestChaveDeAcesso:
    def test_os_44_digitos_carregam_a_nota_inteira(self):
        """Não é leitura de campo: os dados estão dentro do número."""
        chave = chave_nfe.interpretar(CHAVE_DE_TERCEIRO)

        assert chave is not None
        assert chave.uf == "SP"
        assert chave.competencia == "2026-08"
        assert chave.cnpj_emitente == "99887766000155"
        assert chave.modelo_legivel == "NF-e"
        assert chave.numero == 1234

    def test_um_digito_errado_e_reprovado_pela_aritmetica(self):
        """A diferença entre este módulo e um OCR: aqui o erro se denuncia.

        Testa TODAS as posições, porque um verificador que pega o erro em quase
        todo lugar é um verificador que deixa passar em algum lugar — e não há
        como saber qual sem varrer.
        """
        for posicao in range(44):
            assert chave_nfe.interpretar(trocar(CHAVE_DE_TERCEIRO, posicao)) is None

    def test_troca_de_posicao_tambem_e_reprovada(self):
        """Transposição é o segundo erro mais comum, e o módulo 11 pega."""
        trocada = (
            CHAVE_DE_TERCEIRO[:24] + CHAVE_DE_TERCEIRO[25] + CHAVE_DE_TERCEIRO[24]
            + CHAVE_DE_TERCEIRO[26:]
        )
        assert trocada != CHAVE_DE_TERCEIRO
        assert chave_nfe.interpretar(trocada) is None

    def test_uf_que_nao_existe_nao_vira_chave(self):
        """Dígito bate mas os dois primeiros números não são estado nenhum —
        sinal de que o que foi lido não é uma chave de acesso."""
        base = "99" + CHAVE_DE_TERCEIRO[2:43]
        chave = base + str(chave_nfe.digito_verificador(base))

        assert chave_nfe.digito_verificador(chave[:43]) == int(chave[43])
        assert chave_nfe.interpretar(chave) is None

    def test_mes_treze_nao_vira_chave(self):
        base = CHAVE_DE_TERCEIRO[:4] + "13" + CHAVE_DE_TERCEIRO[6:43]
        chave = base + str(chave_nfe.digito_verificador(base))

        assert chave_nfe.interpretar(chave) is None

    def test_tamanho_errado_volta_none_em_vez_de_estourar(self):
        assert chave_nfe.interpretar("") is None
        assert chave_nfe.interpretar("123") is None
        assert chave_nfe.interpretar(CHAVE_DE_TERCEIRO + "0") is None

    def test_encontra_a_chave_impressa_em_grupos_no_danfe(self):
        """No DANFE ela sai em blocos de quatro, com espaço — e é assim que
        chega no texto extraído do PDF."""
        grupos = " ".join(
            CHAVE_DE_TERCEIRO[i:i + 4] for i in range(0, 44, 4)
        )
        texto = f"DANFE\nCHAVE DE ACESSO\n{grupos}\nConsulta em www.nfe.fazenda.gov.br"

        chave = chave_nfe.procurar_no_texto(texto)
        assert chave is not None and chave.chave == CHAVE_DE_TERCEIRO

    def test_entre_numeros_longos_o_verificador_e_quem_escolhe(self):
        """Num DANFE há protocolo de autorização e código de barras por perto.
        Quem separa não é o layout da página: é o dígito."""
        texto = (
            "PROTOCOLO DE AUTORIZACAO 135260000123456 04/08/2026\n"
            "12345678901234567890123456789012345678901234\n"  # 44 dígitos, DV errado
            f"{CHAVE_DE_TERCEIRO}\n"
        )
        chave = chave_nfe.procurar_no_texto(texto)

        assert chave is not None and chave.chave == CHAVE_DE_TERCEIRO

    def test_texto_sem_chave_nenhuma_volta_none(self):
        assert chave_nfe.procurar_no_texto("recibo de aluguel, R$ 2.500,00") is None
        assert chave_nfe.procurar_no_texto("") is None


@pytest.mark.django_db
class TestQuemEmitiu:
    def test_a_nota_do_proprio_cliente_e_de_saida(self, cliente):
        """Receita ou despesa resolvida por igualdade de string, sem modelo."""
        chave = chave_nfe.interpretar(CHAVE_DO_CLIENTE)
        assert chave.emitida_pelo_cliente(cliente) is True

    def test_a_nota_de_terceiro_e_de_entrada(self, cliente):
        chave = chave_nfe.interpretar(CHAVE_DE_TERCEIRO)
        assert chave.emitida_pelo_cliente(cliente) is False

    def test_pontuacao_do_cnpj_cadastrado_nao_atrapalha(self, cliente):
        """O CNPJ do cadastro pode ter vindo formatado; a chave nunca tem."""
        cliente.cnpj = "12.345.678/0001-90"
        chave = chave_nfe.interpretar(CHAVE_DO_CLIENTE)

        assert chave.emitida_pelo_cliente(cliente) is True


# ---------------------------------------------------------------------------
# O boleto
# ---------------------------------------------------------------------------
class TestLinhaDigitavel:
    def test_valor_e_vencimento_saem_conferidos(self):
        """Os dois campos onde errar custa dinheiro — e os dois vêm com
        verificador, ao contrário do que um OCR leria do papel."""
        titulo = boleto_mod.interpretar(LINHA_DIGITAVEL, hoje=HOJE)

        assert titulo is not None
        assert titulo.banco == "341"
        assert titulo.valor == Decimal("1240.00")
        assert titulo.vencimento == date(2026, 8, 15)

    def test_um_digito_errado_derruba_a_leitura(self):
        for posicao in range(47):
            assert boleto_mod.interpretar(trocar(LINHA_DIGITAVEL, posicao), hoje=HOJE) is None

    def test_boleto_de_arrecadacao_e_recusado_e_nao_chutado(self):
        """48 dígitos começando em 8: concessionária ou tributo, outro layout.
        Aplicar a regra do bancário produziria número — e número errado."""
        with pytest.raises(boleto_mod.BoletoNaoBancario):
            boleto_mod.interpretar("8" * 48, hoje=HOJE)

    def test_encontra_a_linha_impressa_com_pontos_e_espacos(self):
        impressa = (
            f"{LINHA_DIGITAVEL[0:5]}.{LINHA_DIGITAVEL[5:10]} "
            f"{LINHA_DIGITAVEL[10:15]}.{LINHA_DIGITAVEL[15:21]} "
            f"{LINHA_DIGITAVEL[21:26]}.{LINHA_DIGITAVEL[26:32]} "
            f"{LINHA_DIGITAVEL[32]} {LINHA_DIGITAVEL[33:]}"
        )
        titulo = boleto_mod.procurar_no_texto(
            f"Pagavel em qualquer banco\n{impressa}\nBeneficiario: Fornecedor X",
            hoje=HOJE,
        )

        assert titulo is not None and titulo.linha == LINHA_DIGITAVEL


class TestFatorDeVencimento:
    """A pegadinha de calendário da FEBRABAN, que hoje está viva em produção."""

    def test_a_segunda_era_e_a_que_vale_para_boleto_de_hoje(self):
        assert boleto_mod.data_do_fator(1539, hoje=HOJE) == date(2026, 8, 15)

    def test_fator_zero_e_sem_vencimento_e_nao_falha(self):
        assert boleto_mod.data_do_fator(0, hoje=HOJE) is None

    def test_data_implausivel_volta_none_em_vez_de_1998(self):
        """Fator baixo pela primeira era cairia em 1998. Boleto com data errada
        é pior que boleto sem data: um o contador confere, o outro ele acredita."""
        assert boleto_mod.data_do_fator(100, hoje=HOJE) is None

    def test_as_duas_eras_nunca_empatam_com_a_janela_de_hoje(self):
        """A regra de desempate existe, e a varredura mostra que ela nunca
        precisa ser exercida: a diferença entre as eras é maior que a janela.

        Se um dia alguém alargar `ANOS_PARA_TRAS`/`ANOS_PARA_FRENTE` a ponto de
        criar ambiguidade real, este teste falha antes de a data errada chegar a
        um lançamento — que é o único momento em que ainda é barato descobrir.
        """
        ambiguos = [
            fator
            for fator in range(1, 10000)
            if boleto_mod.data_do_fator(fator, hoje=HOJE) is None
            and fator >= boleto_mod.FATOR_REINICIO
            and _duas_candidatas_plausiveis(fator)
        ]
        assert ambiguos == []


def _duas_candidatas_plausiveis(fator: int) -> bool:
    from datetime import timedelta

    inicio = date(HOJE.year - boleto_mod.ANOS_PARA_TRAS, HOJE.month, 1)
    fim = date(HOJE.year + boleto_mod.ANOS_PARA_FRENTE, HOJE.month, 1)
    candidatas = [
        boleto_mod.BASE_PRIMEIRA_ERA + timedelta(days=fator),
        boleto_mod.BASE_SEGUNDA_ERA + timedelta(days=fator - boleto_mod.FATOR_REINICIO),
    ]
    return len([d for d in candidatas if inicio <= d <= fim]) == 2


# ---------------------------------------------------------------------------
# A escada inteira
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestExtrair:
    def test_xml_de_terceiro_vira_nota_de_entrada_com_valor(self, cliente):
        """Degrau 1: não há leitura, há campo. Confiança 100."""
        lido = extracao.extrair(
            xml_de(CHAVE_DE_TERCEIRO, "99887766000155"),
            "nfe.xml", "text/xml", cliente,
        )

        assert lido.tipo == "nota_entrada"
        assert lido.confianca == 100
        assert lido.metodo == "xml_nfe"
        assert lido.dados["valor"] == "1240.00"
        assert lido.dados["chave_acesso"] == CHAVE_DE_TERCEIRO
        assert lido.dispensa_revisao is True

    def test_xml_do_proprio_cliente_vira_nota_de_saida(self, cliente):
        lido = extracao.extrair(
            xml_de(CHAVE_DO_CLIENTE, cliente.cnpj, nome="Padaria Estrela Ltda"),
            "nfe.xml", "application/xml", cliente,
        )

        assert lido.tipo == "nota_saida"
        assert "você emitiu" in lido.resumo

    def test_o_resumo_e_a_frase_que_o_cliente_confere(self, cliente):
        """Quem sabe na hora se o arquivo está errado é quem mandou."""
        lido = extracao.extrair(
            xml_de(CHAVE_DE_TERCEIRO, "99887766000155"),
            "nfe.xml", "text/xml", cliente,
        )

        assert lido.resumo == "NF-e 1234 de Distribuidora Norte Ltda, R$ 1.240,00"

    def test_xml_com_chave_adulterada_nao_passa(self, cliente):
        """Arquivo assinado também chega adulterado, e o dígito custa nada."""
        lido = extracao.extrair(
            xml_de(trocar(CHAVE_DE_TERCEIRO, 7), "99887766000155"),
            "nfe.xml", "text/xml", cliente,
        )

        assert lido == extracao.NADA

    def test_xml_quebrado_vai_para_a_fila_em_vez_de_estourar(self, cliente):
        lido = extracao.extrair(b"<nfeProc><infNFe", "nfe.xml", "text/xml", cliente)

        assert lido.confianca == 0

    def test_xxe_nao_le_arquivo_do_servidor(self, cliente, tmp_path):
        """XML vindo do WhatsApp é entrada hostil: qualquer um manda um anexo.

        O parser no padrão resolveria a entidade e devolveria o conteúdo do
        arquivo dentro de um campo — leitura arbitrária de disco por anexo de
        1 KB. Aqui a entidade não resolve, e o resultado é NADA.
        """
        segredo = tmp_path / "segredo.txt"
        segredo.write_text("SENHA-DO-BANCO")
        ataque = f"""<?xml version="1.0"?>
<!DOCTYPE x [<!ENTITY vaza SYSTEM "file://{segredo.as_posix()}">]>
<nfeProc><NFe><infNFe Id="NFe{CHAVE_DE_TERCEIRO}">
<emit><xNome>&vaza;</xNome></emit>
</infNFe></NFe></nfeProc>""".encode()

        lido = extracao.extrair(ataque, "nfe.xml", "text/xml", cliente)

        assert "SENHA-DO-BANCO" not in str(lido.como_dados())
        assert "SENHA-DO-BANCO" not in lido.resumo

    def test_entidade_aninhada_nao_estoura_a_memoria(self, cliente):
        """"Billion laughs": 1 KB de anexo viram gigabytes de string expandida,
        e o contêiner é compartilhado com os outros projetos do servidor."""
        ataque = b"""<?xml version="1.0"?>
<!DOCTYPE x [
  <!ENTITY a "aaaaaaaaaa">
  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
  <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
]>
<nfeProc><NFe><infNFe Id="x"><emit><xNome>&d;</xNome></emit></infNFe></NFe></nfeProc>"""

        assert extracao.extrair(ataque, "nfe.xml", "text/xml", cliente) == extracao.NADA

    def test_danfe_em_pdf_le_a_chave_e_nao_inventa_o_valor(self, cliente):
        """Degrau 2. A recusa é o ponto: "VALOR TOTAL DA NOTA" está impresso ali
        do lado, e um regex acertaria na maioria dos layouts. "Maioria" é o
        problema — o erro não avisa, e valor errado vira imposto errado."""
        grupos = " ".join(CHAVE_DE_TERCEIRO[i:i + 4] for i in range(0, 44, 4))
        pdf = pdf_com_texto(
            "DANFE - DOCUMENTO AUXILIAR DA NOTA FISCAL ELETRONICA",
            "CHAVE DE ACESSO",
            grupos,
            "VALOR TOTAL DA NOTA   1.240,00",
        )

        lido = extracao.extrair(pdf, "danfe.pdf", "application/pdf", cliente)

        assert lido.tipo == "nota_entrada"
        assert lido.confianca == 95
        assert lido.metodo == "chave_no_texto"
        assert "valor" not in lido.dados
        assert "1.240,00" not in lido.resumo

    def test_boleto_em_pdf_sai_com_valor_e_vencimento(self, cliente):
        pdf = pdf_com_texto(
            "Banco Itau S.A.",
            LINHA_DIGITAVEL,
            "Vencimento 15/08/2026",
        )

        lido = extracao.extrair(pdf, "boleto.pdf", "application/pdf", cliente)

        assert lido.tipo == "boleto"
        assert lido.confianca == 95
        assert lido.dados["valor"] == "1240.00"
        assert lido.dados["vencimento"] == "2026-08-15"

    def test_pdf_escaneado_sem_texto_continua_indo_para_a_fila(self, cliente):
        """Foto de nota salva como PDF: nenhuma camada de texto, nada a provar."""
        pdf = pdf_com_texto()

        assert extracao.extrair(pdf, "scan.pdf", "application/pdf", cliente).confianca == 0

    def test_pdf_corrompido_nao_derruba_o_recebimento(self, cliente):
        lido = extracao.extrair(b"%PDF-1.4 truncado", "nota.pdf", "application/pdf", cliente)

        assert lido == extracao.NADA

    def test_foto_de_nota_nao_e_lida_e_isso_e_a_decisao_do_sprint(self, cliente):
        """Imagem não tem degrau nesta escada — de propósito.

        Ler a foto exigiria OCR, e o que sai de um OCR chega sem prova: pode
        estar certo, pode ter trocado 4 por 6, e não há como distinguir depois.
        Enquanto isso for verdade, quem resolve é gente.
        """
        assert extracao.extrair(b"\xff\xd8\xff\xe0jpeg", "nota.jpg", "image/jpeg", cliente) == extracao.NADA

    def test_leitura_que_estoura_por_dentro_ainda_devolve_nada(self, cliente, monkeypatch):
        """O contrato de `extrair` é não levantar nunca — o arquivo já está
        guardado quando ela roda, e falhar aqui perderia o que está a salvo."""
        def explode(*_args, **_kwargs):
            raise RuntimeError("biblioteca com bug")

        monkeypatch.setattr(extracao, "_do_xml", explode)

        assert extracao.extrair(b"<x/>", "nfe.xml", "text/xml", cliente) == extracao.NADA
