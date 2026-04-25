# Test fixtures

Real, redistributable French-language documents used by integration tests.
Source materials are under CC BY-SA 4.0; we keep them committed here so
the test suite is deterministic and offline-runnable.

## fr_sample.pdf

- **Title:** Couscous
- **Source:** https://fr.wikipedia.org/wiki/Couscous
- **Exported via:** `https://fr.wikipedia.org/api/rest_v1/page/pdf/Couscous`
- **Accessed:** 2026-04-25
- **License:** Creative Commons Attribution-ShareAlike 4.0 (CC BY-SA 4.0)
  — https://creativecommons.org/licenses/by-sa/4.0/
- **Attribution:** Wikipedia contributors

## fr_sample_2.pdf

- **Title:** Casablanca
- **Source:** https://fr.wikipedia.org/wiki/Casablanca
- **Exported via:** `https://fr.wikipedia.org/api/rest_v1/page/pdf/Casablanca`
- **Accessed:** 2026-04-25
- **License:** Creative Commons Attribution-ShareAlike 4.0 (CC BY-SA 4.0)
  — https://creativecommons.org/licenses/by-sa/4.0/
- **Attribution:** Wikipedia contributors

## Why these two

The cross-tenant isolation test (`tests/integration/test_pipeline.py::test_tenant_isolation`)
needs two PDFs on **distinct topics**. Couscous (a North African dish) and
Casablanca (the largest city in Morocco) share no notable vocabulary
overlap, so a query intended to match one document would not plausibly
return the other unless tenant filtering is broken.

Both are short (≤8 pages, programmatic PDFs with extractable text) so the
parse → chunk → embed pipeline finishes well within the 60 s test timeout.

## Refreshing

If a regenerated copy is ever needed (Wikipedia content drifts), run from
the repo root:

```bash
curl -fsSL -o backend/tests/fixtures/fr_sample.pdf \
  https://fr.wikipedia.org/api/rest_v1/page/pdf/Couscous
curl -fsSL -o backend/tests/fixtures/fr_sample_2.pdf \
  https://fr.wikipedia.org/api/rest_v1/page/pdf/Casablanca
```

After refreshing, update the **Accessed** dates above.
