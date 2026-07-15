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
7. **You don't have to re-download to save progress.** Every edit is **auto-saved in the browser**
   (localStorage) — a subtle **Saved ✓** confirms it, and a refresh/crash/closed tab loses nothing.
   Re-load the same batch and the annotator **offers to restore** your saved edits (the badged variant
   keys autosave by Batch ID; the plain one uses a single `default` key). **💾 Save progress** downloads
   a re-loadable copy (same schema as Export) whenever you want to hand partial work to someone else.

**Labels & colors** are fixed in the UI: **FTO = blue (key 1)**, **POI = red (key 2)**,
**ORG = green (key 3)**. The batch file's `labels` field is ignored.

## Variant — `annotator_badged.html`
Identical to `annotator.html`, but right after a batch is loaded it **prompts for the annotator's
full name and a numeric Batch ID** (tagging is blocked until both are entered) and shows
`Annotator: <name> · Batch <id>` in the top bar. It stamps **`annotatorID: "<full name>"`** onto
each document that gets marked reviewed. On export, reviewed docs carry both
`humanReviewRequired: false` and `annotatorID`; unreviewed docs carry neither. Existing
`annotatorID`s in a re-loaded file are preserved, so multiple reviewers' work accumulates.
The **Batch ID is used only in the download filename** — `annotated_batch_{BATCH_ID}.json` — and is
**not** written into the JSON content.

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
- **Consolidating many reviewers' exports** (computed hit/no-hit × reviewed/unreviewed
  categories, multi-reviewer merge, training-set assembly) lives in **`consolidation/`** —
  see `consolidation/README.md`.

## Variants — `annotator_ofac.html` and `annotator_ofac_sharepoint.html`

Both build on the badged annotator and add:
- **Initials** (≤3 letters, no spaces) instead of a full name; **JOB-ID** read from the batch
  payload (`job_id`) and shown as `Annotator: ABC · Job <id>`.
- **COUNTRY** per document (payload `country`, or a top-level `countries` map keyed by filename):
  a document pill, a group-by-country dropdown, and **Next unreviewed · country**.
- An **integrated OFAC list panel**. Load the list with **Load OFAC list** (CSV `ID, Type, Text`
  plus optional `Program` and `Country`; one row per name/alias, grouped by ID; countries single
  or `;`-joined), or embed it in the batch JSON as `ofacList`. Empty search box → entities
  **linked to the current document's COUNTRY**; otherwise search **name / alias / OFAC ID**
  (case-insensitive, with an **Exact case** toggle). Click an entity card to target it, then click
  an OFAC row to assign its ID. Results show **Name (ID)** then **Type / Program / Countries / aka**
  each on its own line.

**`annotator_ofac.html`** exports via download: **💾 Save progress** →
`annotated_batch-{initials}.json`, **⬇ Export** → `annotated_batch-{initials}_finished.json`.

**`annotator_ofac_sharepoint.html`** replaces those with a single **⬆ Save to SharePoint** that
POSTs the annotated JSON to a **Power Automate** flow (no Entra app, works inside the SharePoint
embed). Set the `FLOW_URL` constant near the top of the file to your flow's URL.

## End-to-end SharePoint + Power Automate setup

Everything needed to run `annotator_ofac_sharepoint.html` against SharePoint, in order.
**You create two flows total:** one to **Save** reviews (write) and one to **Get/List**
(read) — the read flow serves **both** the batches *and* the OFAC list, so there's no third
flow. (The two behave differently: the read flow's Response must send a CORS header because
the browser reads the reply; the save flow is fire-and-forget.)

### 1. SharePoint folders (manual, one-time)
Under a top `ner` folder in your library, create:
- **`ner/input`** — batches to review (`batch_<jobId>.json`); your Databricks SharePoint
  connection drops them here, or you upload them.
- **`ner/annotations`** — where saved reviews land.
- **`ner/reference`** — upload the OFAC CSV here as `ofac_list.csv` (re-upload when it changes).

Note each folder's server-relative path (e.g. `/sites/<YourSite>/Shared Documents/ner/annotations`).

### 2. Flow A — Save reviews (write) → `FLOW_URL`
1. **make.powerautomate.com → Create → Instant cloud flow → "When an HTTP request is received"**.
2. *(Recommended)* **Condition:** `triggerOutputs()?['queries']?['key']` **is equal to** your
   secret; in **If no** add a **Response** = 403 (stops here).
3. **Send an HTTP request to SharePoint:**
   - **Site Address** = your site · **Method** = `POST`
   - **Uri** = `_api/web/GetFolderByServerRelativeUrl('/sites/<YourSite>/Shared Documents/ner/annotations')/Files/add(url='@{triggerOutputs()?['queries']?['filename']}',overwrite=true)`
   - **Body** = `@{triggerBody()}`
4. **Save**, then reopen the trigger and copy the **HTTP POST URL** → this is **`FLOW_URL`**.

