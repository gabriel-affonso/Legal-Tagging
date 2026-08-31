# Step 3.5 — Recuperação Visual Local de Contratos

O Step 3.5 é um fallback visual local e limitado para contratos cuja leitura
por OCR não permite definir os pontos críticos com confiança. Usa
`qwen3-vl:4b-instruct` via Ollama local; não utiliza cloud, não persiste imagens e não
envia base64 para logs ou para o Excel.

## Ordem de execução

1. A pipeline mantém a extração nativa/OCR existente e executa o fluxo textual
   completo: regras, LLM, recuperação determinística, AI Reviewer e resolvedor
   contratual.
2. Depois da resolução provisória, Step 3.5 avalia a qualidade OCR por página
   e os campos críticos finais.
3. Se elegível, renderiza em memória somente as páginas 1–2 que têm OCR
   degradado e envia uma página por chamada ao modelo visual local.
4. As respostas visuais são propostas de evidência. O resolvedor contratual
   continua sendo o único publicador dos campos finais.
5. A validação final normal da pipeline é executada depois da recuperação.

## Gatilho

O modelo é chamado somente quando todos os critérios são verdadeiros:

- `vision_enabled=true`;
- o ficheiro é candidato a contrato por sinal determinístico, subtipo ou
  classificação textual;
- uma das duas primeiras páginas apresenta qualidade global abaixo de `0.60`,
  baixa recuperabilidade semântica/numérica ou
  `semantic_ocr_degradation`;
- pelo menos um campo crítico está vazio, inválido, em conflito ou com score
  inferior a `vision_critical_candidate_threshold`.

Os campos críticos incluem senhorio, arrendatário, NIF do arrendatário, data
assinada, artigo, secção e renda mensal quando aplicável.

## Política de segurança

- Texto manuscrito, carimbos, conteúdo misto e caracteres incertos nunca são
  autoaceites no Step 3.5.
- Uma proposta precisa de evidência curta e literal da mesma página, com o
  contexto do papel/valor.
- Formatos de nomes, NIF, data, artigo e secção são validados
  deterministicamente.
- Valores em conflito com fonte estruturada não substituem a fonte existente.
- O modo padrão é sombra: `vision_apply_proposals=false`.
- Mesmo com aplicação ativada, só texto impresso com confiança pelo menos
  `vision_auto_accept_confidence` pode entrar no grafo de candidatos.

## Memória

Antes da fase visual a pipeline pede ao Ollama para descarregar o modelo de
texto. Cada chamada visual usa `keep_alive: 0`; o modelo Vision é libertado ao
terminar. As páginas são limitadas por DPI e pixels e são processadas em série.

## Auditoria

`raw_json["vision_recovery"]` conserva versão, modelo, razão do gatilho,
páginas, hashes das imagens, dimensões, legibilidade, candidatos, decisões,
falhas e duração. As imagens e o base64 não são guardados.

As colunas `vision_*` permitem filtrar a operação no Excel sem expor conteúdo:
estado, páginas, modelo, razão, campos revistos/aceites, campos que exigem
humano e duração.

## Rollout

1. Execute em modo sombra numa amostra de contratos de OCR fraco.
2. Compare propostas com a revisão humana e meça campos críticos resolvidos,
   rejeições e conflitos.
3. Ative `vision_apply_proposals` e mantenha `vision_auto_accept_enabled=false`
   se desejar apenas propostas humanas.
4. Só ative autoaceitação de texto impresso após não haver regressões ou falsos
   positivos na amostra representativa. Manuscritos permanecem em revisão.
