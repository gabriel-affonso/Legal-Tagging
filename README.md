# Document Management Ollama

Programa local para criar um register de documentos a partir de PDFs sincronizados do SharePoint/OneDrive, usando Ollama para classificar e extrair metadados e Excel para guardar o resultado.

## Fluxo

1. O Power Automate continua a copiar os PDFs aprovados para uma pasta no SharePoint.
2. O OneDrive/SharePoint Sync coloca essa pasta no seu PC.
3. Este programa monitora a pasta local, copia PDFs novos para `processing/`, extrai texto/OCR, classifica o documento com Ollama, extrai os campos do tipo detectado e adiciona uma linha em `document_register.xlsx`.
4. Cada PDF é identificado por SHA-256, então o mesmo documento não é registrado duas vezes.
5. Cada execução grava eventos em `logs/processing.log`.

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
- `signed_date`
- `contract_type`
- `lessor`
- `lessee`
- `property_address`
- `contract_start_date`
- `contract_end_date`
- `rent_payment_day`
- `monthly_rent`
- `currency`
- `payment_date`
- `payer`
- `payee`
- `payment_amount`
- `payment_method`
- `payment_reference`
- `payment_description`
- `confidence`
- `extraction_notes`
- `text_source`
- `native_text_chars`
- `ocr_text_chars`
- `raw_json`

## Categorias do Ollama

O Ollama trabalha em duas etapas: primeiro classifica o documento; depois usa a categoria escolhida para extrair campos específicos. As categorias atuais são:

- `lease_contract`: contrato de arrendamento.
- `payment_proof`: comprovativo/comprovante de pagamento ou transferencia.
- `invoice_or_receipt`: fatura ou recibo que nao seja claramente comprovativo de pagamento.
- `identification`: documentos de identificacao ou registos.
- `tax_document`: documentos fiscais.
- `correspondence`: cartas, notificacoes e comunicacoes.
- `other`: documentos que nao encaixam nas categorias anteriores.

## Notas importantes

- PDFs digitais normalmente nao precisam de OCR. PDFs escaneados como imagem passam pelo `ocrmypdf` quando `ocr_enabled` estiver ativo.
- O Power Automate pode continuar fazendo o filtro de PDFs adicionados na última hora. Localmente, este programa também evita duplicados por hash.
- Para contratos de arrendamento, o prompt já pede signatários, arrendador, arrendatário, datas e renda mensal.
- `needs_review` fica `yes` quando a confiança é baixa/média, quando há notas de extração ou quando faltam campos essenciais de contratos/pagamentos.
- Erros de processamento são registrados no Excel com `processing_status=error` quando o arquivo já foi copiado para a área local.