> `overwrite=true` updates the file in place (so re-saving to continue previous work replaces
> it). The plain **Create file** action does **not** overwrite — it makes numbered duplicates.
> Filenames are per-reviewer (`annotated_batch-{initials}.json`), so reviewers only overwrite
> their own file. The page posts `text/plain` + `no-cors` (a CORS "simple request" — no
> preflight — works inside the embed); `filename`/`jobId` ride as query params.

### 3. Flow B — Get / List (read; batches **and** OFAC) → `GET_FLOW_URL`
1. New **Instant cloud flow → "When an HTTP request is received"**.
2. *(Recommended)* the same **key Condition** (403 if the secret doesn't match).
3. **Condition:** is `triggerOutputs()?['queries']?['file']` **empty**?
   - **Empty → list:** branch on `triggerOutputs()?['queries']?['folder']` so the picker can
     list fresh batches **or** in-progress files:
     - `= annotations` → **List folder** on `ner/annotations` → **Select** each item to
       `concat('annotations/', item()?['{Name}'])`
     - otherwise → **List folder** on `ner/input` → **Select** each item to
       `concat('input/', item()?['{Name}'])`
     Then **Response** 200, **Body** = the list, **Headers** `Access-Control-Allow-Origin: *`.
     (Names come back folder-prefixed, so the get branch's `/ner/<file>` path resolves either way.)
   - **Not empty → get:** SharePoint **Get file content using path**, **File Path** =
     `@{concat('/ner/', triggerOutputs()?['queries']?['file'])}` → **Response** 200,
     **Body** = the file content, **Headers** `Access-Control-Allow-Origin: *` and
     `Content-Type: application/json`.
4. **Save**, copy this trigger's **HTTP POST URL** → this is **`GET_FLOW_URL`**.

> The page calls Flow B with a plain **GET** (no custom headers → no preflight); the
> **`Access-Control-Allow-Origin` header on the Response** is what lets the browser read the
> reply. `action=list` / `file=<name>` / `key=<secret>` ride as query params. **Both flow
> URLs are secrets** — the read one especially, since it returns file contents.

### 4. Fill in the HTML constants
Near the top of the `<script>` in `annotator_ofac_sharepoint.html`:
```js
const FLOW_URL     = "…Flow A save URL…";
const GET_FLOW_URL = "…Flow B get/list URL…";
const GET_FLOW_KEY = "…your secret…";            // "" if you skipped the key Condition
const OFAC_FILE    = "reference/ofac_list.csv";  // path relative to ner/
```
Save the file; distribute it or embed it in the SharePoint page.

### 5. What a reviewer does
1. Open the page → the **OFAC list auto-loads** and the **dropdown auto-fills**. Use the
   **source toggle** to list **New batches** (`input/`) or **In-progress** files
   (`annotations/`, to resume on another machine).
2. Enter **initials**.
3. Pick a batch → **⬇ Load** → annotate (labels, OFAC IDs).
4. **⬆ Save to SharePoint** → writes/overwrites `annotated_batch-{initials}.json` in `annotations/`.

### Caveats
- **Premium connectors:** the HTTP-request trigger and "Send an HTTP request to SharePoint"
  are Premium Power Automate.
- **Cross-machine resume:** the source toggle can list `annotations/`, so a reviewer can
  reload their partly-finished `annotated_batch-{initials}.json` on another machine and
  continue. (The annotated file doesn't carry `job_id`, so the top-bar Job badge and the
  per-job autosave key won't repopulate from it — the review state itself restores fully.)

## No-Premium variant — `annotator_ofac_local.html` (local folder, File System Access API)

Same annotator (initials, JOB-ID, COUNTRY, OFAC panel) with **no Power Automate / no Premium** —
it reads/writes a **local folder** (e.g. an OneDrive-synced SharePoint library) directly via the
browser **File System Access API**. **Chrome or Edge, run locally** (not inside the SharePoint
embed — the iframe blocks this API).

- **📂 Open folder** — pick any folder; you're asked **every time** (the folder is **not
  remembered**). No subfolder layout is assumed: **every `*.json` in the folder (recursively)** is
  listed in the dropdown (relative path), so you can load a fresh batch **or** resume a saved file.
  It also auto-loads the OFAC list if it finds a CSV whose name contains `ofac`.
- **⬇ Load** — reads the selected file. **💾 Save to folder** — writes the current in-progress state
  to **`inprogress/inprogress_{initials}.json`**.
- **⬇ Export reviewed** — after a **confirmation**, splits the batch and writes
  **`reviewed/reviewed_{initials}.json`** (docs marked reviewed) and
  **`unreviewed/unreviewed_{initials}.json`** (the rest), then **restarts the page** (so the folder
  is re-selected for the next batch).

The `inprogress/`, `reviewed/`, `unreviewed/` subfolders are created as needed; writes overwrite in
place. Non-Chromium browsers fall back to the **Load batch JSON** / **Load OFAC list** file pickers.
