# Step 3.2.0 — Internal Caderneta Recovery for Both Pipelines

Lease contracts in this collection commonly include their `Caderneta Predial
Rústica` after page 20, rather than only at the end.  Step 3.2.0 extracts pages
20 through the end of the PDF, preferring the OCR PDF already generated for the
contract.

The shared detector recognizes `Caderneta Predial Rústica` and both `Modelo A`
and `Modelo B`, then includes the following pages as the same annex.  It reads
the caderneta's holder name, property/localization, matrix article, section and
total area.

The secondary `property-scan` writes `owner_name` alongside the property
fields in `Property Extraction`.  The main `scan` uses the same evidence before
its final contract resolution; caderneta values are authoritative for the
property fields and holder name.  Run
`doc-register scan --config config.json --reprocess-cadernetas` to replace the
existing main-register row for each source PDF rather than append duplicates.
