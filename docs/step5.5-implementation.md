# Step 5.5.1 — implementação da especificação semântica

## Modelo canónico

O PDF contém páginas e observações imutáveis. Um documento lógico agrupa regiões e seções; cada região permanece vinculada à página original. Entidades possuem tipo e evidência de identidade. Afirmações possuem predicado, valor validado para esse predicado, sujeito, escopo e evidência. Pessoas, organizações, imóveis, objeto arrendado, eventos e obrigações têm identidades distintas. Ligações entre fontes permanecem propostas até revisão.

Datas literais, referências a eventos, durações e prazos são tipos distintos. Área guarda conceito, unidade e aproximação. Obrigações guardam taxa/fórmula e referências a área, evento e obrigação base; pagamentos guardam bruto/retenção/líquido separadamente. Revisão e derivações são registros próprios e não reescrevem a observação.

## Mapa de campos

O catálogo executável é gerado a partir dos schemas do registro, de propriedades e das tabelas normalizadas. Cada coluna possui destino canónico ou estado unmapped, origem permitida e política de sobrescrita. Origem de uma afirmação é independente da coluna: documental, derivado, externo, operacional ou humano. O comando field-map também lê cabeçalhos de um workbook. Foi conferido o workbook de testes públicos, conforme o relatório de validação; o Excel operacional privado não foi fornecido.

## Plano por módulo

1. canonical/values: tipos, predicados, cardinalidades e integridade referencial.
2. document_map/recognition: páginas, texto e OCR persistentes, fronteiras e continuidade.
3. extraction: blocos prediais completos, partes, pagamentos, cláusulas temporais/económicas.
4. resolution: conflitos, ligações propostas, verificações financeiras e derivações.
5. tasks/models: evidência selecionada por pergunta, JSON Schema, limites e checkpoints.
6. pipeline/storage: original preservado, execuções imutáveis, cache versionado e índice SQLite.
7. review/export/evaluation: decisões imutáveis, revisão navegável, staging normalizado e métricas por camada.
8. CLI/migration/tests: replay de OCR legado, comandos e regressões semânticas com relações completas.

## Critérios

Propriedades nunca compartilham atributos por proximidade arbitrária. Página ilegível permanece inventariada. Contexto multipágina mantém contrato e evidência original. Schema rejeita tipos cruzados. Revisão exige autor e evidência. Nenhuma exportação sobrescreve arquivo existente. Testes sintéticos verificam software; desempenho e acurácia reais exigem os PDFs e anotações originais.
