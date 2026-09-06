# Step 5.0 — relatório de verificação em 6 de setembro de 2026

## Resultado de qualidade

**Acurácia/recuperação em contratos: não aferida. Meta de 95%: não demonstrada.** Não há corpus rotulado de contratos independentes neste checkout. Não há denominador contratual para calcular distância à meta, desempenho de manuscritos ou custo de revisão humana. Os quatro PDFs disponíveis são cadernetas/documentos públicos sem respostas de referência.

## Baseline e regressão

Verificação final: **167 testes aprovados, 1 ignorado**, com `PYTHONPATH=src .venv/bin/python -m pytest -q`. São testes de software, não 167 contratos avaliados. O launcher confirmou importação de `src/doc_register`, versão 0.5.0.

Antes das alterações: unittest descobriu 140 testes, com 139 aprovados e 1 ignorado. Depois de instalar pytest, a baseline revelou **146 aprovados, 1 falha e 1 ignorado**. A falha era uma expectativa antiga do Sprint 1B: aprovação automática e qualidade 100 sem caderneta. A política mais recente retém artigo/secção nesse caso; o teste foi atualizado para verificar a revisão e os motivos explícitos, sem relaxar o código de produção.

O novo conjunto testa associação de NIF, ausência de votos duplicados, papéis, titular cadastral, datas notariais, renda anual/hectare, secção ABC, página 13, limitação de cobertura, página híbrida, timeout parcial, OCR/visão com falhas independentes, revisão imutável, exportação, duplicados/omissões/evidência na avaliação, separação humano/automático e vazamento entre splits.

## Execução real de reconhecimento, sem modelos

Comando: `PYTHONPATH=src .venv/bin/python -m doc_register.step50 scan test_runs/public_cadernetas/input --output /private/tmp/step50-public-smoke --rules-only`.

| Arquivo | Páginas inventariadas | Tempo observado | Estado de cobertura |
|---|---:|---:|---|
| rustica_ovar.pdf | 2 | 0,35 s | incompleta/revisão |
| rustica_ponte_lima_municipio.pdf | 59 | 65,60 s | incompleta/revisão |
| urbana_penafiel.pdf | 2 | 0,34 s | incompleta/revisão |
| rustica_porto_mos.pdf | 1 | 0,22 s | incompleta/revisão |

São **64 páginas** inventariadas. OCR local foi exercitado onde necessário. Visão desativada e regiões não verificadas permanecem visíveis. Esses tempos são execuções únicas, não p50/p95 nem benchmark de contratos. Saídas locais não foram incluídas no GitHub.

## Integração real com Ollama

`scripts/smoke_step50_models.py` usa texto e imagem sintéticos. Ambas as requisições retornaram JSON estrutural válido:

- Qwen3 8B: 28,82 s. Retornou o nome e a data; usou o papel “Senhorio” como chave de entidade para o nome, ilustrando um erro relacional que não deve ser publicado automaticamente.
- Qwen3-VL 4B: 13,52 s. Transcreveu a data impressa sintética, classificou como printed e não indicou tokens incertos.

Isto não testa manuscritos reais nem atesta precisão semântica geral. Ambos os adaptadores descarregam via `keep_alive: 0`; não foi medido pico de memória do servidor.

Modelos provisionados observados via API local:

| Modelo | Quantização | Digest |
|---|---|---|
| qwen3:8b | Q4_K_M | 500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41 |
| qwen3-vl:4b-instruct | Q4_K_M | ee4b975b58c17ce268cd19d40db35d5edc64603035d2ffc1fee1968eb0947f7b |

Ambiente: macOS 26.3.1 arm64, Python 3.14.4, PyMuPDF 1.28.2, pypdf 6.16.2, openpyxl 3.1.5, pytest 9.1.1. Tesseract e idiomas por/eng estavam disponíveis. Docling não estava instalado e não foi comparado. Capacidade de produção em 16 GB não foi certificada.

## Critério para a próxima avaliação

Começar com aproximadamente 50 contratos estratificados, incluindo manuscritos e contratos longos; separar grupos em desenvolvimento/calibração/teste; dupla anotação e adjudicação para fatos críticos. Medir recall correto automático, precisão crítica e cobertura antes de habilitar aceitação automática para OCR/visão/LLM. Ampliar o conjunto conforme a variância e diversidade observadas. Modelos opcionais devem entrar somente se melhorarem essas métricas no mesmo corpus.
