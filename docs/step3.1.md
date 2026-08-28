# Step 3.1 — Property Pack Discovery & Strict Validation

Step 3.1 corrects the core Step 3.0 assumption: a lease contract and its
`Caderneta Predial` can be separate PDFs in the same input folder.

## Execution order

```text
Contract extraction → strict validation → folder property-pack discovery
→ caderneta parser → reconciliation → focused LLM fallback
```

The folder catalogue is local and deterministic. It indexes likely caderneta
PDFs from their first pages and matches them only through high-confidence
identifiers such as `VA147`, `PR098` or `269F`. Article/section/name agreement
can strengthen a match, but cannot associate an ambiguous or weak candidate.

## Validation rules

- Matrix articles must begin with digits. Tokens such as `SOB`, `ES`, `DA` and
  `DOS` are invalid.
- Matrix sections use one letter only.
- Contractual prose, generic property descriptions and strings containing
  `doravante` or `designado` cannot be property names.
- Invalid values do not earn confidence points and trigger recovery.

## Audit and reruns

`Property Extraction` now stores the pipeline version, pack status, matching
score/method, candidate count, caderneta source file, evidence model and
recovery audit. A Step 3.1 run replaces older results for the same PDF hash,
allowing existing documents to be reprocessed without duplicate rows.

## LLM policy

The local LLM runs only after contract extraction, folder-pack recovery and
same-PDF annex fallback are insufficient. The focused-property prompt is
covered by a regression test for JSON-template formatting.
