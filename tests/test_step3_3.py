from __future__ import annotations

import sys
import unittest

from doc_register.models import ExtractionResult
from doc_register.document_structure import ANNEX_CADERNETA, ANNEX_CRP, PARTY_SECTION, detect_document_structure
from doc_register.source_ranking import RankedValue, select_ranked, source_score
from doc_register.step33_engine import (
    apply_field_centric_extraction,
    prepare_field_centric_extraction,
)
from doc_register.validators.output_safety import apply_output_safety
from doc_register.detectors import DeterministicSignals
from doc_register.validators.validator import validate_result


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


def test_multiple_cadernetas_use_the_unique_contract_matrix_key() -> None:
    document = """[Page 1]
    CONTRATO DE ARRENDAMENTO
    ENTRE: Ana Maria Lopes, de ora em diante designada por Senhorio.
    Considerando que: o prédio está inscrito sob o artigo matricial 456, secção M.
    Cláusula 1
    [Page 4] CADERNETA PREDIAL RÚSTICA
    [Page 5] IDENTIFICAÇÃO DO PRÉDIO
    ARTIGO MATRICIAL: 123
    SECÇÃO: K
    NOME/LOCALIZAÇÃO PRÉDIO: Quinta Errada
    [Page 6] CADERNETA PREDIAL RÚSTICA
    [Page 7] IDENTIFICAÇÃO DO PRÉDIO
    ARTIGO MATRICIAL: 456
    SECÇÃO: M
    NOME/LOCALIZAÇÃO PRÉDIO: Herdade Certa
    """
    report = prepare_field_centric_extraction(document)
    result = apply_field_centric_extraction(
        ExtractionResult(document_category="lease_contract"), report
    )
    assert result.property_article == "456"
    assert result.property_section == "M"
    assert result.property_name == "Herdade Certa"


def test_corrupted_party_and_hallucinated_prices_are_not_published() -> None:
    document = """CONTRATO DE ARRENDAMENTO
    ENTRE: AC os Je, de ora em diante designado por Senhorio.
    Considerando que: o imóvel é objeto do contrato.
    Cláusula 1"""
    report = prepare_field_centric_extraction(document)
    result = apply_field_centric_extraction(
        ExtractionResult(
            document_category="lease_contract",
            lessor="AC os Je",
            option_price="PT50003501740005469590060",
            purchase_price="150000.00",
            assignment_price="150000.00",
        ),
        report,
    )
    assert result.lessor == ""
    assert result.option_price == ""
    assert result.purchase_price == ""
    assert result.assignment_price == ""


def test_output_safety_clears_invalid_bank_and_irrelevant_property_values() -> None:
    result = apply_output_safety(ExtractionResult(
        document_category="bank_details",
        iban="P750003504770000249990004",
        nib="2477002499900",
        property_number="17; 102",
    ))
    assert result.iban == ""
    assert result.nib == ""
    assert result.property_number == ""


def test_output_safety_blocks_birth_date_and_party_address_as_document_metadata() -> None:
    report = prepare_field_centric_extraction(FIELD_CENTRIC_FIXTURE)
    result = apply_field_centric_extraction(
        ExtractionResult(
            document_category="lease_contract",
            document_date="22-02-1996",
            property_address="Avenida das Tulipas, 6, Algés",
            property_display_name="Kare rete CELAS",
        ), report,
    )
    result = apply_output_safety(result, document_text=FIELD_CENTRIC_FIXTURE)
    assert result.document_date == ""
    assert result.property_address == ""
    assert result.property_display_name == "Margaceira"


