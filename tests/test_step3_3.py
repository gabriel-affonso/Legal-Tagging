from __future__ import annotations

from doc_register.models import ExtractionResult
from doc_register.document_structure import ANNEX_CADERNETA, PARTY_SECTION, detect_document_structure
from doc_register.source_ranking import RankedValue, select_ranked, source_score
from doc_register.step33_engine import (
    apply_field_centric_extraction,
    prepare_field_centric_extraction,
)


FIELD_CENTRIC_FIXTURE = """[Page 1]
CONTRATO DE ARRENDAMENTO
ENTRE:
Ana Maria Lopes, NIF 220957592, e Augusto Mendes, NIF 123456789, todos na qualidade de Senhorios.
GESTO ENERGIA, S.A., NIPC 508567475, de ora em diante designada por Arrendatária.
Considerando que:
O prédio denominado por Kare rete CELAS está inscrito na matriz sob o artigo 999, secção X.
Cláusula 1
Objeto.
Cláusula 5
A renda mensal é de 1.200,00 EUR.
Cláusula 6
[Page 8]
CADERNETA PREDIAL RÚSTICA
IDENTIFICAÇÃO DO PRÉDIO
SECÇÃO: J
ARTIGO MATRICIAL Nº: 53
NOME/LOCALIZAÇÃO PRÉDIO: Margaceira
Freguesia de PENAS ROIAS
ELEMENTOS DO PRÉDIO
ÁREA TOTAL (HA): 2,5
[Page 9]
TITULARES
Nome: Ana Maria Lopes
Morada: Rua da Fonte
Tipo de titular: Propriedade plena
"""


def test_structure_finds_contract_and_annex_evidence_zones() -> None:
    structure = detect_document_structure(FIELD_CENTRIC_FIXTURE)
    assert structure.of_type(PARTY_SECTION)
    assert structure.of_type(ANNEX_CADERNETA)


def test_caderneta_source_lock_beats_degraded_contract_property() -> None:
    report = prepare_field_centric_extraction(FIELD_CENTRIC_FIXTURE)
    result = apply_field_centric_extraction(
        ExtractionResult(
            document_category="lease_contract",
            property_name="Kare rete CELAS",
            property_article="999",
            property_section="X",
            owner_name="GESTO ENERGIA, S.A.",
            lessee="GESTO ENERGIA, S.A.",
            lessee_tax_id="508567475",
        ),
        report,
    )

    assert result.property_name == "Margaceira"
    assert result.property_article == "53"
    assert result.property_section == "J"
    assert result.owner_name == "Ana Maria Lopes"
    evidence = result.raw_json["step3_3_field_centric"]["field_evidence"]
    assert evidence["property_name"]["source"] == "caderneta"
    assert evidence["owner_name"]["source"] == "caderneta"


def test_owner_matching_lessee_is_blocked_when_no_cadastral_owner_exists() -> None:
    text = """CONTRATO DE ARRENDAMENTO
    ENTRE: GESTO ENERGIA, S.A., de ora em diante designada por Arrendatária.
    Considerando que: O imóvel é objeto deste contrato.
    Cláusula 1"""
    report = prepare_field_centric_extraction(text)
    result = apply_field_centric_extraction(
        ExtractionResult(
            document_category="lease_contract",
            owner_name="GESTO ENERGIA, S.A.",
            owner_tax_id="508567475",
            lessee="GESTO ENERGIA, S.A.",
            lessee_tax_id="508567475",
        ),
        report,
    )
    assert result.owner_name == ""
    assert result.owner_tax_id == ""


def test_source_ranking_never_lets_residual_ocr_beat_caderneta() -> None:
    selected = select_ranked("property_name", [
        RankedValue("property_name", "Kare rete CELAS", "OCR_RESIDUAL", source_score("property_name", "OCR_RESIDUAL")),
        RankedValue("property_name", "Margaceira", "CADERNETA", source_score("property_name", "CADERNETA")),
    ])
    assert selected and selected.value == "Margaceira"
