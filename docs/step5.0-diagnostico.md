# Diagnóstico para a proposta Step 5.0

Auditoria em 6 de setembro de 2026. Referência do checkout: `4a0af3d` — `feat: add step 4.0 normalized workbook pipeline`.

Este trabalho interpreta o documento enviado como material para reescrita de um prompt, não como ordem para executar a reconstrução. Nenhum código da pipeline foi alterado.

## O que existe

O fluxo principal em `src/doc_register/processor.py` combina ingestão e SHA-256, seleção de texto nativo/layout/OCR, busca de cadernetas internas, classificação e extração via Ollama, recuperações determinísticas, revisão por IA, resolução final, recuperação visual opcional, validação e publicação em Excel.

O projeto tem CLI em `__main__.py`; não identifiquei uma interface gráfica implementada no fluxo auditado. O Step 4.0 é sobretudo uma camada de publicação normalizada: `Entrada_Rapida`, folhas `db.*` e folhas `audit.*`. Ele não constitui uma nova arquitetura de reconhecimento documental. O Step 3.7 é uma opção de extração focada sobre a base anterior.

| Componente | Evidência no repositório | Destino proposto |
|---|---|---|
| Ingestão, hash e processamento em lote | `processor.py` | Adaptar; separar processamento, persistência e efeitos sobre arquivos |
| Texto nativo, layout, OCR e orientação | `pdf_text.py`, `page_orientation.py` | Reutilizar como baseline; substituir seleção global por página/região |
| Evidências, candidatos, fontes e conflitos | `evidence.py`, `validators/contract_final_resolution.py` | Preservar conceitos e testes; consolidar modelo canônico |
| Papéis e extração especializada | `party_extractor.py`, `validators/party_centric.py`, `step33_engine.py`, `property_*` | Reaproveitar regras comprovadas como produtores de candidatos |
| NIF, IBAN, datas, áreas e valores | `validators/*`, `step40.py` | Consolidar funções puras; preservar origem e ambiguidades |
| Ollama e revisão | `ollama_client.py`, `ai_reviewer/*` | Adaptadores opcionais; retirar dependência de publicação direta |
| Visão | `vision_recovery/*` | Evoluir para regiões, manuscritos e evidência visual verificável |
| Excel normalizado | `step40.py`, `registry.py`, `schemas.py` | Preservar contrato externo e testes durante a migração |
| Cópias antigas | arquivos com sufixos `antigo`, `quebrado`, `rebelde`, `build/lib` | Inventariar imports; excluir do pacote ativo após verificar referências |

## Limitações verificadas e riscos inferidos

1. **Reconhecimento global pode esconder lacunas locais.** `pdf_text.py:163` retorna cedo quando o candidato nativo satisfaz volume e qualidade globais. Uma página digital legível pode coexistir com campos manuscritos ou outras páginas sem texto. É um risco inferido do controle de fluxo, não uma taxa de erro medida. A busca adicional de cadernetas mitiga parte do problema, mas não cobre todos os campos.
2. **Limites podem excluir evidência.** `config.py` usa por padrão 12 páginas e 24.000 caracteres na extração inicial. O modo 3.7 limita o contexto principal a páginas físicas 1–3 e uma caderneta. Isso reduz custo, mas não garante cobertura de assinaturas, cláusulas finais, múltiplos imóveis ou aditamentos. `docs/step3.7.md` documenta esse comportamento.
3. **Manuscritos não entram na autoaceitação visual atual.** Em `vision_recovery/integration.py`, candidatos com `content_type != "printed"` seguem para revisão humana. A visão está desativada por padrão, com aplicação e autoaceitação também desativadas em `config.py`. Isso é uma política implementada, não prova de incapacidade do modelo.
4. **A visão opera em páginas inteiras.** `vision_recovery/pdf_renderer.py` não recebe bbox de recorte. A configuração padrão usa 160 DPI e teto de 3 milhões de pixels. Reduzir uma página inteira pode perder detalhes de pequenas anotações; esse efeito precisa ser medido.
5. **Há sobreposição de etapas.** `processor.py` chama recuperações, validações e resolvedores em vários passes; `apply_step37_resolution` ainda pode atuar depois da validação final. Já existem salvaguardas contra sobrescrita pela última etapa, mas a cadeia continua difícil de raciocinar. O resolvedor contratual tem 1.028 linhas; o processador, 730.
6. **Autoridade documental e método aparecem misturados.** `evidence.py` pontua `CONTRACT_EXPLICIT`, `CADERNETA`, `OCR` e `LLM_EXTRACTION` numa mesma tabela. Uma leitura OCR de uma caderneta tem duas dimensões diferentes: origem documental e confiabilidade da leitura. A nova representação deve separar essas dimensões, com precedência por campo e período.
7. **Há correções ligadas a casos específicos.** `validators/fuzzy_recovery.py` contém localidades conhecidas; `validators/entity_resolution.py` contém nomes prediais conhecidos e recuperação pelo filename. São úteis como hipóteses, mas não demonstram generalização. A política mais restritiva documentada no 3.7 deve orientar a migração.
8. **Algumas regras diferem por caminho.** A validação visual aceita uma única letra para secção matricial; `output_safety.py` admite de uma a três. Isso justifica um schema e validadores compartilhados, sem presumir aqui qual domínio é correto para todos os documentos.

