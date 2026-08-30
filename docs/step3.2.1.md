# Step 3.2.1 — Position-Independent Internal Caderneta Discovery

The pipeline no longer assumes that a `Caderneta Predial Rústica` starts after
any specific page.  Both the main register and `property-scan` inspect every
page of a lease contract, preserving page markers and selecting the annex from
its own title and structured labels.

Recognized title variants include `Caderneta Predial Rústica` and
`Actualização de Caderneta Predial Rústica`, in Modelo A or Modelo B.  The
title page and following caderneta pages provide the holder name, property
name/localization, matrix article, section and total area.

Pipeline version: `3.2.1`.
