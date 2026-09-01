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
10. Step 2.2: robustez pré/pós-IA com recuperação determinística de IBAN, labels claros de proprietário/titular, validação de qualidade de nomes e score de OCR.
11. Step 2.3: recuperação focada para contratos de arrendamento, usando zonas contratuais, hierarquia de data assinada, bloco predial agrupado e correção OCR fuzzy conservadora.
12. Step 2.4: segmentação local de cláusulas contratuais em blocos auditáveis como partes, imóvel, prazo, renda, pagamento e assinaturas; estes blocos podem recuperar campos ausentes antes da validação final.
13. Step 2.5: extração complementar ao OCR com preservação de layout, escolha por score entre texto nativo simples, texto nativo estruturado e OCR, e fallback conservador quando uma fonte não melhora a qualidade.
14. Step 2.6: resolução de entidades de proprietário e terreno, com extração determinística de códigos `PR/VA`, bloqueio de valores genéricos e barreira de evidência para campos críticos.
15. Step 2 AI Reviewer: revisão local por Ollama apenas para campos críticos ainda problemáticos, com janelas de evidência curtas e propostas validadas antes de alterar o registo.
16. Step 2.7: em contratos de arrendamento, a extração é orientada por estrutura. Primeiro identifica as partes e os blocos de imóvel/prazo/valores; depois o Ollama só recebe os contextos permitidos para cada campo. O parser de modelos empresariais reconhece marcadores como `doravante designados por Senhorios` e `designada por Arrendatária` antes da extração por IA.
17. Step 2.7 aplica peso por página (1: 1,00; 2: 0,80; 3: 0,50; restantes: 0,20), prefere campos ausentes a inferências, bloqueia entidades de ruído conhecidas e grava a origem/evidência de cada campo em `raw_json["step2_7_party_centric"]`.
18. Step 2.8: resolvedor final de contratos-container. Deteta zonas por página, mede qualidade OCR granular e reúne candidatos estruturados antes de publicar qualquer campo no Excel.
19. A autoridade é específica por campo: caderneta/registo vencem para artigo, secção e localização; designação explícita vence para papéis contratuais; e fórmulas de assinatura vencem para datas. Conflitos entre titulares declarados e cadastrais são preservados e exigem revisão.
20. A renda passa a suportar frequência e unidade. Renda anual por hectare preenche `rent_*` e `annual_rent`, sem inventar ou exigir `monthly_rent`.
21. Step 3.3.3: o resolvedor de contratos é o único publicador final após fontes especializadas. O audit inclui candidatos rejeitados, decisão final, evidência de titularidade separada (`contract_lessors`, `declared_owners`, `cadastral_owners`) e conflitos preservados.
22. A revisão por IA é segmentada em grupos de partes, imóvel e datas/termos; timeout num grupo não descarta decisões já aceites noutro grupo.
23. Step 3.5.1: `scan --property-table` e `watch --property-table` escrevem na sheet `Property Table`, com uma linha por matriz de contratos de arrendamento; anexos bancários e documentos prediais isolados não criam linhas vazias. A tabela reutiliza a `Property Extraction` pelo SHA-256 quando disponível.
24. Step 3.5: fallback visual local opcional com `qwen3-vl:4b-instruct` (família Qwen3-VL 4B). Depois de esgotar OCR, regras, LLM textual e o resolvedor contratual, analisa apenas as primeiras páginas de contratos com OCR fraco e campos críticos incertos. A imagem nunca sai da máquina nem é gravada em log.
25. Step 3.5 começa em modo sombra: propostas visuais são auditadas, mas não alteram o registo. Mesmo quando a aplicação é explicitamente ativada, texto manuscrito, caracteres incertos e conflitos exigem revisão humana.
26. Nova validação determinística; só o validator pode atribuir `AUTO_APPROVED`.
27. Marcação automática de `needs_review`/`human_review_required` quando houver baixa confiança, conflito de categoria, OCR fraco, campos essenciais ausentes, valores suspeitos ou proposta de IA que precise de validação humana.

