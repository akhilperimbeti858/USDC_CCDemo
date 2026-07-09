# Off-AWS NER Labeling Pipeline — Overview & Function Reference

A complete, **off-AWS** loop for labeling OFAC sanctions-screening entities and
continuously retraining an Amazon Comprehend custom recognizer — **no Cognito, no
SageMaker Ground Truth, no server**. The only AWS service touched is **Comprehend**
(the recognizer itself). Everything else runs in a **Databricks notebook**, a
**fully-offline HTML annotator**, and small **pure-Python** modules.

> This document is the end-to-end map: the overall flow, the shared data contract, and a
> **function-by-function reference** for every module. For task-specific detail, each
> folder also has its own README (linked below).

## Table of contents
- [Why this exists](#why-this-exists)
- [The overall flow](#the-overall-flow)
- [The shared data contract](#the-shared-data-contract)
- [The computed-category model](#the-computed-category-model)
- [Module reference](#module-reference)
  - [`databricks/` — orchestration notebooks](#databricks--orchestration-notebooks)
  - [`local_annotator/` — the offline HTML annotator](#local_annotator--the-offline-html-annotator)
  - [`training_prep/partition.py` — training-size splitting](#training_preppartitionpy--training-size-splitting)
  - [`consolidation/` — multi-reviewer merge + categories](#consolidation--multi-reviewer-merge--categories)
  - [`sharepoint/` — SharePoint integration + quick parsing step](#sharepoint--sharepoint-integration--quick-parsing-step)
- [Running it](#running-it)
- [Roadmap / not-yet-built](#roadmap--not-yet-built)

---

## Why this exists
AWS Cognito (required by Ground Truth's private workforce) isn't on the approved software
list, so the **human review** step was moved off AWS. Comprehend still pre-labels the text;
reviewers confirm/correct entities and set OFAC IDs in a local browser tool; the confirmed
results are consolidated and partitioned into training documents that retrain the next,
sharper recognizer version. Each review cycle feeds better data back into the model — a
self-improving loop.

## The overall flow

```
┌────────────────────────────── Databricks notebook ──────────────────────────────┐
│ Phase 0  pull a batch from ecata.sentences                                        │
│ Phase 1  run Comprehend recognizer (v7)  ->  output.tar.gz                         │
│ Phase 2  parse output.tar.gz  ->  BATCH JSON  (initialEntities + metaData)         │
└───────────────────────────────────┬───────────────────────────────────────────────┘
                                     │  batch_<ID>.json  (drop in SharePoint / share)
                                     ▼
┌───────────────────────── local_annotator/annotator.html ─────────────────────────┐
│ human highlights/confirms entities, sets OFAC IDs, marks docs reviewed            │
│ autosaves in-browser; exports annotated JSON (per-doc humanReviewRequired flag)   │
└───────────────────────────────────┬───────────────────────────────────────────────┘
                                     │  batch_<ID>.<reviewer>.json  (one per reviewer)
                                     ▼
        ┌────────────────────────────┴─────────────────────────────┐
        ▼                                                            ▼
consolidation/  (full path)                          sharepoint/extract_training_data.py
  merge N reviewers -> one record/doc                  (quick path)
  compute hit/no-hit × reviewed/unreviewed             pull humanReviewRequired==false docs
  summary + conflicts + training set                   -> training data (no input batch)
        └────────────────────────────┬─────────────────────────────┘
                                     ▼
                 training/docs/*.txt  +  annotations.csv   (training_prep/partition.py)
                                     ▼
                 Phase 5  train Comprehend recognizer v8  (retrain)
```

Both downstream paths reuse the **same** `training_prep/partition.py`, so their training
output is identical in format.

## The shared data contract
Every stage speaks the **same per-document JSON**, so no translation is ever needed.

**Batch JSON (annotator input)** — produced by Databricks Phase 2:
```json
{
  "labels": ["FTO", "POI", "ORG"],
  "documents": [{
    "id": "training_doc_CECGHHTE.txt",
    "text": "Acme Corp wired funds to Volkov Industries …",
    "humanReviewRequired": true,
    "initialEntities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"}],
    "metaData":        [{"startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "FILL"}]
  }]
}
```

**Annotated JSON (annotator export)** — consumed by consolidation / the parser:
```json
{
  "documents": [{
    "file": "training_doc_CECGHHTE.txt",
    "text": "Acme Corp wired funds to Volkov Industries …",
    "humanReviewRequired": false,
    "annotatorID": "Jane Doe",
    "entities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"}],
    "metaData":  [{"startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "OFAC_1001"}]
  }]
}
```

Key rules:
- **`initialEntities` / `entities`** and **`metaData`** are **parallel, offset-aligned**
  arrays; offsets are **character** offsets into `text`.
- **`humanReviewRequired`**: `true` = not yet reviewed (default), `false` = finished. Either
  casing is accepted on load.
- **`ofacID: "FILL"`** is the unset placeholder; it's **dropped from the export** when left
  unedited. **`confidence: null`** marks a human-added span.
- **`annotatorID`** (badged variant only) is stamped onto reviewed docs.
- Labels/colors are **hardcoded**: FTO = blue (key 1), POI = red (key 2), ORG = green (key 3).

## The computed-category model
A document's four-way status is **derived at consolidation time**, never stored as a folder
location — so the transitions the team cares about need **zero file moves**:

| fact | meaning |
|---|---|
| `comprehend_hit` | the Comprehend input seeded ≥1 entity (`initialEntities` non-empty) — immutable |
| `reviewed` | a reviewer marked it done (`humanReviewRequired == false`) |
| `final_hit` | `reviewed` **and** the final `entities` list is non-empty |

|                        | reviewed            | not reviewed              |
|------------------------|---------------------|---------------------------|
| **final_hit**          | `hits_reviewed`     | —                         |
| **no final hit (seed)**| `no_hits_reviewed`  | `hits_unreviewed`         |
| **no final hit (none)**| `no_hits_reviewed`  | `no_hits_unreviewed`      |

- Comprehend **miss** the human labels → `no_hits_unreviewed` → `hits_reviewed`.
- Comprehend **false positive** the human clears → `hits_unreviewed` → `no_hits_reviewed`.

---

## Module reference

### `databricks/` — orchestration notebooks
[`databricks/README.md`](databricks/README.md) · main file `ner_pipeline_notebook.py`.

Drives Phases 0–5. The only AWS service is Comprehend.

| function / cell | what it does |
|---|---|
| `cfg(name)` | read a Databricks widget (with a default) |
| `_split_s3(uri)` | `s3://bucket/key` → `(bucket, key)` |
| `allowed_labels()`, `min_score()` | parse the label allow-list / score threshold widgets |
| **`doc_to_record(doc_id, text, entities)`** | Comprehend doc → **batch record**: keeps allowed-type entities above `min_score`, builds parallel `initialEntities` + `metaData` (`ofacID:"FILL"`), seeds `humanReviewRequired: True` |
| Phase 0 | `spark.table("ecata.sentences").limit(n)` → `[{id, text}]` |
| Phase 1 | upload ONE_DOC_PER_LINE to S3 → `StartEntitiesDetectionJob` (v7) → poll to COMPLETED |
| Phase 2 | download/parse `output.tar.gz` → **BATCH JSON** (`doc_to_record` per doc) |
| Phase 3 | *manual* — reviewer uses the local annotator |
| Phase 4 | read the export → `partition_document(...)` → training docs + `annotations.csv` to S3 |
| Phase 5 | *optional* (`DO_TRAIN`) — `create_entity_recognizer` → v8 |

`consolidate_notebook.py` (in `consolidation/`) is the multi-reviewer alternative to Phase 4.

### `local_annotator/` — the offline HTML annotator
[`local_annotator/README.md`](local_annotator/README.md) · `annotator.html` (base) and
`annotator_badged.html` (adds a name + numeric Batch ID prompt). Single self-contained
files: inline CSS+JS, **zero network requests**. Same functions in both except where noted.

**Loading & rendering**
| function | what it does |
|---|---|
| `loadBatch(obj)` | parse batch/annotated JSON → in-memory `batch` (per doc: `id, text, entities, meta, reviewRequired`); opens at the first unreviewed doc; offers autosave **resume** |
| `renderLabelBar()` | draw the FTO/POI/ORG chips with number-key hints |
| `renderDoc()` | render `text` with entity `<span>`s; sets `dataset.start`; wires hover-✕. Only entity text goes in spans so `#doc` offsets stay exact |
| `absOffset(node, nodeOffset)` | DOM selection point → absolute character offset via `Range.toString().length` (robust with nested spans) |

**Editing entities**
| function | what it does |
|---|---|
| `applyLabel(label)` | turn the pending selection into an entity (rejects overlaps); seeds `meta{confidence:null, ofacID:"FILL"}`; autosaves |
| `removeEntity(startOffset)` | drop an entity + its meta; autosaves |
| `showEntX / scheduleHideEntX / hideEntX` | floating hover-✕ (kept **outside** `#doc` so offsets stay exact) |
| `flashCard(startOffset)` / `flashDocSpan(e)` | jump between a doc span and its side-list card |
| `renderEntityList()` | side panel: one card per entity (label, offsets, confidence, OFAC ID) |
| `openModal / closeModal / saveModal` | OFAC-ID editor; blank keeps `FILL`; autosaves |

**Review status (start/stop)**
| function | what it does |
|---|---|
| `firstUnreviewed()`, `reviewedCount()`, `updateReviewStat()` | review progress helpers |
| `renderDocStatus()` | the per-doc pill + "Mark reviewed / Mark needs review" button |
| `toggleReview()` | flip `humanReviewRequired`; (badged) stamp/clear `annotatorID` |
| `gotoNextUnreviewed()` | jump to the next doc still needing review |

**Navigation & keyboard**
| function | what it does |
|---|---|
| `renderAll()`, `go(delta)` | render current doc / Prev-Next navigation |
| keydown handler | press **1–9** to apply the matching label (ignores modifiers, inputs, open modal) |

**Export, save & autosave**
| function | what it does |
|---|---|
| `buildExport()` | current state → the annotated-JSON object (drops `FILL`; `confidence:null` for human spans; badged adds `annotatorID`) |
| `downloadJson(obj, name)` | trigger a browser download |
| `exportBatch()` / `saveProgress()` | download the final / in-progress file (interchangeable on re-load; badged names by Batch ID) |
| `autosave()` | write full state to **localStorage** on every edit ("Saved ✓") — no re-download to keep progress |
| `readAutosave()` / `clearAutosave()` / `applySaved(export)` | read/clear/overlay saved state |
| `maybeResume()` | on load (badged: after Batch ID entered) offer to restore saved edits |
| `readJsonFile(input, cb)`, `escapeHtml(s)` | file-picker read / HTML-escaping |

**Badged-only:** `promptName()` and `startAnnotating()` require a full name + numeric Batch
ID before tagging; the Batch ID is used **only** in the export filename, not the JSON.

### `training_prep/partition.py` — training-size splitting
[`training_prep/README.md`](training_prep/README.md). Comprehend caps a training document at
**5000 bytes**; oversized docs are split into **balanced** parts on **whitespace** that
**never cut an entity**, with offsets **rebased** per part. Pure logic is AWS-free/unit-tested.

| function | what it does |
|---|---|
| **`partition_document(text, entities, max_bytes)`** | → `[{text, entities}]`, one per ≤max_bytes part; a doc within the limit returns unchanged; part count = `ceil(bytes/max)`, raised only if a balanced split can't fit |
| **`part_filenames(file_name, n)`** | `base.txt` → `[base.txt]` if n==1, else `base_part_1.txt … base_part_n.txt` |
| `_byte_len`, `_byte_prefix` | UTF-8 byte length / prefix-sum (the limit is measured in bytes) |
| `_is_safe_cut`, `_is_word_boundary` | a cut splits no entity / lands on whitespace |
| `_find_boundary`, `_split_positions` | nearest safe cut to a target byte / the `n` balanced boundaries |
| `_entities_of`, `_file_base`, `_text_of` | pull entities / filename / text from a record |
| `_read_s3_text`, `_write_s3`, `_resolve_input_location` | S3 I/O (module-level so tests monkeypatch) |
| `lambda_handler(event, context)` | Lambda entry: read a labeled manifest → write parts + `annotations.manifest` |

### `consolidation/` — multi-reviewer merge + categories
[`consolidation/README.md`](consolidation/README.md). The **full path**: merge every
reviewer's export into one status record per doc, compute the four categories, and assemble
the training set. Core logic in `consolidate.py` (AWS-free, unit-tested).

| function | what it does |
|---|---|
| `doc_id(rec)` | stable id from `id`/`file`/`File` (basename) |
| `_review_required(rec)` | `humanReviewRequired` with either casing; default `True` |
| **`base_records(input_batch)`** | one base record per doc, keyed by id; fixes `comprehend_hit` from `initialEntities` |
| **`merge_annotations(base, sources)`** | overlay reviewer exports (only **reviewed** docs override; applied **oldest→newest by mtime**, latest wins; records `conflicts` when >1 annotator) → `(consolidated, conflicts)` |
| **`categorize(rec)`** | → `hits_reviewed` / `no_hits_reviewed` / `hits_unreviewed` / `no_hits_unreviewed` |
| `bucketize(consolidated)` | group records into the four buckets |
| `summarize(consolidated, conflicts)` | counts per bucket + reviewed/total + conflicts (the `summary.json` payload) |
| **`training_records(consolidated)`** | **all reviewed docs** → `{file, text, entities}` (hits + confirmed no-hits) |
| **`consolidate(input_batch, sources)`** | end-to-end: `{consolidated, buckets, summary, training}` |

Wrappers: `consolidate_notebook.py` (Databricks: globs `batch_<ID>/{input,annotations}/` →
writes `consolidated/` + `training/`), `run_local.py` — `run(root, batch_id, max_bytes)` runs
the same over an on-disk folder. Batch folder layout:
`ner/batch_<ID>/{input, annotations, consolidated/{consolidated.json, summary.json, views/*}, training}`.

### `sharepoint/` — SharePoint integration + quick parsing step
[`sharepoint/README.md`](sharepoint/README.md). Runbook for hosting the loop on a
team-created **SharePoint site**: the library is **OneDrive-synced** and the annotator runs
**locally in Chrome** (File System Access API), so saves land straight in the shared folder —
**no Entra app, no server**. Includes the `.aspx` answer (you don't need it — SharePoint
won't execute uploaded HTML/JS; run the local synced copy) and the **Batch Review Tracking**
list schema (the "forms" piece).

`extract_training_data.py` is the **quick path**: annotated JSON → training data, **no
Comprehend input batch required**.

| function | what it does |
|---|---|
| `_doc_id(d)`, `_is_reviewed(d)` | id from `file`/`File`/`id`; reviewed = `humanReviewRequired == false` |
| **`reviewed_docs(*objects)`** | pull only reviewed docs across one/more exports → `{file, text, entities}`; de-dup by id (**last wins**) |
| **`to_training(reviewed, max_bytes)`** | partition → `(files=[(name,text)], csv_rows)` via `partition.py` |
| `_load_paths(path)` | a file, or every `*.json` in a folder → parsed objects |
| **`run(path, out_dir, max_bytes)`** | end-to-end: write `out_dir/docs/*.txt` + `annotations.csv`; print a summary |

Use `consolidation/` instead when you also need categories, the multi-reviewer merge summary,
and conflict reporting. Both reuse `partition.py`, so training output is identical.

---

## Running it
```bash
# Consolidation (full path) over the bundled sample:
python consolidation/run_local.py consolidation/sample 7
python consolidation/test_consolidate.py

# SharePoint quick parser (annotated -> training):
python sharepoint/extract_training_data.py sharepoint/sample sharepoint/sample/_out
python sharepoint/test_extract_training_data.py

# Annotator: open local_annotator/annotator.html in a browser, Load a batch JSON.
```
Databricks: import `databricks/ner_pipeline_notebook.py` (Phases 0–5) and, for many
reviewers, `consolidation/consolidate_notebook.py`.

## Roadmap / not-yet-built
- **OFAC-ID gate** in the annotator: block **Mark reviewed** until every entity has a real
  OFAC ID (not `FILL`), with a toast + jump to the first unfilled entity. *(Designed, not yet
  implemented.)*
- **Folder-backed annotator mode**: an "Open batch folder" button using the File System
  Access API so loads/autosaves/exports read & write the OneDrive-synced SharePoint folder
  directly (Chrome). *(Designed in `sharepoint/README.md`, not yet implemented.)*
