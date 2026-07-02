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
2. **Load batch JSON** (try `sample_batch.json`). It opens at the first document still needing review.
3. Review each document:
   - **Add an entity:** select text, then click a label button **or press its number key (1–9)**.
   - **Remove an entity:** click the ✕ on its card, **or hover the highlight in the document and
     click the ✕** that appears over it.
   - **Set an OFAC ID:** click the entity's OFAC ID (or “edit”), type an ID, and **Save**. Blank keeps `FILL`.
   - Click a highlighted span to jump to its card (and vice-versa).
4. **Mark reviewed (start/stop):** when a document is done, click **Mark reviewed** — this toggles its
   `humanReviewRequired` flag. The top bar shows **Reviewed X / N**, and **Next unreviewed ⏭** jumps to
   the next pending document.
5. Navigate with **Prev / Next** (state is kept per document).
6. **Export annotated batch** → downloads `annotated_batch.json` (each doc carries its
   `humanReviewRequired`). **Re-load that file later to resume** — it reopens at the first doc still
   needing review, so you can stop and pick up where you left off across sessions.

**Labels & colors** are fixed in the UI: **FTO = blue (key 1)**, **POI = red (key 2)**,
**ORG = green (key 3)**. The batch file's `labels` field is ignored.

## Variant — `annotator_badged.html`
Identical to `annotator.html`, but it **prompts for the annotator's full name** right after a batch
is loaded (tagging is blocked until entered), shows `Annotator: <name>` in the top bar, and stamps
**`annotatorID: "<full name>"`** onto each document that gets marked reviewed. On export, reviewed
docs carry both `humanReviewRequired: false` and `annotatorID`; unreviewed docs carry neither name.
Existing `annotatorID`s in a re-loaded file are preserved, so multiple reviewers' work accumulates.

## Data contracts

### Batch in (loaded via “Load batch JSON”)
```json
{
  "labels": ["FTO", "ORG", "POI"],
  "documents": [
    {
      "id": "training_doc_CECGHHTE.txt",
      "text": "Acme Corp wired funds to Volkov Industries …",
      "humanReviewRequired": true,
      "initialEntities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"}],
      "metaData": [{"startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "FILL"}]
    }
  ]
}
```
- `labels` optional (defaults to `["FTO","ORG","POI"]`).
- `id` may also be `file`; `text` may also be `source`.
- `humanReviewRequired` optional — `true` (default) = not yet reviewed, `false` = finished.
  Either casing (`humanReviewRequired` / `HumanReviewRequired`) is accepted on load.
- `initialEntities` / `metaData` are **parallel, offset-aligned** (the same shape the
  Comprehend converter already emits). `ofacID` of `"FILL"` is the placeholder the
  reviewer replaces. A bare JSON array of documents (no wrapper) is also accepted.
- **Offsets are character offsets** into `text`.

### Annotated out (downloaded as `annotated_batch.json`)
```json
{
  "documents": [
    {
      "file": "training_doc_CECGHHTE.txt",
      "text": "Acme Corp wired funds to Volkov Industries …",
      "humanReviewRequired": false,
      "entities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"}],
      "metaData": [{"startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "OFAC_1001"}]
    }
  ]
}
```
- `humanReviewRequired` round-trips so you can stop/resume: re-load this file and the annotator
  reopens at the first doc still `true`. (Reviewed docs export `false`.)
- `text` is included so the export is self-contained for the downstream partition step.
- `entities` and `metaData` are parallel. `ofacID` is included **only when set** — spans
  left as `"FILL"` have it dropped. `confidence` is `null` for human-added spans.
- This matches the post-annotation / partition contract in the repo, so the downstream
  ≤5000-byte partitioning (`training_prep/partition.py`) consumes it without translation.

## Notes
- **No dependencies / no network.** Everything (HTML, CSS, JS) is in `annotator.html`.
- Functionality mirrors the original Ground Truth Crowd-HTML template: highlight/label spans,
  a running entity list, and a click-to-edit OFAC ID per entity (`FILL` placeholder). There is
  no separate OFAC reference/lookup panel.
- The highlighter is plain JS: selections are mapped to character offsets via
  `Range.toString().length`, so they stay exact even with nested highlight spans.
- New, overlapping selections are rejected (one label per span; remove and re-add to change).
- Orchestration: `databricks/ner_pipeline_notebook.py` produces the batch JSON (Comprehend v7)
  and consumes the export → partition. Field names are identical across the pipeline, so that
  glue needs no translation.