Se o Ollama local expirar, o pipeline não perde o documento inteiro:

- timeout na primeira avaliação: registra resultado baseado nas regras determinísticas e marca revisão;
- timeout na segunda avaliação: mantém a classificação inicial, grava a nota de timeout e marca revisão.

Regras de segurança importantes:

- IBAN, NIB, BIC/SWIFT, número de conta ou referência bancária nunca devem ser usados como `payment_amount`.
- Nome de banco ou titular de conta não deve virar `property_name`.
- `payment_amount` só é aceite quando há contexto claro de valor/montante/total/pagamento.
- `property_address` deve ser a morada/localização do imóvel; morada fiscal ou sede vai para revisão e pode ser movida para `owner_address`.
- Documentos com dados bancários mas sem prova clara de pagamento são tratados como `bank_details`, não `payment_proof`.
- Valores de proprietário, terreno, artigo, secção, morada, moeda e valores monetários extraídos por LLM precisam ter evidência literal, normalizada ou fuzzy conservadora no texto antes de chegar ao Excel.
- `property_number` representa o número operacional interno derivado de códigos como `PR098` ou `VA140`; não deve receber NIF/NIPC, artigo matricial, número de cláusula, anexo ou placeholder.

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
- `pdf_layout_extraction_enabled`: ativa a extração layout-aware do Step 2.5 quando PyMuPDF estiver disponível.
- `pdf_layout_min_quality_score`: score mínimo para aceitar texto nativo/layout sem recorrer ao OCR.
- `ai_review_enabled`: ativa o Step 2 AI Reviewer após a validação inicial.
- `ai_review_timeout_seconds`: tempo máximo da chamada focada ao Ollama no Step 2.
- `ai_review_max_evidence_chars`: limite de caracteres de evidência enviados ao Step 2.
- `contract_clause_segmentation_enabled`: ativa o Step 2.4 para contratos.
- `contract_clause_segmentation_max_clause_chars`: limite por cláusula guardada em `raw_json["contract_clause_segmentation"]`.
- `vision_enabled`: ativa o Step 3.5. O padrão é `false`.
- `vision_model`: modelo visual local; usar `qwen3-vl:4b-instruct` em máquinas com 16 GB de RAM.
- `vision_apply_proposals`: mantenha `false` no período de modo sombra. Somente depois de validar uma amostra real deve ser ativado.
- `vision_auto_accept_enabled`: exige também `vision_apply_proposals=true`; não aceita manuscritos, texto incerto ou conflitos.

### Step 3.5 — recuperação visual local

Instale o modelo apenas quando for iniciar os testes locais:

```bash
ollama pull qwen3-vl:4b-instruct
```

Comece com a configuração abaixo. Ela executa o Vision apenas para contratos
com pontos críticos incertos e OCR fraco nas duas primeiras páginas, mas não
altera nenhum campo no Excel:

```json
{
  "vision_enabled": true,
  "vision_model": "qwen3-vl:4b-instruct",
  "vision_apply_proposals": false,
  "vision_auto_accept_enabled": false
}
```

As propostas ficam em `raw_json["vision_recovery"]` e nas colunas técnicas
`vision_*`. O modelo textual é descarregado antes da chamada visual e a
chamada visual usa `keep_alive: 0`; portanto os dois modelos não ficam
carregados juntos. Veja [Step 3.5](docs/step3.5.md) para gatilhos, segurança e
rollout.

## Usar uma vez

```bash
source .venv/bin/activate
doc-register scan --config config.json
```

## Identificação de terrenos

Para executar apenas a identificação de terrenos em contratos de arrendamento,
sem processar o registo documental completo, use:

```bash
source .venv/bin/activate
doc-register property-scan --config config.json
```

Para manter esta identificação a monitorizar a pasta de entrada:

```bash
source .venv/bin/activate
doc-register property-watch --config config.json
```

