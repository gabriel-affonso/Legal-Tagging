"""Versioned rule catalogue; examples are regressions, not calibration data."""
VERSION='5.5.1-rules-1'


def rule_catalog():
    families={
        'party_name':('explicit_identification',['lease_contract','cadastral_record'],['Senhorio: Maria Ribeiro'],['parte incumpridora','dados pessoais Nome']),
        'tax_id':('identified_party',['lease_contract','cadastral_record'],['NIF 196415209'],['invalid_checksum','unassociated_number']),
        'labelled_role':('party_label',['lease_contract'],['Arrendatária: Empresa, S.A.'],['casada com','representada por']),
        'collective_definition':('outorgante_block',['lease_contract'],['doravante designados Senhorios'],['hypothetical_party']),
        'property_block':('bounded_property_description',['lease_contract','cadastral_record'],['artigo matricial 68'],['Artigo 306 do Código Civil','corporate_address']),
        'area':('bounded_property_description',['lease_contract','cadastral_record'],['área total de 25.650 m2'],['área útil aproximada']),
        'leased_area':('lease_object',['lease_contract'],['área útil aproximada de 3,7 ha'],['distribute_joint_area_to_each_property']),
        'cadastral_name':('property_name_heading',['cadastral_record'],['NOME/LOCALIZAÇÃO PRÉDIO'],['Nome: titular','ELEMENTOS DO PRÉDIO']),
        'cadastral_area':('property_total_label',['cadastral_record'],['Área Total (ha): 1,64'],['Área: parcela agrícola']),
        'cadastral_holder':('titulares_block',['cadastral_record'],['Propriedade plena Parte: 1/2'],['quota_greater_than_one']),
        'signature_date':('signature_context',['lease_contract'],['Assinado em 22 de Dezembro de 2020'],['data da sua assinatura','notarial_context']),
        'duration':('term_clause',['lease_contract'],['duração de 29 anos e 11 meses'],['prazo máximo de três anos']),
        'deadline':('deadline_clause',['lease_contract'],['prazo de três meses após notificação'],['date_without_trigger']),
        'condition':('condition_clause',['lease_contract'],['Condição Suspensiva'],['infer_occurrence_from_definition']),
        'start_trigger':('effect_clause',['lease_contract'],['início depende da Condição Suspensiva'],['signature_implies_lease_start']),
        'rent':('rent_clause',['lease_contract'],['renda anual de 1.000,00 EUR por hectare'],['penalização','valor patrimonial']),
        'percentage':('percentage_clause',['lease_contract'],['reserva de 25% da renda anual'],['25_percent_as_EUR','withholding_as_reservation']),
        'receipt_money':('amount_label',['payment_receipt','payment_correspondence','bank_payment_confirmation'],['valor líquido de 2.775,00 EUR'],['ambiguous_decimal']),
        'payment_date':('date_label',['payment_receipt','payment_correspondence','bank_payment_confirmation'],['data-valor: 18/04/2024'],['letter_date_as_bank_date']),
        'payment_reference':('reference_clause',['payment_receipt','payment_correspondence'],['artigos 68 e 530'],['registry_530_as_article_306']),
        'payment_kind':('document_type',['payment_receipt','payment_correspondence','bank_payment_confirmation'],['Envio de recibo'],['correspondence_proves_bank_settlement']),
        'domain':('explicit_local_context',['lease_contract','cadastral_record','payment_receipt','payment_correspondence','bank_payment_confirmation'],['explicit_event_or_reference'],['unanchored_value']),
    }
    return {'version':VERSION,'calibration_status':'not_calibrated','rules':[
        {'id':name,'section':scope,'source_types':types,'positive_examples':positive,'negative_examples':negative,
         'evidence_requirement':'immutable_text_offsets_and_subject_anchor','acceptance':'needs_review'}
        for name,(scope,types,positive,negative) in families.items()]}
