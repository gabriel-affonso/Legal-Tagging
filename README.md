# Document Management Ollama

Programa local para criar um register de documentos a partir de PDFs sincronizados do SharePoint/OneDrive, usando regras determinísticas, OCR local, Ollama local e Excel. A v2 foi desenhada para documentos confidenciais: não usa cloud, APIs externas ou fallback remoto.

## Fluxo

1. O Power Automate continua a copiar os PDFs aprovados para uma pasta no SharePoint.
2. O OneDrive/SharePoint Sync coloca essa pasta no seu PC.
3. Este programa monitora a pasta local, copia PDFs novos para `processing/`, extrai texto/OCR, aplica regras determinísticas, faz uma primeira avaliação local do tipo de documento, seleciona/destaca trechos relevantes, faz uma segunda avaliação local com prompt específico da tipologia, executa validação/recuperação determinística, chama o Step 2 AI Reviewer quando houver issues elegíveis e adiciona uma linha em `document_register.xlsx`.
4. Cada PDF é identificado por SHA-256, então o mesmo documento não é registrado duas vezes.
5. A saída do LLM é validada e normalizada antes de entrar no Excel.
6. Cada execução grava eventos em `logs/processing.log`.

## Pipeline v2

O processamento é híbrido e conservador:

1. Regras determinísticas por nome do arquivo e regex no texto.
2. Extração local de IBAN, NIB, BIC/SWIFT, NIF/NIPC, datas, valores e sinais prediais.
3. Primeira avaliação do Ollama local: usa apenas as duas primeiras páginas com texto ou, quando não houver marcação de páginas, as primeiras 500 palavras. O objetivo é definir a categoria ampla e o tipo/subtipo provável.
4. Seleção de trechos relevantes: primeiras páginas e janelas perto de termos como contrato, caderneta, artigo, IBAN, pagamento, beneficiário e ordenante.
5. Enriquecimento local do texto: datas, valores, meses e palavras-chave importantes são marcados no texto com etiquetas como `[[DATE:...]]`, `[[MONEY:...]]`, `[[MONTH:...]]` e `[[KEYWORD:...]]`.
6. Segunda avaliação do Ollama local: usa o resultado da primeira avaliação, os sinais determinísticos e os trechos destacados. A partir da categoria ampla, entra numa árvore de prompts específica para contrato, documento predial, dados bancários, comprovativo de pagamento, fatura/recibo, documento fiscal, identidade, correspondência ou outros.
7. Extração com schema específico por categoria.
8. Sprint 1B: recuperação determinística de campos críticos quando houver evidência direta no texto.
9. Sprint 1A: validação determinística, `quality_score`, prioridade e estado de validação.
10. Step 2 AI Reviewer: revisão local por Ollama apenas para campos críticos ainda problemáticos, com evidência curta e propostas validadas antes de alterar o registo.
11. Nova validação determinística; só o validator pode atribuir `AUTO_APPROVED`.
12. Marcação automática de `needs_review`/`human_review_required` quando houver baixa confiança, conflito de categoria, OCR fraco, campos essenciais ausentes, valores suspeitos ou proposta de IA que precise de validação humana.

Se o Ollama local expirar, o pipeline não perde o documento inteiro:

- timeout na primeira avaliação: registra resultado baseado nas regras determinísticas e marca revisão;
- timeout na segunda avaliação: mantém a classificação inicial, grava a nota de timeout e marca revisão.

Regras de segurança importantes:

- IBAN, NIB, BIC/SWIFT, número de conta ou referência bancária nunca devem ser usados como `payment_amount`.
- Nome de banco ou titular de conta não deve virar `property_name`.
- `payment_amount` só é aceite quando há contexto claro de valor/montante/total/pagamento.
- `property_address` deve ser a morada/localização do imóvel; morada fiscal ou sede vai para revisão e pode ser movida para `owner_address`.
- Documentos com dados bancários mas sem prova clara de pagamento são tratados como `bank_details`, não `payment_proof`.