Ela grava um JSON por PDF em `property_extractions/` (ou no caminho definido
por `property_extraction_dir`) e uma linha na aba `Property Extraction` do
mesmo Excel. Não altera a aba principal, o arquivo ou o estado da pipeline
principal. A saída contém `owner_name`, `property_name`, `matrix_article`,
`matrix_section`, `area_m2`, confiança, origem dos dados e auditoria.

Primeiro, o processo confirma que o PDF é um contrato de arrendamento;
documentos não compatíveis recebem `status: "skipped"` e
`reason: "not_lease_contract"`. Nos casos de baixa confiança, procura uma
`Caderneta Predial` nos anexos e usa-a para recuperar ou validar os dados.
O Ollama local só é usado quando as extrações determinísticas do contrato e da
caderneta não forem suficientes.

No Step 3.0, uma extração de baixa confiança não chama o Ollama de imediato.
Antes, a pipeline lê apenas a cauda do PDF (as últimas 10 páginas
ou 30%, conforme o maior intervalo), localiza páginas de `Caderneta Predial`,
extrai nome, artigo, secção e área por regras e reconcilia os dados. A área e o
nome da caderneta têm prioridade; divergências de artigo ou secção ficam
registadas para revisão. A aba `Property Extraction` guarda a proveniência por
campo e a auditoria completa da recuperação.

O [Step 3.1](docs/step3.1.md) amplia essa recuperação para cadernetas que
existem como PDFs separados na mesma pasta do contrato. Cada nova execução
substitui o resultado anterior do mesmo PDF na aba `Property Extraction`.

O [Step 3.1.1](docs/step3.1.1.md) reutiliza OCR para indexar cadernetas e
CRPs escaneadas antes de tentar associá-las ao contrato.

O [Step 3.1.2](docs/step3.1.2.md) reconcilia conjuntamente o contrato com
cadernetas separadas e com cadernetas presentes no final do próprio PDF.
O [Step 3.1.3](docs/step3.1.3.md) reforça a leitura de anexos no próprio
contrato: pesquisa uma faixa mais ampla perto do final, a partir das últimas
páginas, e identifica o título `Actualização de Caderneta Predial Rústica`
com `Modelo A` ou `Modelo B`, incluindo as páginas seguintes da caderneta.

O [Step 3.3](docs/step3.3.md) procura a caderneta em **todas** as páginas
do próprio contrato, sem pressupor a sua posição. Esta regra é partilhada com
a pipeline principal, que passa a priorizar a caderneta para proprietário,
identificação, artigo, secção e área. Para atualizar contratos já presentes na aba principal,
sem criar linhas duplicadas, use:

```bash
doc-register scan --config config.json --reprocess-cadernetas
```

O [Step 3.2](docs/step3.2.md) separa múltiplas cadernetas presentes no mesmo
contrato. Quando não houver uma associação inequívoca com o imóvel contratual,
nenhuma delas é misturada nem publicada automaticamente: o resultado fica para
revisão. Conflitos entre contrato e caderneta passam a ser visíveis no Excel,
e o Ollama recebe apenas um resumo cadastral determinístico, nunca as páginas
OCR brutas da caderneta.

Configurações opcionais:

- `property_extraction_dir`: diretório dos JSONs da pipeline secundária.
- `property_llm_enabled`: desativa a recuperação dirigida por Ollama quando `false`.
- `property_llm_timeout_seconds`: limite da chamada dirigida ao Ollama (180 por padrão).

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
- `lessor_2`
- `lessee`
- `lessee_tax_id`
- `property_name`
- `property_number`
- `property_display_name`
- `property_article`
- `property_section`
- `property_parish`
- `property_municipality`
- `property_district`
- `property_total_area`
- `leased_parcel_area`
- `property_location`
- `property_address`
- `owner_name`
- `owner_tax_id`
- `owner_address`
- `contract_start_date`
- `contract_end_date`
- `rent_payment_day`
- `monthly_rent`
- `annual_rent`
- `rent_amount`
- `rent_currency`
- `rent_frequency`
- `rent_unit`
- `rent_basis`
- `rent_area_basis`
- `rent_payment_timing`
- `rent_adjustment_rule`
- `rent_condition`
- `currency`
- `option_price`
- `purchase_price`
- `assignment_price`
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
- `ocr_quality_score`
- `ocr_quality_flags`
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

