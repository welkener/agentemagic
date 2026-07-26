"""
Adaptador MOCK da NFS-e Nacional — simula o ADN/Sefin para as semanas 1–2.

Comportamentos simulados:
- valida campos obrigatórios da DPS;
- rejeita quando o CNAE está ausente (erro comum na vida real);
- na emissão feliz, devolve protocolo falso + link de DANFSE falso.

O adaptador real (Produção Restrita → produção) entra na Semana 3 e
implementa exatamente o mesmo contrato.
"""
import uuid

from .base import AdapterBase, ResultadoAcao

CAMPOS_OBRIGATORIOS_NFSE = (
    "cnpj_prestador",
    "cnae",
    "valor",
    "descricao_servico",
    "tomador",
)


class NfseMockAdapter(AdapterBase):
    """Simulação da API NFS-e Nacional (emissão de nota de serviço)."""

    def capacidades(self) -> set[str]:
        return {"emitir_nfse", "consultar_nfse", "criar_rascunho_nfse", "cancelar_nfse"}

    def consultar(self, recurso: str, filtros: dict, ctx) -> ResultadoAcao:
        if recurso != "nfse":
            return ResultadoAcao(ok=False, erro_padronizado="RECURSO_NAO_ENCONTRADO")
        protocolo = filtros.get("protocolo")
        if not protocolo:
            return ResultadoAcao(ok=False, erro_padronizado="FILTRO_OBRIGATORIO_AUSENTE")
        return ResultadoAcao(
            ok=True,
            dados={"protocolo": protocolo, "situacao": "AUTORIZADA"},
            referencia_externa=protocolo,
        )

    def criar_rascunho(self, recurso: str, dados: dict, ctx) -> ResultadoAcao:
        if recurso != "nfse":
            return ResultadoAcao(ok=False, erro_padronizado="RECURSO_NAO_ENCONTRADO")
        rascunho_id = f"RASC-NFSE-{uuid.uuid4().hex[:8].upper()}"
        return ResultadoAcao(
            ok=True,
            dados={"rascunho_id": rascunho_id, "dps": dados},
            referencia_externa=rascunho_id,
        )

    def alterar(self, recurso: str, id_ext: str, mudancas: dict, ctx) -> ResultadoAcao:
        # NFS-e emitida não se altera — cancela e reemite (fora do escopo do MVP).
        return ResultadoAcao(ok=False, erro_padronizado="OPERACAO_NAO_SUPORTADA")

    def cancelar(self, documento: str, referencia: str, motivo: str, ctx) -> ResultadoAcao:
        """Simula o evento de cancelamento do ADN.

        Na vida real isto é um evento assinado sobre a NFS-e (não um DELETE) e
        tem **prazo legal** — passado o prazo, a Sefin recusa e o caminho vira
        substituição. O mock reproduz a recusa por motivo ausente, que é a
        rejeição mais comum, pra o fluxo do contador já nascer testado nela.
        """
        if documento != "nfse":
            return ResultadoAcao(ok=False, erro_padronizado="RECURSO_NAO_ENCONTRADO")
        if not referencia:
            return ResultadoAcao(ok=False, erro_padronizado="FILTRO_OBRIGATORIO_AUSENTE")
        if not (motivo or "").strip():
            return ResultadoAcao(
                ok=False,
                erro_padronizado="REJEITADA_MOTIVO_AUSENTE",
                dados={"mensagem_sefin": "Justificativa de cancelamento não informada."},
            )

        protocolo_cancelamento = f"CANC-{uuid.uuid4().hex[:12].upper()}"
        return ResultadoAcao(
            ok=True,
            dados={
                "protocolo_cancelamento": protocolo_cancelamento,
                "situacao": "CANCELADA",
                "nfse_cancelada": referencia,
            },
            referencia_externa=protocolo_cancelamento,
        )

    def emitir(self, documento: str, dados: dict, ctx) -> ResultadoAcao:
        if documento != "nfse":
            return ResultadoAcao(ok=False, erro_padronizado="RECURSO_NAO_ENCONTRADO")

        # Simula a rejeição mais comum: CNAE ausente.
        if not dados.get("cnae"):
            return ResultadoAcao(
                ok=False,
                erro_padronizado="REJEITADA_CNAE_AUSENTE",
                dados={"mensagem_sefin": "Código CNAE não informado na DPS."},
            )

        faltantes = [c for c in CAMPOS_OBRIGATORIOS_NFSE if not dados.get(c)]
        if faltantes:
            return ResultadoAcao(
                ok=False,
                erro_padronizado="CAMPO_OBRIGATORIO_AUSENTE",
                dados={"campos_faltantes": faltantes},
            )

        protocolo = f"NFSE-{uuid.uuid4().hex[:12].upper()}"
        return ResultadoAcao(
            ok=True,
            dados={
                "protocolo": protocolo,
                "situacao": "AUTORIZADA",
                "danfse_url": f"https://danfse.exemplo.gov.br/{protocolo}.pdf",
                "valor": dados["valor"],
            },
            referencia_externa=protocolo,
        )
