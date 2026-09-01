# Step 3.7 — Focused Core Evidence Extraction

O Step 3.7 é um modo opt-in que preserva o pipeline e o resolvedor do Step 3.6,
mas limita a extração principal às páginas físicas 1, 2 e 3 do contrato e à
primeira página posterior validada como conteúdo útil de caderneta predial.
Páginas intermédias nunca são adicionadas por proximidade.

## Execução

```bash
doc-register scan --step 3.7 --config config.json
doc-register scan --focused-core-extraction --config config.json
doc-register --step 3.7 --last 10 --config config.json
doc-register --focused-core-extraction-last 10 --config config.json
```

O processamento normal permanece no Step 3.6. Para ativar o modo focado por
configuração, use `step_3_7.enabled=true`.

## Configuração

```json
{
  "step_3_7": {
    "enabled": false,
    "maximum_contract_pages": 3,
    "maximum_cadastral_pages": 1,
    "maximum_total_pages": 4,
    "maximum_chars_per_page": 12000,
    "maximum_total_context_chars": 40000,
    "cadastral_page_threshold": 7,
    "cadastral_min_indicator_diversity": 2,
    "cadastral_ocr_quality_threshold": 0.60,
    "maximum_cadastral_visual_candidates": 3,
    "cadastral_indicator_scores": {}
  }
}
```

O detector remove acentos para comparação, tolera pequenas corrupções de OCR,
combina sinais ponderados e exige pelo menos dois grupos de conteúdo entre:
identificação do prédio, localização, titulares e cabeçalho institucional. Uma
capa com apenas “Caderneta Predial” é registada como separador e não é escolhida.

O scan textual usa primeiro o PDF nativo ou o PDF OCR em cache. Se não localizar
conteúdo cadastral válido, pode gerar OCR uma vez. Vision Recall só é planeado
quando ainda não há candidata acima do limiar, existe empate, ou a qualidade OCR
da melhor candidata é baixa, e fica limitado às melhores três páginas por
defeito. As regras de aplicação segura do Step 3.5 continuam válidas.

## Auditoria e arbitragem

O contexto é delimitado por papel semântico e página física. Truncamentos são
marcados com `TRUNCATED=true` e priorizam linhas com partes, papéis, artigo,
secção, localização e titularidade. Classificação e extração estruturada são as
únicas chamadas textuais ao Ollama; o AI Reviewer e o recovery AI não fazem
chamadas adicionais neste modo.

O resultado completo fica em `raw_json["step3_7"]`, contendo:

- páginas selecionadas e candidatas cadastrais pontuadas;
- texto bruto, normalizado, fonte e qualidade por página;
- candidatos determinísticos com offsets, evidência e confiança;
- decisão e confiança por campo;
- artigos mencionados no contrato e artigo confirmado pela caderneta;
- plano condicionado de confirmação visual.

Papéis contratuais e titularidade cadastral são independentes. O artigo e a
secção continuam em campos separados. Evidência cadastral direta prevalece para
dados matriciais; evidência contratual direta prevalece para senhorio e
arrendatário; o nome do ficheiro nunca é evidência suficiente.