## Step 2.2 Robustez

O Step 2.2 reduz falsos positivos antes da IA e melhora a fila de revisão:

- nomes em `lessor`, `lessee`, `owner_name`, `bank_account_holder`, `payer` e `payee` são avaliados por qualidade. Valores genéricos ou OCR corrompido geram issues como `invalid_lessor_format`, `invalid_owner_name_format` e `ocr_corrupted_party_name`.
- IBAN/NIB são recuperados deterministicamente em janelas perto de `IBAN`/`NIB`, com correção OCR limitada (`O->0`, `I/l->1`, `S->5`, `B->8`, `Z->2`) e aceitação apenas quando o checksum é válido.
- `owner_name` e `bank_account_holder` podem ser recuperados de labels claros como `Sujeito Passivo`, `Proprietário`, `Titular` e `Titular da conta`.
- `owner_name` passou a ser elegível para o AI Reviewer quando estiver ausente, genérico ou inválido.
- `ocr_quality_score` e `ocr_quality_flags` permitem ver rapidamente quando a causa da revisão é degradação OCR, e `ocr_quality_low` bloqueia autoaprovação.
- regras de consistência detectam conflito entre `monthly_rent` e `purchase_price`/`option_price`/`assignment_price`, além de divergência entre artigo/secção extraídos e sinais determinísticos fortes.

## Step 2.3 Precisão em Contratos

O Step 2.3 foca os campos que mais impactam a leitura dos contratos de arrendamento: `signed_date`, `lessor`, `owner_name`, `property_article` e `property_section`.

- partes contratuais são recuperadas primeiro a partir de zonas como `Primeiro Outorgante`, `Segundo Outorgante`, `Senhorio`, `Arrendatário`, `De um lado` e `Do outro lado`.
- candidatos vindos do nome do arquivo continuam úteis, mas quando uma zona de senhorio existe eles precisam ser confirmados nessa zona, não em qualquer ponto do documento.
- `signed_date` segue hierarquia explícita: data de assinatura primeiro, data de celebração/outorga depois, e ignora datas de validade, registo, emissão, licença, matriz ou caderneta.
- `property_article` e `property_section` são recuperados em bloco: artigo/secção só entram quando aparecem perto de contexto predial como `matriz`, `prédio`, `inscrito`, `predial` ou `caderneta`.
- a revisão por IA recebe janelas focadas de 300 caracteres antes/depois dos termos relevantes, em vez de blocos longos de contrato inteiro.
- a correção fuzzy OCR é conservadora e auditável em `raw_json["fuzzy_recovery"]`, corrigindo localidades conhecidas e nomes muito próximos quando há evidência local suficiente.

## Step 2.4 Segmentação de Cláusulas

O Step 2.4 adiciona uma camada local e auditável para contratos. O arquivo principal é `src/doc_register/validators/contract_clause_segmentation.py`: a função `segment_contract_clauses(document_text)` devolve uma lista de cláusulas tipificadas e pode ser substituída por um modelo local mantendo a mesma interface.

A integração fica em `src/doc_register/validators/contract_clause_integration.py`. Ela usa as cláusulas para recuperar, de forma conservadora, `lessor`, `lessee`, `property_article`, `property_section`, `signed_date`, `contract_start_date`, `contract_end_date`, `monthly_rent` e `rent_payment_day` apenas quando os campos estão ausentes, genéricos ou inválidos. O resultado completo é guardado em `raw_json["contract_clause_segmentation"]`, incluindo `clause_type`, página, confiança, padrão usado, excerto e lista de campos recuperados.

Os tipos de cláusula atuais são: `title`, `parties`, `property`, `term`, `rent`, `payment`, `deposit`, `expenses`, `obligations`, `communications`, `signatures`, `annexes` e `other`.

