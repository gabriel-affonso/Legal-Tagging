# Step 5.5 — extração local por documento, entidade e tarefa

Implementação do piloto definido em [step5.1-prompt-implementacao-semantica.md](step5.1-prompt-implementacao-semantica.md). Schema atual: `5.5.1`. O Step 5.0 permanece disponível; seus candidatos e aprovações não são promovidos automaticamente.

## Executar

No ambiente já provisionado:

```sh
./scripts/doc-register-step55 doctor
./scripts/doc-register-step55 scan /caminho/documento.pdf --output /caminho/step55-runs
```

O padrão usa texto nativo, OCR Tesseract quando necessário e regras. Não faz chamadas ao modelo nem downloads. O launcher usa o código deste checkout e a `.venv` existente. As dependências continuam em `requirements-step5.lock.txt`; o Tesseract e os idiomas são dependências externas locais.

Para habilitar um modelo **já instalado e conferido**:

```sh
./scripts/doc-register-step55 scan /caminho/documento.pdf \
  --output /caminho/step55-runs --text-enabled --model TAG_LOCAL_CONFIRMADA
```

`qwen3:8b` é o nome padrão de referência, não um requisito. A variante/quantização adequada precisa ser medida na máquina alvo. `--config arquivo.json` aceita as opções de `PipelineConfig`:

```json
{
  "text_enabled": false,
  "max_model_calls": 6,
  "max_model_failures": 2,
  "max_llm_seconds": 480,
  "max_document_seconds": 600,
  "context_tokens": 4096,
  "ocr_max_pixels": 8000000,
  "memory_budget_bytes": 10737418240
}
```

Ao habilitar texto, defina `model_revision` com o digest completo do modelo instalado para usar cache semântico verificável. O adaptador consulta `/api/tags` e rejeita divergências. Com `unverified`, não reutiliza resultados semânticos finais de outra execução como cache validado.

## Componentes

| Camada | Implementação |
|---|---|
| Modelo | `canonical.py`, `values.py`: tipos fechados, JSON Schema por predicado, referências e compatibilidade sujeito/campo/documento. |
| Documentos | `document_map.py`: segmentos dentro da mesma região, contexto entre páginas, anexos, cabeçalhos cadastrais repetidos e distinção entre bloco curto e página curta. |
| Extração | `extraction.py`, `rules.py`: partes, blocos prediais delimitados, cadernetas, áreas, datas, condições, renda e pagamentos; catálogo com positivos e negativos. |
| Leitura | `recognition.py`: observações nativas/OCR separadas, palavras TSV, coordenadas, recortes, receita de renderização e inventário de todas as páginas. |
| Resolução | `resolution.py`: conflitos por entidade/escopo/conceito, identidades propostas, namespaces cadastral/registral, duplicidade de pagamentos e derivações rastreáveis. |
| Modelo local | `tasks.py`, `models.py`: trechos por tarefa, schema de resposta, IDs permitidos, offsets, verificação de valores literais, orçamento, retry limitado e isolamento de falhas. |
| Persistência | `storage.py`, `pipeline.py`: original com hash, execuções imutáveis, checkpoints, cache versionado, SQLite e migração por observações. |
| Revisão | `review.py`: HTML por objeto, quotes e links ao original, decisões versionadas, correção/reassociação e reconstrução das derivações. |
| Avaliação | `evaluation.py`: alinhamento gold/predição, fatos/escopo/evidência, relações, objetos completos, camadas de aceitação e prevenção de vazamento entre famílias. |
| Projeção | `field_map.py`, `export.py`, `dossier.py`: inventário de colunas, origem por afirmação, staging separado e propostas de vínculo entre arquivos. |

### Regras semânticas

- Bruto, retenção e líquido são distintos. A igualdade financeira é uma verificação; não prova execução bancária.
- Data documental, emissão, execução bancária e data-valor têm predicados distintos. Carta registra pagamento reportado.
- Artigo matricial e descrição registral mantêm namespaces separados. Artigo 530 não é corrigido para 306 porque existe uma descrição 530.
- Cada imóvel recebe os atributos do seu bloco. Área útil conjunta pertence a `lease_object`.
- Área contratual, cadastral, medida e operacional permanecem separadas. Conversões equivalentes não conflitam. Somas são diagnósticos derivados, não substitutos da área arrendada.
- Duração, prazo, definição de evento e ocorrência são tipos distintos. A aritmética de calendário exige âncora e convenção explícitas.
- Renda por hectare é taxa. Percentual só vira obrigação de reserva quando o texto identifica reserva. A base de área não é escolhida por proximidade.
- Papéis hipotéticos não são entidades; cônjuge e representante não recebem automaticamente o papel de arrendatário.
- RGPD não gera imóvel nem IBAN genérico. NIF/IBAN passam por validação sintática e checksum.

As afirmações extraídas permanecem em `needs_review`: não existe corpus independente que autorize autoaceitação. `resolved` significa associação local sem conflito conhecido, não identidade global confirmada nem publicação. Revisão humana registra `acceptance_basis`, autor, razão e evidência. `recognition_uncalibrated` permanece visível e não provoca LLM apenas para eliminar esse aviso.

## Persistência e cobertura

```text
output/<sha256>/original.pdf
output/<sha256>/recognition-<chave>/...
output/<sha256>/<fingerprint>/run-<uuid>/
  result.json
  schema.json
  rules-manifest.json
  prompt-system.txt
  field-map.json
  staging-projection.json
  review.html
  tasks/<task-id>.json
  model-checkpoints/<chave>.json
output/index.sqlite3
```

