# Step 3.4 — Property Table

O Step 3.4 adiciona uma visão normalizada do registo: uma linha por
propriedade, numa sheet separada chamada `Property Table`. O modo existente
por contrato permanece como default e não sofre migração automática.

## Execução

```bash
PYTHONPATH=src python3 -m doc_register scan --property-table
PYTHONPATH=src python3 -m doc_register watch --property-table
PYTHONPATH=src python3 -m doc_register scan --property-table --reprocess-cadernetas
```

`--property-table` não se aplica a `property-scan` ou `property-watch`, pois
esses comandos pertencem ao pipeline predial independente e já utilizam a
sheet `Property Extraction`.

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

Quando nenhuma propriedade estruturada é encontrada, é criada uma única
linha de controlo com `property_row_status = unresolved_no_property`. Isso
impede reprocessamentos infinitos e torna a falha visível sem inventar uma
matriz.

## Idempotência

`property_row_id` combina o SHA-256 do contrato com a chave matricial. Cada
escrita remove primeiro todas as linhas do mesmo SHA-256 e grava o conjunto
atual numa única operação protegida pelo lock do workbook. Reprocessar um
contrato não duplica linhas e também remove propriedades que deixaram de ser
suportadas pela nova execução.

O `raw_json` de cada linha recebe `step3_4_property_table`, com versão,
posição, total de propriedades, chave matricial, estado de correspondência e
páginas de origem. `property_json` contém apenas a propriedade daquela linha.

## Testes

```bash
PYTHONPATH=src python3 -m unittest tests.test_step3_4 -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
