# Step 5.5 — validação da correção

## Ambiente

MacOS 26.3.1 arm64, 10 CPUs lógicas, 16 GiB RAM, Python 3.14.4. Pydantic 2.13.5, PyMuPDF 1.28.2, openpyxl 3.1.5 e Tesseract local. Nenhuma dependência ou modelo novo foi instalado.

## Regressões

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q
```

Rodada de implementação: **209 testes passaram, 1 foi ignorado**. O ignorado é o smoke test opt-in de Ollama. Avisos: depreciação SWIG/PyMuPDF. Step 5.5 tem 42 casos, entre os sete originais corrigidos e novas regressões parametrizadas.

Verificam: objetos prediais completos, ordem invertida, continuidade entre páginas, RGPD, artigos legais, namespaces 530/306, áreas por conceito, papéis, NIF/IBAN, bruto/retido/líquido, reserva versus percentual, duração/prazo/data, dependências de cálculo, pagamentos duplicados, falha OCR, cache/force, replay, decisões idempotentes, correção de conflito, exportação literal e vínculo entre arquivos.

O transporte do modelo é simulado nos testes: sucesso ancorado, data inventada, JSON inválido, truncamento, timeout, indisponibilidade, excesso de contexto e preservação de fatos determinísticos. Isso não mede qualidade/velocidade de modelo real.

## PDFs públicos existentes no projeto

Leitura e replay usaram `test_runs/public_cadernetas/input`. Os PDFs privados de LLA/comprovativo não estavam no workspace.

| Arquivo | Páginas do PDF | Caderneta | Artigo | Área total | Afirmações no replay | Conflitos detectados |
|---|---:|---|---|---|---:|---:|
| `rustica_porto_mos.pdf` | 1 | página 1 | 344 | 0,1 ha | 13 | 0 |
| `rustica_ovar.pdf` | 2 | páginas 1–2 | 2322 | 1,64 ha | 21 | 0 |
| `urbana_penafiel.pdf` | 2 | páginas 1–2 | 290 | 492 m² | 8 | 0 |
| `rustica_ponte_lima_municipio.pdf` | 59 | página 25 | 478 | 1,568 ha | 9 | 0 |

CASTANHAL, Jugal e SABADÃO-ARCOZELO ficaram nos respectivos imóveis. A freguesia extensa de Ovar manteve a continuação de linha. Não foi inventado nome para o prédio urbano. Em Ovar, cinco titulares e quotas ficaram associados ao mesmo imóvel. Identidades continuam sujeitas a revisão.

O PDF municipal de 59 páginas é composto: recuperar uma caderneta não significa extrair todos os documentos. Seis páginas tinham alertas de leitura. Nas demais entradas, páginas híbridas mantiveram avisos de comparação nativo/OCR. Zero conflitos detectados não prova precisão total; não foi calculada acurácia sem gold humano independente.

### Tempos

Na rodada com leitura/OCR e regras corrigidas: Porto de Mós 0,91 s; Ovar 1,61 s; Penafiel 1,76 s; PDF municipal 49,74 s. **Zero chamadas LLM**. Essa rodada detectou problemas reais de segmentação e está em `test_runs/step55_corrected_public_v2`.

O replay subsequente das observações com o código corrigido, incluindo validação e gravação, mediu 0,044 s, 0,029 s, 0,029 s e 0,096 s, respectivamente. Saídas: `test_runs/step55_verified_replay`. Replay não inclui OCR e não equivale a processamento frio. São quatro casos de desenvolvimento, sem p50/p95 populacional.

O pico observado de RSS Python nas rodadas de leitura ficou abaixo de 600 MB. É cumulativo por processo e não inclui servidor Ollama/swap. Não há evidência para afirmar a meta de dez minutos no LLA privado ou recomendar um modelo pela velocidade real neste hardware.

## Excel

Inspeção sem alteração de `test_runs/public_cadernetas/results/document_register.xlsx`: `Document Register` tem 102 colunas e `Property Extraction`, 47. Cada folha tem cinco linhas incluindo cabeçalho. O mapa de código também cobre o registro normalizado e a tabela de propriedades. `field-map --workbook` inventaria cabeçalhos efetivos.

Não foi inventariada nem modificada a planilha operacional privada do relatório. Cada afirmação mantém sua origem; cabeçalho isolado não autoriza classificar uma correção humana como dado documental.

## Dependências externas à implementação

- Corpus independente e anotado para calibrar autoaceitação. Candidatos tipados são entregues para revisão, sem apagar alertas para inflar campos aceites.
- Medição de modelo real e memória conjunta Python/Ollama no PC alvo.
- PDFs privados de LLA/comprovativo e vínculo com o Excel operacional.
- Fora do piloto: visão de manuscritos/plantas, CRP completa, cessões/CPCVs e publicação operacional. Não são acionados como fallback implícito.
