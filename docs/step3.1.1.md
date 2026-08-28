# Step 3.1.1 — OCR-Aware Property Pack Discovery

The first Step 3.1 run showed `property_pack_candidate_count = 0`: the
catalogue was reading only native PDF text, while relevant cadernetas and CRPs
often require cached OCR.

Step 3.1.1 indexes only likely property documents (caderneta, CRP, certidão,
matriz and registo), using the same OCR-reuse path as the property pipeline.
It records the text source and distinguishes `property_catalog_no_caderneta`
from `property_pack_not_found`.

Matching remains conservative: direct property identifiers win; a unique
contract–CRP–caderneta chain may use adjacent SharePoint `SP` numbers only
when CRP and caderneta agree on property facts. Weak or ambiguous matches are
never applied automatically.

Audit values are cumulative, so invalid fields and discovery failures are both
preserved. A failed recovery keeps the original confidence rather than
recomputing a higher score. Results are written as pipeline version `3.1.1`.