## Step 2.5 Extração Complementar ao OCR

O Step 2.5 melhora a entrada textual antes da classificação e extração. O arquivo principal é `src/doc_register/pdf_text.py`. A função `extract_text_with_optional_ocr(...)` agora compara candidatos locais: texto nativo simples via `pypdf`, texto nativo com quebras de linha preservadas, texto layout-aware via PyMuPDF e texto OCR reutilizado/gerado quando disponível.

Cada candidato recebe um score de qualidade com base em volume útil, linhas, páginas, sinais documentais e ruído. O OCR deixa de ganhar apenas por ter mais caracteres; ele precisa melhorar a qualidade do texto ou resolver ausência de texto nativo. Quando o texto layout-aware já tem qualidade suficiente, o OCR é evitado.

As fontes possíveis em `text_source` incluem `native_pdf_text`, `native_pdf_pypdf_layout_text`, `native_pdf_layout_text`, `cached_ocr_pdf_text`, `cached_ocr_pdf_pypdf_layout_text` e `cached_ocr_pdf_layout_text`.

## Step 2.8 Final Contract Resolution

O Step 2.8 evita que a última recuperação executada determine o resultado. Para contratos de arrendamento, executa uma cadeia auditável: zonas documentais → qualidade por página → candidatos de partes/imóvel/renda/data → normalização e validação semântica → resolução de entidades e papéis → autoridade da fonte → conflitos → publicação final → recálculo da revisão.

O relatório está em `raw_json["step2_7_final_resolution"]` por compatibilidade com o nome exigido no desenho anterior. Inclui zonas, qualidade, entidades, candidatos, decisões, conflitos e campos não resolvidos. `lessor` contém a lista canónica completa; `lessor_2` é apenas compatibilidade legada e nunca recebe uma entidade marcada como `lessee`.

Hierarquia usada: para imóvel, caderneta predial > CRP > considerando > cláusula do objeto > filename; para partes, designação explícita > reconhecimento > assinatura > notificações > considerandos > filename. A pontuação combina autoridade específica do campo, confiança semântica e recuperabilidade da página; um candidato semanticamente inválido é bloqueado, mesmo que venha de uma página legível.

Para executar apenas a regressão do Step 2.8:

```bash
PYTHONPATH=src python3 -m unittest tests.test_step2_8 -v
```

Para executar toda a suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Step 3.3.3 Finalização e tolerância a falhas de revisão

O Step 3.3.3 mantém as fontes especializadas como produtoras de evidência e
deixa a publicação no registo para o resolvedor final. O relatório em
`raw_json["step2_7_final_resolution"]` inclui `ownership_evidence`, IDs de
candidatos alternativos rejeitados e a decisão explícita de que uma renda
anual por hectare não tem `monthly_rent` aplicável. A IA faz revisões menores
e independentes; as falhas ficam auditadas sem reverter os resultados dos
outros grupos. Veja [docs/step3.3.3.md](docs/step3.3.3.md).

## Step 3.4 Property Table

O modo opcional abaixo executa o pipeline principal, mas escreve o resultado
na sheet `Property Table` com uma linha por propriedade:

```bash
PYTHONPATH=src python3 -m doc_register property-scan
PYTHONPATH=src python3 -m doc_register scan --property-table
```

`property-scan` é a etapa de elegibilidade: `--property-table` materializa a
Property Table a partir de `Property Extraction` e `Document Register`, sem
voltar a abrir PDFs nem chamar o Ollama.

Para reprocessar todos os contratos e substituir atomicamente as linhas já
existentes pelo mesmo SHA-256:

```bash
PYTHONPATH=src python3 -m doc_register scan --property-table --reprocess-cadernetas
```

O modo padrão `scan` continua a escrever uma linha por contrato em
`Document Register`. Consulte [docs/step3.4.md](docs/step3.4.md) para o schema,
as regras de correspondência matricial e os estados de revisão.

## Step 2.6 Entity Resolution