## O que o histórico mostra que precisou ser corrigido

Os documentos e testes de regressão registram problemas com papéis usados como nomes; senhorio confundido com titular cadastral; NIF e códigos internos confundidos com identificadores prediais; datas notariais confundidas com assinatura; renda anual por hectare tratada como mensal; mistura de cadernetas; e perda de resultados quando um grupo de revisão falhava.

As garantias dos Steps 2.8 e 3.3.3 mostram correções para esses casos. Não afirmo que todos continuam a falhar hoje. Elas devem virar casos obrigatórios de regressão na reconstrução. Referências: `docs/step3.3.3.md`, `tests/test_step2_8.py`, `tests/test_step3_3_3.py`.

## Verificação executada

Comando sobre o código atual:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

Resultado: **140 testes descobertos, 139 aprovados e 1 ignorado**. Isso não é 99% de acurácia documental: são verificações de software, várias com texto sintético e chamadas simuladas.

Há funções de teste em estilo pytest, incluindo `tests/test_step40.py`, que o unittest não executa. O pytest não está instalado na `.venv`; portanto, não apresento esse comando como execução completa de todos os testes do repositório.

Uma primeira execução sem `PYTHONPATH=src` carregou `doc_register` de `.venv/lib/python3.14/site-packages`, não do checkout. Descobriu 127 testes e terminou com 1 falha, 6 erros e 1 ignorado, incluindo módulos recentes ausentes. Isso comprova divergência entre a instalação local e `src`, não essas mesmas falhas no código atual. A nova CLI deve expor versão, commit e caminho do pacote efetivamente carregado.

Os quatro JSONs em `test_runs/public_cadernetas/property_extractions` registram `status=skipped`, `reason=not_lease_contract`, na versão 3.3.3. Isso é compatível com a elegibilidade restrita daquele fluxo; não deve ser contado como erro de OCR. Também não constitui benchmark de contratos manuscritos.

Não identifiquei no material auditado um conjunto rotulado representativo de contratos reais com manuscritos e métricas de ponta a ponta. Não executei modelos sobre contratos reais nem medi RAM ou latência de novos parsers. A acurácia atual e a viabilidade de atingir 95% permanecem por medir.

## Opinião arquitetural

A reconstrução deve concentrar esforço em **cobertura da evidência, leitura de regiões difíceis, relações corretas e avaliação**, antes de adicionar vários modelos. O prompt original acerta na extração por candidatos, mas coloca benchmark e revisão tarde demais, prescreve componentes antes da comparação e sugere somar confirmações que podem vir do mesmo erro de OCR.

Minha proposta inicial é texto nativo por região + um parser/OCR principal, um resolvedor determinístico e visão seletiva desde o primeiro corte funcional. Docling é o primeiro candidato a avaliar; MinerU, uma alternativa de comparação. GLiNER, NuExtract e o reconciliador Qwen entram apenas com ganho medido. Essa preferência é uma hipótese de engenharia, não resultado de benchmark neste acervo.

NuExtract-2.0-2B é multimodal e multilíngue, baseado em Qwen2-VL-2B; não é simplesmente um pequeno extrator textual. A compatibilidade de sua execução e quantização deve ser verificada. Fonte: [model card NuMind](https://huggingface.co/numind/NuExtract-2.0-2B).

Docling documenta opções CPU e MPS e OCR configurável, o que justifica avaliá-lo sem presumir CUDA. Fonte: [opções de pipeline](https://docling-project.github.io/docling/reference/pipeline_options/). MinerU deve ser avaliado por backend e versão, pois as exigências não são intercambiáveis: [repositório oficial](https://github.com/opendatalab/MinerU).

Qwen3-VL-4B-Instruct é candidato visual plausível, mas sua ficha não comprova 95% nos nossos manuscritos. Fonte: [model card Qwen](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct). Para NER, avaliar um checkpoint multilíngue explícito, por exemplo [GLiNER multi-v2.1](https://huggingface.co/urchade/gliner_multi-v2.1), sem supor que detectar nomes resolve papéis e relações.