O fingerprint semântico inclui código, dependências, schema, regras, prompt e configuração. A leitura tem cache próprio para não repetir OCR após uma correção semântica. `--force` cria execução nova. Falhas técnicas não são sucesso em cache. SQLite é um índice reconstruível; os JSONs e originais são a auditoria.

Páginas com OCR vazio, erro ou orçamento esgotado continuam no inventário. Cobertura distingue página, documento, tarefa e campo. `not_found_by_rules` não significa ausência documental comprovada. Manuscritos/imagens não examinados não recebem cobertura completa.

### Limites do modelo

Somente HTTP loopback, sem proxy ou fallback remoto. Uma chamada por vez por diretório de saída, `think=false`, temperatura zero, 768 tokens de saída e até 60 segundos por tentativa. Limites: seis chamadas, duas falhas por documento, 480 segundos LLM e 600 segundos de documento. O segundo intento usa menos contexto. Trechos omitidos ficam registrados.

JSON inválido, truncamento, ID desconhecido, quote incorreto, valor literal incompatível ou referência inválida não alteram as afirmações existentes. Propostas de novas entidades ficam separadas para revisão.

Após timeout, a execução não presume cancelamento no servidor: abre `ollama-circuit.json` e bloqueia novas chamadas naquele diretório. Depois de verificar/reiniciar o servidor:

```sh
./scripts/doc-register-step55 reset-model-circuit --output /caminho/step55-runs
```

RSS mede o processo Python, sem incluir Ollama. Memória conjunta e swap ainda precisam de medição com modelo real.

## Revisão e staging

Abra `review.html`. Crie decisões com IDs efetivos do resultado:

```json
{
  "run_id": "run-ID_REAL",
  "source_sha256": "HASH_REAL_64_CARACTERES",
  "decisions": [{
    "target_id": "ID_REAL_DA_AFIRMACAO",
    "action": "accept",
    "reason": "Valor e sujeito conferidos no original.",
    "evidence_ids": ["ID_REAL_DA_EVIDENCIA"]
  }]
}
```

```sh
./scripts/doc-register-step55 review resultado/result.json decisoes.json --reviewer "Nome do revisor"
./scripts/doc-register-step55 export-staging resultado/reviews/ID/result.json --output staging-novo.xlsx
```

A revisão produz outro resultado. Reaplicar exatamente a mesma decisão ao mesmo resultado é idempotente. A próxima revisão deve partir do resultado revisado.

Ações: `accept`, `reject`, `correct` (`value`), `reassign` (`subject_id`), `split_entity` (`assertion_ids`), `confirm_entity`, `confirm_link`, `reject_link`, `add_entity` (`kind`, `label`, `logical_document_id`), `add_assertion` (`subject_id`, `predicate`, `value`, `scope` opcional), `add_relation` (`subject_id`, `predicate`, `object_id`, `role` quando aplicável). Todas exigem razão/evidência. Para adições, use o sujeito/documento como `target_id`; a decisão registrada aponta para o ID criado. Use esse novo ID em uma revisão seguinte. Identidades incompatíveis exigem correção/rejeição dos fatos conflitantes antes do vínculo.

Staging contém fontes, documentos, entidades, fatos aceites, relações com estados/papéis, derivações e auditoria completa. Valores complexos ficam em JSON com unidade/tipo. Identificadores mantêm texto. Conteúdo iniciado por `=` não vira fórmula. Não há escrita em `db.*` ou workbook operacional. Não se sobrescreve staging nem se selecionam duas versões do mesmo documento.

```sh
./scripts/doc-register-step55 field-map --output mapa-schema.json
./scripts/doc-register-step55 field-map --workbook existente.xlsx --output mapa-cabecalhos.json
./scripts/doc-register-step55 link-results contrato/result.json caderneta/result.json --output dossier.json
```

`link-results` cria propostas rastreáveis, sem merge nem soma de pagamentos. Publicação de vínculos entre arquivos permanece externa ao staging. O mapa mantém ambiguidades/colunas `unmapped`; não presume que `payment_amount` seja líquido ou que área conjunta preencha uma parcela. O workbook localizado é um resultado de testes públicos, não o Excel operacional privado do relatório.

## Migração e avaliação

```sh
./scripts/doc-register-step55 replay legado/result.json --source original.pdf --output replay-runs
./scripts/doc-register-step55 annotate resultado/result.json --output gold-a-preencher.json
./scripts/doc-register-step55 evaluate gold.json predictions.json --split test --output avaliacao.json
```

`replay` exige original com hash compatível e usa somente observações. Decisões antigas exigem reassociação explícita. `predictions.json`: `{ "id-do-documento-gold": "caminho/para/result.json" }`, com caminhos relativos ao arquivo.

Anote o gold no original, independentemente da saída. Informe `annotators`, `group_id`, split, identidades com identificadores/contexto, `fully_annotated_fields`, fatos e páginas de evidência. Para relações/objetos completos: `fully_annotated_relations`, `relations`, `complete_objects`. IDs gerados não são exigidos como IDs de gold; itens não processados não viram acertos. Casos conhecidos são desenvolvimento/regressão, não teste cego.

## Validação e limites

Consulte [step5.5-validation.md](step5.5-validation.md). Não estão incluídos: interpretação visual de plantas/manuscritos, CRP completa, cessões/CPCVs e publicação operacional. Essas tipologias são reconhecidas e permanecem fora da extração automática do piloto. Calibração e rodada de LLA/comprovativo privados dependem dos respectivos dados e do modelo local.