O Step 2.6 aproxima o resultado final de uma verdade documental mais conservadora. O arquivo principal é `src/doc_register/validators/entity_resolution.py`, aplicado durante a validação normal e novamente no passe final depois do AI Reviewer.

A camada extrai códigos internos do nome do arquivo, como `PR098`, `VA140` e sequências `VA088_089_397_399_401`. Esses códigos alimentam `property_number` como número operacional interno, sem misturar esse conceito com `property_article`, `property_section` ou número de descrição predial.

Também são extraídos candidatos de proprietário/senhorio a partir do filename. Em contratos com marcadores como `Senhorio 1`, `Senhorio 2` ou `Senhorios`, quando o OCR confirma o papel mas não permite ler os nomes, o sistema pode preencher `lessor` com os candidatos do filename usando confiança média e auditoria. Essa regra não copia automaticamente o mesmo valor para `owner_name`, porque titularidade jurídica deve vir de caderneta, CRP ou outra evidência predial.

O Step 2.6 bloqueia valores finais perigosos:

- rótulos genéricos em partes, como `Senhorios`, `Arrendatária`, `Outorgante` e `As partes`;
- nomes genéricos de terreno, como `Prédio`, `Prédio rústico`, `Terreno`, `Parcela`, `Locado` e `Prédio Solar`;
- `property_number` com NIF/NIPC, placeholders, artigos legais, anexos, percentagens, letras isoladas ou texto como `não especificado`;
- `owner_name` igual à arrendatária, salvo quando outra evidência forte demonstrar titularidade;
- campos prioritários e monetários sem evidência no documento.

Para `property_name`, a camada procura denominações e topónimos em padrões como `denominado`, `denominado por`, `designado`, `conhecido por` e `sito no lugar de`, inclusive quando há quebras de linha causadas por OCR. Casos OCR conhecidos, como `A Gyax ais`, são normalizados para `Aguaxais` quando há evidência local suficiente. Em documentos prediais, `property_location` pode preencher `property_name` quando a localização é o topónimo empresarial usado para identificar o terreno.

O Step 2.6.2 adiciona uma barreira de evidência para reduzir alucinações do LLM. Campos como `owner_name`, `owner_tax_id`, `property_name`, `property_article`, `property_section`, localidades, moradas, `monthly_rent`, `currency`, `option_price`, `purchase_price` e `assignment_price` são apagados quando não aparecem no texto de forma literal, compactada ou fuzzy conservadora. O objetivo é preferir campo vazio e revisão humana a um valor plausível mas fabricado.

O detector predial também evita confundir `Ano de inscrição na matriz: 1945` com artigo matricial. Quando existe `Artigo matricial No: 140`, o artigo correto passa a ser `140`, reduzindo conflitos falsos entre caderneta e sinais determinísticos.

O relatório completo fica em `raw_json["step2_6_entity_resolution"]`, com `property_codes`, `property_numbers`, candidatos do filename, mudanças aplicadas e valores bloqueados. As notas de extração incluem `Step 2.6 entity resolution` quando a camada altera algum campo.

## Tipologias Contratuais

Os instrumentos contratuais usam `document_category=lease_contract` por compatibilidade com o schema existente, mas `document_subtype` e `contract_type` são normalizados para códigos canónicos:

- `contrato_de_arrendamento`: contrato de arrendamento; `monthly_rent` é aplicável quando houver renda mensal explícita.
- `contrato_promessa_compra_venda`: CPCV ou contrato-promessa de compra e venda; `purchase_price` guarda o preço prometido quando explícito.
- `opcao_de_compra`: opção de compra; `option_price` guarda o preço da opção quando explícito.
- `acordo_cedencia_posicao_contratual`: acordo de cedência/cessão de posição contratual; `assignment_price` guarda a contrapartida da cedência quando explícita.
- `ratificacao`, `aditamento`, `renovacao`, `rescisao`: instrumentos acessórios; `monthly_rent` só deve ser preenchido quando o texto repetir explicitamente uma renda mensal.

## Categorias oficiais

