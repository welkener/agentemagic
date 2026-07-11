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
        return {"emitir_nfse", "consultar_nfse", "criar_rascunho_nfse"}

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
