# ADR 0001 — Document AI v2 for rent re-search

## Status

Accepted.

## Context and decision

The historical rent scan uses the order returned by `pypdf` and starts on fixed
pages 8–9. That is fast for born-digital contracts but unsafe for scans with a
bad embedded OCR layer. Document AI v2 therefore inspects every page first,
routes it by measurable text-layer quality, and creates canonical text only
from blocks ordered by page geometry. The legacy OCR layer is not a canonical
source; it remains available only through the old pipeline for comparison.

Native PyMuPDF parsing is the cheapest route. RapidOCR is an optional local
CPU/ONNX adapter for visual pages. PaddleOCR-VL is deliberately feature-gated
and not installed in the base image: its exact supported CPU package/model
must be pinned and benchmarked before enabling it. This differs from the
initial proposal's mandatory integration because a 16 GB CPU target must not
silently download or load a large VLM.

## Consequences

`doc-register rent-scan --pipeline v2` writes canonical-cache provenance,
routes, per-block bounding boxes and a processing report to the existing Rent
Extraction sheet. Failures are isolated per page and force review. The legacy
command remains the default during migration.
