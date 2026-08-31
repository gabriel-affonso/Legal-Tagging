# Step 3.3.3 — Resolução final auditável de contratos-container

O Step 3.3.3 fecha o ciclo de resolução introduzido nos Steps 2.7, 2.8 e
3.3. As recuperações determinísticas, fuzzy, LLM e IA são evidência, e não
publicadores finais. A ordem efetiva é: zonas documentais → qualidade por
página → candidatos estruturados → normalização/validação → entidades e
papéis → autoridade por campo → conflitos → resolução final → segurança de
output → recomputação da revisão.

## Garantias

- O `ContractResolutionReport` em `raw_json["step2_7_final_resolution"]`
  usa a versão `3.3.3` e preserva cada candidato com página, zona, método,
  pontuação, evidência de suporte e motivo de bloqueio.
- Cada decisão final identifica o candidato selecionado e todos os
  candidatos alternativos rejeitados. Não há semântica de *last-write-wins*.
- `ownership_evidence` separa `contract_lessors`, `declared_owners`,
  `cadastral_owners` e conflitos. Um senhorio declarado não substitui um
  titular da caderneta e vice-versa.
- A renda anual por hectare cria uma decisão `monthly_rent=not_applicable`;
  o campo mensal continua vazio e não gera `missing_monthly_rent`.
- A revisão por IA é particionada em `party_resolution`,
  `property_resolution` e `dates_and_terms`. Falhas e timeouts de um grupo
  ficam em `raw_json["ai_review"]["group_failures"]`, sem descartar as
  decisões aceites de outro grupo.

## Autoridade de fontes

Para identidade cadastral: caderneta > CRP > considerando > cláusula do
objeto > filename. Para papéis: designação contratual explícita >
reconhecimento > assinatura > notificações > considerando > filename. Para
datas: fórmula de assinatura > página de assinatura > reconhecimento; uma
data notarial não é promovida automaticamente a data de assinatura.

## Regressão PR098

O fixture do Step 2.8 assegura: GESTO ENERGIA, S.A. como arrendatária com
NIPC `508567475`; Aquaxais, artigo `140`, secção `J`, Penas Roias, Mogadouro
e Bragança publicados de fonte cadastral; e renda `1000.00 EUR` anual por
hectare sem mensalização inventada. Quando titular cadastral e senhorios
divergem, a divergência continua em revisão humana.

## Execução

```bash
PYTHONPATH=src python3 -m unittest tests.test_step2_8 tests.test_step3_3_3 -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Para processar o PDF real, coloque-o na pasta configurada em `INPUT_DIR` e
execute `PYTHONPATH=src python3 -m doc_register --once`. O processamento usa
paths configurados, UTF-8 e objetos JSON serializáveis; nenhuma path do caso
de regressão é embutida no código.
