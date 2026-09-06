# Step 5.0 — pipeline local com evidência e avaliação

## Estado da entrega

Versão do pacote: **0.5.0**. O novo fluxo está em `src/doc_register/step50/` e possui CLI própria. O fluxo anterior permanece disponível para comparação e para os demais tipos documentais. **95% de recuperação automática ainda não foram demonstrados.** Esta entrega é uma implementação funcional conservadora, não a conclusão experimental de todas as propostas do documento de referência.

A autoaceitação atual limita-se a regras de rótulos explícitos sobre texto nativo sem alertas, com validação e resolução de conflitos. Candidatos de OCR, visão e modelo textual exigem revisão; não há probabilidades calibradas nem aprovação integral automática. Isso limita a cobertura automática e deve ser contabilizado como omissão no benchmark. Usar um limiar arbitrário para aprovar esses candidatos não demonstraria a meta.

## Instalação e execução

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-step5.lock.txt
.venv/bin/python -m pip install --no-deps .
./scripts/doc-register-step5 doctor
./scripts/doc-register-step5 scan /caminho/contratos --output /caminho/step5-runs --config examples/step5.config.json
```

O launcher `scripts/doc-register-step5` determina o diretório do checkout e coloca seu `src` à frente do pacote instalado. É a opção recomendada neste repositório. Também existem `doc-register-step5`, `python -m doc_register.step50` e `doc-register step5` após instalação. O comando `doctor` informa versão, caminho importado, commit, hash do código e dependências. Cada resultado mantém essa procedência.

Tesseract com `por+eng` deve ser provisionado previamente e estar no PATH. O modo completo usa os modelos locais `qwen3:8b` e `qwen3-vl:4b-instruct` já provisionados no Ollama. Não há download automático nem fallback externo. `--rules-only` desativa os modelos e mantém o OCR; o resultado registra essa degradação. Os comandos históricos `doc-register scan/watch/property-scan/normalize` continuam com suas configurações anteriores.

No ambiente auditado, um `.pth` de instalação editável recebeu o atributo macOS `hidden`; Python 3.14 ignorou-o e a importação falhou. O launcher independe desse mecanismo. Uma instalação convencional também evita depender de `.pth` editável. Sempre execute `doctor` depois de instalar.

## Arquitetura e artefatos

1. SHA-256, cópia do original e confirmação do hash após cópia; o original nunca é salvo por PyMuPDF.
2. Inventário físico de todas as páginas, com estados explícitos para páginas fora do orçamento, falhas, proteção e possível ausência de conteúdo.
3. Blocos nativos com bbox; propostas de recortes em imagens e desenhos, mesmo quando há texto digital; OCR por crop. Falha de OCR não impede a tentativa visual.
4. Leitura visual literal, sem candidato OCR no prompt. Preservação de conteúdo incerto, manuscritos e rasuras para revisão.
5. Segmentos com limite de contexto e sobreposição. Todos os segmentos são percorridos até o orçamento; não há corte silencioso em 12 páginas.
6. Regras e Ollama produzem candidatos; normalização pura; um resolvedor final agrupa por documento lógico, entidade e campo. Repetir um candidato não cria votos independentes.
7. JSON canônico, relatório HTML local, decisões humanas imutáveis e exportação compatível em workbook novo.

Layout em disco:

```text
<output>/<sha256>/original.pdf
<output>/<sha256>/<fingerprint>/<run_id>/pages/<page>.json
<output>/<sha256>/<fingerprint>/<run_id>/crops/<region>.png
<output>/<sha256>/<fingerprint>/<run_id>/extraction/<segment>.json
<output>/<sha256>/<fingerprint>/<run_id>/result.json
<output>/<sha256>/<fingerprint>/<run_id>/review.html
<output>/<sha256>/<fingerprint>/<run_id>/reviews/<event>.json
```

Retomada usa checkpoints de página e de extração. Repetir uma execução concluída reutiliza seu resultado quando a configuração e as revisões permitem cache. `--force` cria uma nova execução e preserva resultados e revisões anteriores. Um arquivo original com hash divergente bloqueia o processamento. Escritas JSON e a publicação final do Excel são atômicas; locks locais serializam o processamento por documento. Não há sincronização distribuída.

`model_revision="unverified"` desativa a reutilização de resultados dependentes de modelos entre invocações. Para habilitar, registre as revisões/digests reais dos modelos provisionados e altere essa chave quando houver troca. Essa declaração é operacional, não uma verificação criptográfica automática do servidor. O fingerprint inclui código, schema via código, prompt, configuração e versões Python de reconhecimento. Tesseract/tessdata não têm fingerprint automático nesta versão: ao atualizá-los, use `--force` e registre a alteração.

## Schemas e políticas

O schema `5.0.0` contém Document, Page, Region, Segment, Candidate, Entity, Relationship, FieldResult e ProcessingResult. Os IDs de entidades são chaves textuais estritas locais ao documento lógico; não representam resolução global comprovada de identidade. Cabeçalhos de contrato, caderneta, certidão e aditamento delimitam documentos lógicos de forma heurística. Continuidades complexas exigem revisão.

Bbox: pontos de página não rodada, origem superior esquerda, granularidade bloco ou crop; não é inventada uma caixa por token. Cada crop registra hash, escala, dimensões, DPI efetivo e rotação original. Offsets referem-se ao texto bruto da região. Fonte documental, método de reconhecimento e extrator são dimensões separadas. `probability` permanece `null`.

Dinheiro e áreas usam strings Decimal e dimensões explícitas. `rent` inclui amount, currency, frequency, basis e condition. Renda anual por hectare não vira renda mensal. NIF, IBAN português, datas e secção matricial de uma a três letras têm validação estrita. Checksum não comprova associação à pessoa. NIF associado a duas identidades bloqueia a aceitação de ambos.

Ollama recebe conteúdo documental como dado não confiável. Quote precisa pertencer à região fornecida; valor e entidade sem suporte recebem alertas. Propostas sem ancoragem não são aceitas. Uma transcrição visual não autentica assinatura nem elimina a necessidade de conferir a imagem.

## Revisão local

Abra `review.html` no navegador. O relatório mostra campos, estados, candidatos, páginas, crops e falhas. Conteúdo documental é escapado e o relatório não executa scripts.

```sh
./scripts/doc-register-step5 review /caminho/result.json /caminho/decisions.json --reviewer "Nome do revisor"
```

Exemplo de arquivo de decisões (substitua IDs pelos presentes no resultado):

```json
{
  "sha256": "HASH_COMPLETO_DO_DOCUMENTO",
  "run_id": "RUN_ID",
  "decisions": [
    {
      "logical_document": "contract-p1",
      "entity": "contract",
      "field": "signed_date",
      "candidate_id": "ID_DO_CANDIDATO",
      "reason": "Data conferida na página original"
    }
  ]
}
```

Para corrigir um valor, substitua candidate_id por `value`, `page` e `evidence`; a normalização continuará sendo exigida. A evidência de correção é uma declaração humana auditada, não OCR validado automaticamente. Para decisões cumulativas, passe como entrada o último JSON revisado. O original automático permanece intacto. Reprocessamentos não reaplicam silenciosamente correções antigas; os eventos anteriores permanecem consultáveis.

## Exportação e migração

```sh
./scripts/doc-register-step5 export /caminho/result.json --output /caminho/step5-comparacao.xlsx --output-format both --reference-mg MG-100
```

`legacy`, `normalized` e `both` preservam os formatos existentes. O workbook inclui `audit.Step50` com todos os estados, vínculos a candidatos e caminho do JSON; apenas campos aceitos alimentam a projeção operacional. As folhas Step 4.0 e suas regras de referência MG continuam pelo adaptador existente. O exportador recusa destino existente: produza um workbook de comparação novo, evitando que fatos antigos pareçam reconfirmados.

O formato legado não expressa todos os papéis, períodos e entidades do canônico. A projeção não mistura aditamentos com datas escalares do contrato original, nem fabrica relações proprietário–parcela. Papéis exatos e campos não representáveis permanecem no audit e no JSON; dados bancários sem associação humana validada não alimentam a projeção. A migração operacional e a consolidação temporal completa ainda dependem de validação do corpus.

## Avaliação independente

```sh
./scripts/doc-register-step5 annotate /caminho/result.json --output /caminho/gold.json
./scripts/doc-register-step5 evaluate /caminho/gold.json /caminho/predictions.json --split test --output /caminho/metrics.json
```

A anotação inicial deixa respostas vazias: não copia previsões como verdade. `predictions.json` mapeia ID documental para caminho do result.json (relativo ao arquivo de mapeamento ou absoluto). Para comparar baseline e Step 5.0, forneça duas projeções canônicas com o mesmo schema, hash e identidades de anotação e execute o avaliador duas vezes. A conversão automática dos resultados históricos para gold não existe e não deve ser usada.

Cada documento anotado informa `id`, `sha256`, `group_id`, `split`, `annotators`, `tags`, `fully_annotated_fields` e `facts`. Cada fato contém `logical_document`, `entity`, `field`, `value` normalizado, `legibility` (`legible`, `absent`, `illegible`) e `evidence_pages`. Anote todas as ocorrências dos campos declarados completos, incluindo entidades extras e ausências. Nomes usam apenas casefold e normalização de espaços; valores monetários devem estar em strings decimais canônicas, com todas as dimensões. Use exatamente o escopo documental e de entidade; similaridade não equivale a identidade.

O avaliador exige igualdade de campo, entidade, documento e valor, além de evidência na página anotada. Omissões e resultados ausentes reduzem recall. Duplicados e extras reduzem precisão. Fatos ilegíveis ficam fora do recall legível, mas possuem contadores próprios, inclusive publicações sobre fatos ilegíveis. Campos fora do escopo completamente anotado não são avaliados. Isso exige que o plano de anotação cubra realmente os campos de interesse.

Há micro métricas, macro recall por documento, recortes por campo/tags, falsos preenchimentos, revisão, completude crítica e intervalo bootstrap por grupo contratual quando há pelo menos dois grupos. Curva precisão–cobertura fica indisponível sem probabilidades calibradas. Resultado após revisão usa `--after-human-review` e permanece separado. O avaliador rejeita hashes divergentes e grupos/duplicados distribuídos entre splits. A independência humana, representatividade, quase duplicados de layout e adjudicação não são comprováveis por schema: precisam de governança do conjunto.

## Limites e próximo experimento

Não foram implementados/calibrados: comparação operacional Docling/MinerU, reconhecedor especializado de manuscritos, GLiNER/NuExtract, reconciliador seletivo Qwen por IDs, consolidação temporal geral, correção de rotação física/deskew em imagens sem metadados e telemetria de pico de toda a árvore de processos. Há orçamento de pixels, arquivo, páginas, regiões, chamadas e timeouts; o prazo total é verificado entre operações e limita timeouts externos, não constitui isolamento rígido de CPU/memória de parsers nativos.

Recortes baseados em imagens/desenhos são propostas de regiões, não detector validado de manuscritos. Modelos sem ganho medido não foram adicionados como dependências. O próximo experimento é rotular contratos reais estratificados, conferir os candidatos retidos, comparar OCR atual com Docling no mesmo conjunto e calibrar regras de aceitação por campo/modalidade. Os 95% precisam ser demonstrados nesse experimento, e não inferidos dos testes de software.
