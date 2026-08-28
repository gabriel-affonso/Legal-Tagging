# Step 3.1.3 — Same-PDF Caderneta Title Detection

The documents in the first Step 3.1.2 run confirmed that the usual evidence
source is a caderneta included in the contract itself.  It is close to the end
of the PDF, but not necessarily in its final pages.

Step 3.1.3 reads a wider region near the end of the contract, beginning from
the final pages so the extraction character limit preserves them.  It detects
the title **Actualização de Caderneta Predial Rústica** (including unaccented
OCR variants) together with **Modelo B**.  Once the title is found, the title
page and the adjacent pages are parsed as a single caderneta evidence set.

This runs before the local LLM and records the contract file in
`caderneta_evidence_sources` and `caderneta_same_pdf_found = true` when the
annex is identified.  Pipeline version: `3.1.3`.