def test_real_template_notarization_beats_degraded_identity_ocr() -> None:
    text = """[Page 1]
    CONTRATO DE ARRENDAMENTO
    GESTO ENERGIA, S.A., com sede em Algés, e Número de Identificação de Pessoa
    Colectiva 508 567 475, de ora em diante designada por “Arrendatária”.
    Considerando que:
    O prédio é objeto do contrato.
    Cláusula 1
    [Page 20]
    ANEXO IV Identificação dos Senhorios
    LOPES<MENDES<<FELISBINASMARIAS
    [Page 30]
    RECONHECIMENTO DE ASSINATURA
    Reconheço as assinaturas, no documento anterior, que corresponde a um Contrato de
    Arrendamento, de Felisbina Maria Lopes Mendes, titular do Cartão de Cidadão, e de
    Nuno Miguel Telo Preto, titular do Cartão de Cidadão, e de Augusto Maria Mendes,
    titular do Bilhete de Identidade e de Dirce da Assunção Manso Mendes, titular do
    Bilhete de Identidade, todos na qualidade de Senhorios.
    """
    report = prepare_field_centric_extraction(text)
    result = apply_field_centric_extraction(
        ExtractionResult(document_category="lease_contract"), report
    )
    assert result.lessor == (
        "Felisbina Maria Lopes Mendes; Nuno Miguel Telo Preto; "
        "Augusto Maria Mendes; Dirce da Assunção Manso Mendes"
    )
    assert result.lessee == "GESTO ENERGIA, S.A."
    assert result.lessee_tax_id == "508567475"


def test_real_template_annual_per_hectare_money_and_mid_document_signature() -> None:
    text = """[Page 1]
    CONTRATO DE ARRENDAMENTO
    ENTRE: FREGUESIA DE PENAS ROIAS, contribuinte fiscal 508574935, na qualidade de
    Promitente Senhoria. GESTO ENERGIA, S.A., com sede em Algés, NIPC 508567475,
    de ora em diante designada por “Arrendatária”.
    Considerando que: o prédio é objeto do contrato.
    Cláusula 1
    [Page 8]
    Cláusula 5 (Renda e Forma de Pagamento)
    A renda anual corresponde 1.000,00 EUR por hectare efetivamente ocupado.
    A Renda será paga anualmente.
    Cláusula 6
    [Page 15]
    Elaborado e assinado em Mogadouro, em 13 de Abril de 2023.
    SENHORIA ARRENDATÁRIA
    [Page 16] ANEXO I
    [Page 29] RECONHECIMENTO DE ASSINATURA
    """
    report = prepare_field_centric_extraction(text)
    result = apply_field_centric_extraction(
        ExtractionResult(document_category="lease_contract"), report
    )
    assert result.rent_amount == "1000.00"
    assert result.rent_frequency == "annual"
    assert result.rent_unit == "per_hectare"
    assert result.rent_basis == "effectively_occupied_area"
    assert result.signed_date == "2023-04-13"