## Instalação

```bash
cd "/Users/gabriel.affonso/Documents/Document Management Ollama"
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Instale e rode o Ollama:

```bash
ollama pull qwen3:8b
ollama serve
```

Instale OCR local para PDFs escaneados:

```bash
brew install ocrmypdf tesseract tesseract-lang
```

Em outro terminal, copie a configuração:

```bash
cp config.example.json config.json
```

Edite `config.json` e ajuste principalmente:

- `input_dir`: pasta local sincronizada com SharePoint/OneDrive.
- `processing_dir`: pasta onde os PDFs serão copiados antes do processamento.
- `log_dir`: pasta dos logs persistentes.
- `excel_path`: caminho do arquivo Excel que será criado/atualizado.
- `ollama_model`: modelo local disponível no Ollama. Nesta máquina, `qwen3:8b` já aparece instalado.
- `ollama_timeout_seconds`: tempo máximo para cada chamada ao Ollama local antes de marcar revisão e seguir o lote.
- `llm_classification_words`: número de palavras usadas na primeira avaliação quando o PDF não tem marcação de páginas.
- `llm_extraction_max_chars`: limite de caracteres destacados enviados para a segunda avaliação.
- `ocr_enabled`: ativa OCR quando o PDF não tiver texto legível suficiente.
- `ocr_language`: use `por+eng` para documentos em português e inglês.
- `ocr_min_text_chars`: mínimo de caracteres extraídos antes de considerar que OCR é necessário.
- `ai_review_enabled`: ativa o Step 2 AI Reviewer após a validação inicial.
- `ai_review_timeout_seconds`: tempo máximo da chamada focada ao Ollama no Step 2.
- `ai_review_max_evidence_chars`: limite de caracteres de evidência enviados ao Step 2.

## Usar uma vez

```bash
source .venv/bin/activate
doc-register scan --config config.json
```

## Deixar monitorando

```bash
source .venv/bin/activate
doc-register watch --config config.json
```

Por padrão, o `watch` verifica a pasta a cada `poll_interval_seconds`.

## Campos gravados no Excel

- `processed_at`
- `source_file_name`
- `copied_file_path`
- `sha256`
- `file_created_at`
- `file_modified_at`
- `processing_status`
- `processed_ok`
- `needs_review`
- `review_reason`
- `reviewed_by`
- `reviewed_at`
- `error_message`
- `document_category`
- `document_type`
- `document_subtype`
- `document_date`
- `summary`
- `language`
- `confidence`
- `extraction_notes`
- `signed_date`
- `contract_type`
- `lessor`
- `lessee`
- `property_name`
- `property_number`
- `property_display_name`
- `property_article`
- `property_section`
- `property_parish`
- `property_municipality`
- `property_district`
- `property_location`
- `property_address`
- `owner_name`
- `owner_tax_id`
- `owner_address`
- `contract_start_date`
- `contract_end_date`
- `rent_payment_day`
- `monthly_rent`
- `currency`
- `bank_account_holder`
- `iban`
- `nib`
- `bic_swift`
- `bank_name`
- `bank_account_number`
- `payment_date`
- `payer`
- `payee`
- `payment_amount`
- `payment_method`
- `payment_reference`
- `payment_description`
- `text_source`
- `native_text_chars`
- `ocr_text_chars`
- `ai_review_status`
- `ai_reviewed_fields`
- `ai_accepted_fields`
- `ai_rejected_fields`
- `ai_review_confidence`
- `ai_review_model`
- `ai_review_duration_seconds`
- `ai_review_reason`
- `human_review_required`
- `deterministic_json`
- `llm_json`
- `raw_json`

## Step 2 AI Reviewer

O Step 2 não reprocessa documentos já `AUTO_APPROVED` e não substitui o validator. Ele só atua quando a validação inicial deixa issues que a IA pode resolver consultando o texto do próprio documento, como `missing_lessor`, `generic_lessee`, `missing_signed_date`, `missing_property_article`, `missing_property_section` ou `missing_monthly_rent`.

Cada proposta inclui campo, valor, evidência e confiança. O sistema só aplica automaticamente propostas com confiança suficiente e validação local positiva: nomes precisam estar no texto e não podem ser genéricos; datas precisam ter contexto de assinatura; artigo/secção precisam aparecer perto de termos prediais; renda mensal precisa ter periodicidade mensal explícita. Propostas de confiança intermediária ficam marcadas para revisão humana.

O relatório completo é gravado em `raw_json["ai_review"]`, e as colunas `ai_review_*` permitem medir no Excel/Power BI o que foi resolvido sem IA, resolvido pelo Step 2, rejeitado pelo validator ou encaminhado para validação humana.

A validação final depois do Step 2 é executada em modo puro, sem nova recuperação determinística. Isso evita que a etapa final altere campos novamente e garante que issues antigas, como `generic_lessor`, desapareçam quando já não forem produzidas pelas regras atuais.

Estados técnicos são separados de revisão semântica: `NEEDS_OCR` indica ausência de texto utilizável, `OCR_FAILED` indica falha/indisponibilidade de OCR com texto insuficiente, `TECHNICAL_ERROR` indica erro de processamento e `NEEDS_HUMAN_REVIEW` indica proposta ou condição que precisa de decisão humana.

## Categorias oficiais

O Ollama trabalha em duas avaliações separadas:

1. Classificação inicial com começo do documento: define `document_category`, `document_type` e `document_subtype`.
2. Extração por árvore de prompts: escolhe um prompt específico a partir da categoria ampla e só então extrai os campos do schema correto.

As categorias oficiais são:

- `lease_contract`: contrato de arrendamento.
- `property_document`: caderneta predial, certidão predial, CRP, registo predial, matriz ou averbamento.
- `bank_details`: IBAN, NIB, BIC/SWIFT, titular de conta e dados bancários sem prova clara de pagamento.
- `payment_proof`: comprovativo de transferência/pagamento com data, pagador, beneficiário e valor.
- `invoice_or_receipt`: fatura ou recibo que não seja claramente comprovativo bancário.
- `identity_document`: documentos de identificação pessoal ou empresarial.
- `tax_document`: documentos fiscais.
- `correspondence`: cartas, notificações e comunicações.
- `other`: documentos que nao encaixam nas categorias anteriores.

## Notas importantes

- PDFs digitais normalmente nao precisam de OCR. PDFs escaneados como imagem passam pelo `ocrmypdf` quando `ocr_enabled` estiver ativo.
- O Power Automate pode continuar fazendo o filtro de PDFs adicionados na última hora. Localmente, este programa também evita duplicados por hash.
- Para contratos de arrendamento, o schema pede signatários, arrendador, arrendatário, datas e renda mensal.
- `property_display_name` é montado automaticamente com nome, artigo, secção e número quando o LLM não preencher a coluna conjunta.
- `needs_review` fica `yes` quando a confiança é baixa/média, quando há notas de extração, quando a categoria determinística diverge da categoria do LLM, quando faltam campos essenciais ou quando há campos suspeitos.
- Erros de processamento são registrados no Excel com `processing_status=error` quando o arquivo já foi copiado para a área local.

## Validações locais

```bash
python3 -m py_compile src/doc_register/processor.py src/doc_register/validators/validator.py src/doc_register/ai_reviewer/*.py
PYTHONPATH=src python3 -m unittest tests/test_ai_reviewer.py tests/test_sprint1b.py tests/test_text_selection.py tests/test_ollama_client.py tests/test_detectors.py
```

Smoke test opcional com Ollama local:

```bash
DOC_REGISTER_RUN_OLLAMA_SMOKE=1 OLLAMA_MODEL=qwen3:8b PYTHONPATH=src python3 -m unittest tests/test_ai_reviewer.py
```
