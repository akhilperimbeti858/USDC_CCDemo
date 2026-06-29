# Local NER Annotator (off-AWS)

A single, fully-offline HTML page that replaces the SageMaker Ground Truth worker UI for
the **human review** step — no AWS, no Cognito, no Ground Truth, no CDN, no server. Open
`annotator.html` in any browser, load a batch file, label entities + set OFAC IDs, and
export the results.

It exists because Cognito (required by Ground Truth's private workforce) isn't on the
approved software list. The labeling UI was just HTML/JS, so it's reproduced here as a
standalone tool. Comprehend (the recognizer) is still called separately — from a
Databricks notebook — which produces the **batch file** this tool consumes and ingests
the **annotated file** this tool exports.

## Where it fits

```
Databricks notebook
  pull ecata.sentences → Comprehend recognizer (v7) → output.tar.gz → parse
        │   produces  ▼
        BATCH JSON  ──────────►  annotator.html (this tool)  ──────────►  ANNOTATED JSON
                                  human highlights + OFAC IDs                    │
                                                                                 ▼
                                                            Databricks notebook (later):
                                                            partition to ≤5000-byte training
                                                            docs (training_doc_*_part_N.txt)
```

(SharePoint is fine as the place to drop the BATCH JSON and pick up the ANNOTATED JSON.)

## Usage

1. Open **`annotator.html`** in a browser (double-click it — no server needed).
2. **Load batch JSON** (try `sample_batch.json`).
3. Optionally **Load OFAC list** (try `sample_ofac_list.json`) to enable the reference panel.
4. Review each document:
   - **Add an entity:** select text in the document, then click a label button (FTO / ORG / POI).
   - **Remove an entity:** click the ✕ on its card in the Entities panel.
   - **Set an OFAC ID:** click the entity's OFAC ID (or “edit”). In the modal, type an ID, or
     search the **OFAC reference** panel and click a matching row to fill it. Blank keeps `FILL`.
   - Click a highlighted span to jump to its card (and vice-versa).
5. Navigate with **Prev / Next** (state is kept per document).
6. **Export annotated batch** → downloads `annotated_batch.json`.

## Data contracts

### Batch in (loaded via “Load batch JSON”)
```json
{
  "labels": ["FTO", "ORG", "POI"],
  "documents": [
    {
      "id": "training_doc_CECGHHTE.txt",
      "text": "Acme Corp wired funds to Volkov Industries …",
      "initialEntities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"}],
      "metaData": [{"startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "FILL"}]
    }
  ]
}
```
- `labels` optional (defaults to `["FTO","ORG","POI"]`).
- `id` may also be `file`; `text` may also be `source`.
- `initialEntities` / `metaData` are **parallel, offset-aligned** (the same shape the
  Comprehend converter already emits). `ofacID` of `"FILL"` is the placeholder the
  reviewer replaces. A bare JSON array of documents (no wrapper) is also accepted.
- **Offsets are character offsets** into `text`.

### OFAC list in (optional, “Load OFAC list”)
```json
[
  {"id": "OFAC_1001", "name": "Acme Corp", "aliases": ["Acme Corporation"], "program": "SDN", "country": "United States"}
]
```
Searchable by **name, alias, or id**; shown as Name · Aliases · Program · Country · ID.

### Annotated out (downloaded as `annotated_batch.json`)
```json
{
  "documents": [
    {
      "file": "training_doc_CECGHHTE.txt",
      "text": "Acme Corp wired funds to Volkov Industries …",
      "entities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"}],
      "metaData": [{"startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "OFAC_1001"}]
    }
  ]
}
```
- `text` is included so the export is self-contained for the downstream partition step.
- `entities` and `metaData` are parallel. `ofacID` is included **only when set** — spans
  left as `"FILL"` have it dropped. `confidence` is `null` for human-added spans.
- This matches the existing post-annotation / partition contract in the repo, so the
  downstream ≤5000-byte partitioning (`lambdas/partition_training_docs/handler.py`, reusable
  as plain Python) consumes it without translation.

## Notes
- **No dependencies / no network.** Everything (HTML, CSS, JS) is in `annotator.html`.
- The highlighter is plain JS: selections are mapped to character offsets via
  `Range.toString().length`, so they stay exact even with nested highlight spans.
- New, overlapping selections are rejected (one label per span; remove and re-add to change).
- Not yet built (next round): the Databricks notebook (`ecata.sentences` → Comprehend v7 →
  batch JSON; consume the export → partition). Field names here are identical to the rest of
  the pipeline so that glue is straightforward.
