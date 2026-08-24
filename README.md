# Document Management Ollama

Programa local para criar um register de documentos a partir de PDFs sincronizados do SharePoint/OneDrive, usando regras determinísticas, OCR local, Ollama local e Excel. A v2 foi desenhada para documentos confidenciais: não usa cloud, APIs externas ou fallback remoto.

## Fluxo

1. O Power Automate continua a copiar os PDFs aprovados para uma pasta no SharePoint.
2. O OneDrive/SharePoint Sync coloca essa pasta no seu PC.
3. Este programa monitora a pasta local, copia PDFs novos para `processing/`, extrai texto/OCR, aplica regras determinísticas, faz uma primeira avaliação local do tipo de documento, seleciona/destaca trechos relevantes, faz uma segunda avaliação local com prompt específico da tipologia e adiciona uma linha em `document_register.xlsx`.
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
8. Validação pós-LLM, normalização e regras negativas.
9. Marcação automática de `needs_review` quando houver baixa confiança, conflito de categoria, OCR fraco, campos essenciais ausentes ou valores suspeitos.

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
- `ocr_enabled`: ativa OCR quando o PDF não tiver texto legível suficiente.
- `ocr_language`: use `por+eng` para documentos em português e inglês.
- `ocr_min_text_chars`: mínimo de caracteres extraídos antes de considerar que OCR é necessário.

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
- `deterministic_json`
- `llm_json`
- `raw_json`

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
python3 -m compileall src
PYTHONPATH=src python3 -m unittest tests/test_detectors.py tests/test_text_selection.py
```
