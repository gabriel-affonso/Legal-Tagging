# Step 3.6 — Evidence Resolution and Vision Recall

Step 3.6 changes the register from an extraction-centric pipeline to an
evidence-centric pipeline. Every valid candidate is retained in
`raw_json["step3_6_evidence"]`, with its field, value, page, source,
confidence and authority score. The published spreadsheet value is a resolved
view of that graph.

The source hierarchy is: `CONTRACT_EXPLICIT` (100), `CADERNETA`/`CRP` (95),
bank and identity documents (90), verified Vision (85), deterministic recovery
(70), OCR (65), and LLM extraction (50). A lower-authority LLM candidate can
fill an empty field, but cannot replace documentary evidence.

Conflicting values are never deleted. They remain in the evidence graph and in
`evidence_conflicts`; conflicts require review. Ownership is represented as
separate roles: contractual lessors/lessees, cadastral owners, registered
owners, CRP active/passive subjects, and a multi-owner `owners` list with name,
tax ID and ownership share.

## Vision Recall

Use `--vision-recall` with a normal scan to force a targeted pass, or rerun the
latest registered documents in place:

```bash
python -m doc_register --vision-recall-last 10 --config config.json
```

Recall does not reprocess an entire PDF visually. It selects the first five
pages, final three pages, signature pages, property-clause pages and relevant
annexes, then requests only unresolved/conflicting fields. It remains local to
the configured Ollama Vision model and preserves the Step 3.5 acceptance rules.

Before OCR, each page is assessed at 0°, 90°, 180° and 270° using a linguistic
score when local Tesseract is available; PDF rotation metadata is a safe
fallback. OCRmyPDF subsequently applies its own `--rotate-pages` correction.
