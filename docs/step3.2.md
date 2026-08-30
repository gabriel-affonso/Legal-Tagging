# Step 3.2 — Controlled Cadastral Decisions

Step 3.2 strengthens the main document pipeline and the independent
`property-scan` pipeline after internal caderneta discovery.

## Decision policy

- Every PDF is checked for a caderneta before the main pipeline decides whether
  it is a lease annex. OCR is used for a lease candidate only when native text
  did not reveal a caderneta.
- Each caderneta title and its following pages become one independent property
  group. Fields from different groups are never merged.
- With one caderneta, its holder, property name/localization, matrix article,
  section and total area are authoritative for cadastral fields.
- With multiple cadernetas, the secondary pipeline selects one only when its
  article uniquely agrees with the contract. Otherwise it returns
  `multiple_internal_cadernetas_unresolved` for human review.
- The main pipeline blocks automatic cadastral publication when several
  internal cadernetas are present, preserving the candidates and page evidence
  in the audit trail.

## Conflicts and Excel audit

When a contract clause and caderneta disagree on property name, article,
section or total area, the caderneta value remains selected but the document is
marked for review. The main register exposes `cadastral_evidence_status`,
`cadastral_evidence_pages` and `cadastral_conflicts`. The `Property Extraction`
worksheet exposes the internal caderneta status and count as separate columns.

## LLM boundary

Raw caderneta OCR pages are excluded from the LLM property context. The local
LLM receives only a concise `FACTOS CADASTRAIS VERIFICADOS` summary. The final
cadastral decision remains deterministic and is never made by the LLM.
