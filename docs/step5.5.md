# Step 5.5 — pipeline canónica local

O Step 5.5 é uma nova pipeline piloto. Não altera o resultado do Step 5.0 e não escreve em workbooks operacionais. A saída é um grafo canónico de documentos lógicos, evidências localizáveis, entidades provisórias, afirmações tipadas, relações e tarefas pendentes.

## Executar

```sh
.venv/bin/python -m pip install -r requirements-step5.lock.txt
.venv/bin/python -m pip install --no-deps .
./scripts/doc-register-step55 scan /caminho/documento.pdf --output /caminho/step55-runs
```

Também é possível executar `doc-register step55 scan ...` após a instalação do pacote. O launcher lê sempre o código deste checkout. O resultado fica em `result.json`; `staging-projection.json` é apenas uma visão de revisão e contém `export_blocked: true`.

## O que o piloto implementa

- Classificação sem herdar automaticamente a tipologia de um contrato para RGPD, identidade ou planta.
- Evidência com página, região, offsets, quote, coordenadas e dependência de leitura.
- Entidades provisórias tipadas para contrato, pessoa, organização, imóvel, objeto arrendado, pagamento e obrigação financeira.
- Regras contextuais para comprovativos/correspondência: bruto, retenção, líquido, data documental e artigos apenas como referências não resolvidas entre documentos.
- Regras para contrato: datas portuguesas, duração, gatilho de condição suspensiva, renda anual por hectare, percentagem de reserva, artigos, descrição predial, freguesia, concelho e áreas com conceito próprio.
- Projeção de tarefas `parts`, `properties`, `temporal` e `financial` para uma fase LLM delimitada.
- Projeção de staging bloqueada: nenhuma afirmação é enviada ao Excel.

Todas as afirmações extraídas por regras continuam em `needs_review`. Isto evita transformar um OCR ou uma associação textual em dado operacional só porque o valor tem formato válido.

## Limites deliberados desta versão

O Step 5.5 faz OCR local em páginas sem texto nativo ou com texto abaixo do limiar configurado. A leitura OCR é uma observação independente e a respetiva falha fica registada como `ocr_failure`; nenhuma dessas situações torna a página “vazia”. O planejador apenas registra tarefas que exigiriam um modelo local: ainda não há chamadas LLM. Também não há resolvedor interdocumental, merge automático, interface de revisão humana, SQLite, exportador Excel nem interpretação de plantas. Esses limites aparecem no resultado em vez de serem tratados como ausência documental.

O planejador foi separado da execução de modelo porque o relatório demonstrou que chamadas por página eram caras e semanticamente inseguras. A próxima etapa deve conectar apenas tarefas selecionadas a um adaptador Ollama com JSON Schema, orçamento por tarefa, `keep_alive` curto e checkpoints; não deve restaurar chamadas obrigatórias por segmento.

## Garantias verificadas

Os testes do Step 5.5 cobrem comprovativo com bruto/retenção/líquido sem LLM, data documental distinta de data bancária, artigos 68/530 preservados como referências não vinculadas, RGPD sem imóvel/IBAN, duração distinta de data, taxa de renda distinta de percentagem e medidas de escopo distinto. A suite completa do checkout foi executada com 146 testes aprovados e um smoke test local do Ollama ignorado por configuração.
