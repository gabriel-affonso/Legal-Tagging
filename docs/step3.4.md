# Step 3.5.1 — Property Table robusta

O modo `--property-table` cria uma projeção normalizada: uma linha por
propriedade de cada contrato de arrendamento, na sheet separada `Property
Table`. O modo existente por contrato permanece como default e não sofre
migração automática.

## Execução

```bash
PYTHONPATH=src python3 -m doc_register property-scan
PYTHONPATH=src python3 -m doc_register scan --property-table
PYTHONPATH=src python3 -m doc_register watch --property-table
PYTHONPATH=src python3 -m doc_register scan --property-table --reprocess-cadernetas
```

`--property-table` não se aplica a `property-scan` ou `property-watch`, pois
esses comandos pertencem ao pipeline predial independente e já utilizam a
sheet `Property Extraction`.

`property-scan` é obrigatório como etapa de elegibilidade antes de usar
`--property-table`. A tabela é uma materialização direta das sheets
`Property Extraction` e `Document Register`: não abre PDFs, não executa OCR e
não chama o LLM. Apenas linhas `lease_contract` da Property Extraction entram
na tabela; a Document Register fornece os campos contratuais já resolvidos.

Cada execução reconstrói integralmente a Property Table a partir dessas duas
fontes, substituindo linhas obsoletas de forma atómica.

## Elegibilidade e fontes

Só contratos classificados como `lease_contract` podem publicar linhas na
Property Table. `bank_details`, comprovativos, faturas, identificações e
documentos prediais isolados não recebem uma linha `unresolved_no_property`.
Eles podem continuar no Document Register e na Property Extraction, mas não
representam uma propriedade arrendada por si só.

Antes de publicar, o scan procura pelo mesmo SHA-256 na sheet `Property
Extraction`. Quando existe uma decisão de contrato nessa pipeline, as suas
propriedades estruturadas são reutilizadas, incluindo `processed_multi_property`.
As restantes fontes são consideradas por esta ordem: resolução final
estruturada, extração predial independente, resultado field-centric,
cadernetas internas, property pack e, por último, os campos escalares do
contrato. Assim, o writer não tenta resolver novamente um imóvel já auditado.

## Granularidade

Para um contrato com as matrizes `4-L`, `29-F` e `98-G`, são criadas três
linhas. Cada linha repete os campos contratuais, como senhorios,
arrendatária, NIPC, datas e renda, mas recebe apenas os campos cadastrais da
respetiva caderneta:

- `property_name`;
- `property_article`;
- `property_section`;
- `property_matrix_key`;
- freguesia, concelho e distrito;
- área total, também disponível numericamente em
  `property_total_area_m2`;
- titular cadastral e respetivo identificador;
- páginas e evidência da caderneta.

Campos prediais são limpos antes da aplicação de cada propriedade. Assim,
artigo, secção, área ou titular de duas cadernetas nunca são combinados na
mesma linha. `leased_parcel_area` só é copiada quando existe uma única
propriedade; em casos multi-property permanece vazia até existir uma ligação
documental explícita à respetiva matriz.

## Correspondência e revisão

Quando o contrato enumera matrizes, entram apenas cadernetas cuja
`property_matrix_key` corresponda a uma dessas identidades. Cadernetas
internas não confirmadas só são publicadas quando nenhuma chave contratual
foi reconhecida; nesse caso recebem:

- `property_match_status = internal_annex_unconfirmed`;
- `needs_review = yes`;
- `human_review_required = yes`;
- `review_reason = property_contract_identity_unconfirmed`.

Quando nenhuma propriedade estruturada é encontrada para um contrato, é
criada uma única linha de controlo com
`property_row_status = unresolved_no_property`. Isso torna a falha visível
sem inventar uma matriz.

Chaves matriciais só são publicadas quando têm artigo numérico e secção de uma
letra. Grupos repetidos com a mesma chave são unidos, preservando páginas e
preferindo os valores mais completos. Nomes prediais genéricos ou jurídicos
não são publicados como facto: são limpos e a linha segue para revisão.

## Idempotência

`property_row_id` combina `SHA-256::chave_matricial`. Cada
escrita remove primeiro todas as linhas do mesmo SHA-256 e grava o conjunto
atual numa única operação protegida pelo lock do workbook. Reprocessar um
contrato não duplica linhas e também remove propriedades que deixaram de ser
suportadas pela nova execução.

Cada linha mantém tanto `contract_operational_property_id` (por exemplo,
`PR098`) como `property_matrix_key` (por exemplo, `140-J`), sem os confundir.
O `raw_json` de cada linha recebe `step3_5_property_table`, com versão,
posição, total de propriedades, chave matricial, estado de correspondência e
páginas de origem. `property_json` contém apenas a propriedade daquela linha.

## Testes

```bash
PYTHONPATH=src python3 -m unittest tests.test_step3_4 -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
