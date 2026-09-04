# Step 4.0 — workbook normalizado

Step 4.0 publica um workbook operacional normalizado sem retirar a visão
forense da pipeline. A versão de schema é `3`.

## Fluxo

1. A pipeline extrai e valida o documento como antes.
2. `audit.DocumentRegister` conserva o registo técnico completo por SHA-256.
   `audit.PropertyEvidence`, `audit.FieldEvidence` e `audit.Conflicts`
   guardam evidência, páginas e conflitos.
3. A consolidação gera `Entrada_Rapida`. Cada linha representa apenas uma
   combinação contrato–proprietário–parcela sustentada pela evidência; não há
   produtos cartesianos entre todos os proprietários e todas as parcelas.
4. `NormalizedWorkbookRegister` é o único normalizador. Ele valida a entrada,
   atribui IDs determinísticos e faz upsert nas folhas `db.*`.

`db.ProprietarioTerreno.terreno_id` tem uma semântica única: contém o
`parcela_id` físico de `db.Terreno`. `terreno_grupo_id` não é usado como chave
estrangeira nessa relação.

## Configuração

```json
{
  "output_format": "normalized",
  "normalized_output_path": "output/Malhadas_normalizado.xlsx",
  "normalized_template_path": "Nova_BD.xlsx",
  "normalized_batch_id": "LOTE-2026-09",
  "normalized_reference_mg_context": "MG-100"
}
```

`normalized_output_path` é opcional e, se ausente, usa `excel_path`.
`normalized_template_path` também é opcional. Quando não existe, a pipeline
cria o schema V3. Quando o template anterior contém `terreno_id`, a migração
preserva os dados e converte a coluna para `parcela_id` nas tabelas V3.

Os formatos disponíveis são:

- `normalized` (padrão): staging, `db.*` e auditoria.
- `legacy`: `Document Register` histórico.
- `both`: mantém também o exportador legado, mas só Step 4.0 escreve `db.*`.

```bash
PYTHONPATH=src python3 -m doc_register scan --config config.json --output-format normalized
PYTHONPATH=src python3 -m doc_register scan --config config.json --output-format both
# Depois de corrigir uma linha e definir estado_processamento=PENDENTE:
PYTHONPATH=src python3 -m doc_register normalize --config config.json
```

## Regras de identidade e segurança

- IDs usam SHA-256 sobre chaves canónicas e prefixos `ENT-`, `CTR-`, `PROP-`,
  `PAR-`, `TER-`, `CONT-`, `BANK-` e `REL-`.
- `referencia_mg*` ausente gera `REQUER_REVISAO`; nenhum contrato ou relação
  contratual é publicado.
- Proprietários usam NIF válido, documento ou nome + referência MG nessa ordem.
  Um NIF inválido continua auditável, mas não é usado como identidade forte.
- Um IBAN só é publicado quando o titular documental coincide com o
  proprietário da entrada e o checksum é válido.
- `area_at_ha` e `area_medida_ha` derivam sempre de metros quadrados por
  divisão por 10 000. Área documental não é substituída por área matricial.
- Dados publicados numa execução anterior não são apagados por campos vazios
  numa nova execução; as entradas idênticas não são reprocessadas.