def test_real_template_multiple_properties_are_aligned_and_flat_fields_abstain() -> None:
    text = """[Page 1]
    CONTRATO DE ARRENDAMENTO
    ENTRE: FREGUESIA DE PENAS ROIAS, contribuinte fiscal 508574935, na qualidade de
    Promitente Senhoria. GESTO ENERGIA, S.A., NIPC 508567475, de ora em diante
    designada por “Arrendatária”.
    Considerando que:
    “Serra da Abelha”, artigo 4, da secção L;
    “Serra da Senhora”, artigo 29, da secção F;
    “Juncal e Monterroso”, artigo 98, da secção G.
    Cláusula 1
    [Page 17]
    CADERNETA PREDIAL RÚSTICA Modelo B
    IDENTIFICAÇÃO DO PRÉDIO
    SECÇÃO: L ARTIGO MATRICIAL Nº: 4 ARV:
    NOME/LOCALIZAGAO PRÉDIO
    Serra da Abelha
    ELEMENTOS DO PRÉDIO
    Área Total (ha): 76,775000
    TITULARES
    Identificação fiscal: 508574935 Nome: FREGUESIA DE PENAS ROIAS
    Morada: Penas Roias Tipo de titular: Propriedade plena
    [Page 19]
    CADERNETA PREDIAL RÚSTICA Modelo B
    IDENTIFICAÇÃO DO PRÉDIO
    SECÇÃO: F ARTIGO MATRICIAL Nº: 29 ARV:
    NOME/LOCALIZAGAO PRÉDIO
    Serra da Senhora
    ELEMENTOS DO PRÉDIO
    Área Total (ha): 48,881100
    TITULARES
    Identificação fiscal: 508574935 Nome: FREGUESIA DE PENAS ROIAS
    Morada: Penas Roias Tipo de titular: Propriedade plena
    [Page 21]
    CADERNETA PREDIAL RÚSTICA Modelo B
    IDENTIFICAÇÃO DO PRÉDIO
    SECÇÃO: G ARTIGO MATRICIAL Nº: 98 ARV:
    NOME/LOCALIZAGAO PRÉDIO
    Juncal e Monterroso
    ELEMENTOS DO PRÉDIO
    Área Total (ha): 16,387500
    TITULARES
    Identificação fiscal: 508574935 Nome: FREGUESIA DE PENAS ROIAS
    Morada: Penas Roias Tipo de titular: Propriedade plena
    """
    report = prepare_field_centric_extraction(text)
    result = apply_field_centric_extraction(
        ExtractionResult(document_category="lease_contract"), report
    )
    assert result.property_name == ""
    assert result.property_article == ""
    assert result.property_section == ""
    assert result.owner_name == "FREGUESIA DE PENAS ROIAS"
    assert [item["matrix_key"] for item in report.cadastral_properties] == ["4-L", "29-F", "98-G"]
    assert all(item["matched_contract_identity"] for item in report.cadastral_properties)
    assert any(item["reason"] == "multiple_properties_preserved_in_structured_output" for item in report.blocked)


def test_contract_recital_is_not_misclassified_as_registry_annex() -> None:
    text = """[Page 1]
    CONTRATO DE ARRENDAMENTO
    ENTRE: Ana Maria Lopes, NIF 220957592, na qualidade de Senhoria.
    Considerando que: o prédio está descrito na Conservatória do Registo Predial e
    comprova-se pela caderneta predial junta como Anexo I.
    Cláusula 1
    """
    structure = detect_document_structure(text)
    assert not structure.of_type(ANNEX_CRP)
    assert not structure.of_type(ANNEX_CADERNETA)


def test_authoritative_caderneta_evidence_suppresses_global_signal_conflict() -> None:
    report = prepare_field_centric_extraction(FIELD_CENTRIC_FIXTURE)
    result = apply_field_centric_extraction(
        ExtractionResult(document_category="lease_contract"), report
    )
    validated = validate_result(
        result,
        signals=DeterministicSignals(property_article="999", property_section="X"),
        recover=False,
        entity_resolution=False,
    )
    assert "property_article_conflicts_with_deterministic" not in validated.validation_issues
    assert "property_section_conflicts_with_deterministic" not in validated.validation_issues


def test_property_document_uses_caderneta_evidence_without_contract_party_rules() -> None:
    document = """CADERNETA PREDIAL RÚSTICA
    IDENTIFICAÇÃO DO PRÉDIO
    ARTIGO MATRICIAL: 80
    SECÇÃO: J
    NOME/LOCALIZAÇÃO PRÉDIO: Herdade do Norte
    TITULARES
    Nome: Ana Maria da Silva
    Tipo de titular: Propriedade plena
    """
    report = prepare_field_centric_extraction(document)
    result = apply_field_centric_extraction(
        ExtractionResult(document_category="property_document"), report
    )
    assert result.property_article == "80"
    assert result.owner_name == "Ana Maria da Silva"


def load_tests(loader, tests, pattern):
    module = sys.modules[__name__]
    functions = [
        getattr(module, name)
        for name in sorted(dir(module))
        if name.startswith("test_") and callable(getattr(module, name))
    ]
    return unittest.TestSuite(unittest.FunctionTestCase(function) for function in functions)