O Ollama trabalha em duas avaliações separadas:

1. Classificação inicial com começo do documento: define `document_category`, `document_type` e `document_subtype`.
2. Extração por árvore de prompts: escolhe um prompt específico a partir da categoria ampla e só então extrai os campos do schema correto.

As categorias oficiais são:

- `lease_contract`: instrumento contratual imobiliário, incluindo arrendamento, aditamento, renovação, rescisão/cessação, opção de compra, CPCV e acordo de cedência de posição contratual.
- `property_document`: caderneta predial, certidão predial, CRP, registo predial, matriz ou averbamento.
- `bank_details`: IBAN, NIB, BIC/SWIFT, titular de conta e dados bancários sem prova clara de pagamento.
- `payment_proof`: comprovativo de transferência/pagamento com data, pagador, beneficiário e valor.
- `invoice_or_receipt`: fatura ou recibo que não seja claramente comprovativo bancário.
- `identity_document`: documentos de identificação pessoal ou empresarial.
- `tax_document`: documentos fiscais.
- `correspondence`: cartas, notificações e comunicações.
- `other`: documentos que nao encaixam nas categorias anteriores.

## Notas importantes

### Step 3.6 — Evidence Resolution and Vision Recall

O resolvedor conserva todas as evidências em `raw_json["step3_6_evidence"]`
e publica o valor final a partir da respetiva autoridade documental; uma saída
do LLM não pode substituir evidência determinística. Conflitos permanecem
visíveis em `evidence_conflicts`. Consulte [docs/step3.6.md](docs/step3.6.md)
e use `python -m doc_register --vision-recall-last 10 --config config.json`
para revalidar, de forma seletiva, os últimos documentos registados.

- PDFs digitais normalmente nao precisam de OCR. PDFs escaneados como imagem passam pelo `ocrmypdf` quando `ocr_enabled` estiver ativo.
- O Power Automate pode continuar fazendo o filtro de PDFs adicionados na última hora. Localmente, este programa também evita duplicados por hash.
- Para instrumentos contratuais, o schema pede signatários, partes contratuais quando aplicável, datas, imóvel e valores específicos. `monthly_rent` só é obrigatório para contrato de arrendamento; CPCV usa `purchase_price`, opção de compra usa `option_price`, e cedência/cessão de posição contratual usa `assignment_price`.
- `property_display_name` é montado automaticamente com nome, artigo, secção e número quando o LLM não preencher a coluna conjunta.
- `needs_review` fica `yes` quando a confiança é baixa/média, quando há notas de extração, quando a categoria determinística diverge da categoria do LLM, quando faltam campos essenciais ou quando há campos suspeitos.
- Erros de processamento são registrados no Excel com `processing_status=error` quando o arquivo já foi copiado para a área local.

## Validações locais

```bash
python3 -m py_compile src/doc_register/pdf_text.py src/doc_register/processor.py src/doc_register/validators/validator.py src/doc_register/validators/name_quality.py src/doc_register/validators/iban_recovery.py src/doc_register/validators/field_recovery.py src/doc_register/validators/fuzzy_recovery.py src/doc_register/validators/contract_structure.py src/doc_register/validators/contract_clause_segmentation.py src/doc_register/validators/contract_clause_integration.py src/doc_register/validators/property_recovery.py src/doc_register/validators/signature_date.py src/doc_register/validators/ocr_quality.py src/doc_register/ai_reviewer/*.py
PYTHONPATH=src python3 -m unittest tests/test_step2_5.py tests/test_step2_4.py tests/test_step2_3.py tests/test_step2_2.py tests/test_ai_reviewer.py tests/test_sprint1b.py tests/test_text_selection.py tests/test_ollama_client.py tests/test_detectors.py
```

Smoke test opcional com Ollama local:

```bash
DOC_REGISTER_RUN_OLLAMA_SMOKE=1 OLLAMA_MODEL=qwen3:8b PYTHONPATH=src python3 -m unittest tests/test_ai_reviewer.py
```
