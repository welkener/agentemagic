"""Testes da trilha de auditoria: cadeia de hashes e imutabilidade."""
import pytest

from apps.audit.models import Auditoria, ErroAuditoriaImutavel, calcular_hash
from apps.audit.services import registrar, verificar_cadeia


@pytest.mark.django_db
def test_cadeia_de_tres_registros_e_integra(cliente):
    r1 = registrar("evento_um", {"a": 1}, cliente=cliente)
    r2 = registrar("evento_dois", {"b": 2})
    r3 = registrar("evento_tres", {"c": 3}, cliente=cliente)

    # Encadeamento: hash_anterior de cada linha = hash_atual da anterior.
    assert r1.hash_anterior == ""
    assert r2.hash_anterior == r1.hash_atual
    assert r3.hash_anterior == r2.hash_atual

    # Hashes recalculáveis de forma determinística.
    assert r1.hash_atual == calcular_hash("evento_um", {"a": 1}, cliente.id, "")
    assert r2.hash_atual == calcular_hash("evento_dois", {"b": 2}, None, r1.hash_atual)
    assert r3.hash_atual == calcular_hash(
        "evento_tres", {"c": 3}, cliente.id, r2.hash_atual
    )
    assert verificar_cadeia() is True


@pytest.mark.django_db
def test_adulteracao_quebra_a_cadeia(cliente):
    registrar("evento_um", {"a": 1}, cliente=cliente)
    registrar("evento_dois", {"b": 2}, cliente=cliente)

    # Adulteração direta no banco (burlando o save) é detectada na verificação.
    Auditoria.objects.filter(evento="evento_um").update(dados={"a": 999})
    assert verificar_cadeia() is False


@pytest.mark.django_db
def test_update_de_registro_levanta_erro(cliente):
    registro = registrar("evento_imutavel", {"x": 1}, cliente=cliente)
    registro.dados = {"x": 2}
    with pytest.raises(ErroAuditoriaImutavel):
        registro.save()


@pytest.mark.django_db
def test_delete_de_registro_levanta_erro(cliente):
    registro = registrar("evento_permanente", {"x": 1}, cliente=cliente)
    with pytest.raises(ErroAuditoriaImutavel):
        registro.delete()
