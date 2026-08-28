# Step 3.1.2 — Unified Contract and Caderneta Evidence

Step 3.1.2 ensures every lease result can reconcile contract evidence with a
`Caderneta Predial` regardless of where it appears:

- in a separate PDF in the same folder, matched directly or through a CRP;
- in the final pages of the contract itself.

CRPs are now recognised from a `CRP_*` filename when OCR text is weak. Their
matrix article is recovered from the filename and normalized so `140-J` and
`140J` agree. This enables unique `Caderneta → CRP → Contract` chains based on
SharePoint sequence numbers without relying on that sequence alone.

The contract, external caderneta and same-PDF caderneta are reconciled before
the local LLM can run. The output records all caderneta evidence sources and
whether a same-PDF caderneta was found. Pipeline version: `3.1.2`.
