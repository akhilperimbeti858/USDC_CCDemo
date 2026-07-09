# SharePoint integration (off-AWS NER review loop)

How to run the whole review loop on a **SharePoint site** your team can create itself —
no Azure/Entra app registration, no server, no Cognito. Reviewers open the annotator
**locally in Chrome** and their loads/saves/partial-saves land **directly in a shared
SharePoint folder** (via OneDrive sync); Databricks pushes inputs and pulls annotations
over your **existing SharePoint connection**; and a small **parsing step**
(`extract_training_data.py`) turns finished annotations into Comprehend training data.

## Do we need to rename `annotator.html` to `.aspx`? — No.

Renaming it would **not** make it run inside SharePoint, and you don't need it to.

- Modern SharePoint **will not execute an uploaded custom HTML/JS file**, whether it's
  named `.html` or `.aspx`. Custom inline script is disabled by default (the "NoScript"
  tenant setting), and `.aspx` pages are **server-rendered SharePoint pages**, not a
  container for your own client-side JavaScript. Opening the file from the SharePoint web
  UI just downloads it or shows its source — that's expected.
- Truly hosting a custom JS UI *inside* SharePoint requires an **SPFx web part** deployed
  through the tenant App Catalog — developer tooling plus IT/admin involvement. Out of
  scope, and unnecessary here.
- **Our design runs the annotator locally instead.** SharePoint is only the **file
  store**. Each reviewer OneDrive-**syncs** the library so it appears as a local folder,
  then **double-clicks `annotator.html`** (kept as a normal `.html`) to open it in Chrome.
  It reads/writes the synced folder locally; OneDrive pushes those files back to
  SharePoint. No `.aspx`, no SPFx, no admin.

> Keep one master copy of `annotator.html` in the library so everyone opens the same
> version from their synced copy — but they always run the **local** copy, never the
> SharePoint-rendered one.

## Architecture

```
Databricks ──(your existing SharePoint connection)──►  SharePoint document library "ner"
                                                          │  batch_<ID>/input/batch_<ID>.json
   reviewer PC:  OneDrive sync  ◄───────────────────────┤
        │  library shows up as a local folder            │
        ▼   (Chrome, File System Access API)              ▲
   annotator.html  ── reads input/, writes ──►  batch_<ID>/annotations/batch_<ID>.<reviewer>.json
                                                          │  OneDrive syncs it back up
        parsing step (this folder) or consolidation/ ─────┘
                                   └─► training/docs/*.txt + annotations.csv  ─► Comprehend v8
```

## Library layout (one folder per batch)

```
ner/
  batch_<ID>/
    input/         batch_<ID>.json                     # Databricks writes this
    annotations/   batch_<ID>.<reviewer>.json          # reviewer exports (full or partial)
                   batch_<ID>.<reviewer>.inprogress.json  # autosaves (optional)
    consolidated/  (optional — written by consolidation/)
    training/      docs/*.txt + annotations.csv        # parsing step output
  annotator.html                                       # master copy (opened locally)
```

## Setup runbook (you, in the browser — no code)

1. **Create a Team site** (e.g. "NER Review").
2. **Create a document library** `ner` and, per batch, the folders
   `batch_<ID>/{input,annotations,consolidated,training}`.
3. **Turn on versioning** on the library (recovers from any sync conflict).
4. Give reviewers **Edit** on the library (or on specific `batch_<ID>` folders).
5. Reviewers click **Sync** → the library mounts under their OneDrive as a local folder.
   Set the batch folder to **"Always keep on this device"** so Chrome can read it without
   a stall (avoids Files On-Demand placeholders).
6. Drop the master **`annotator.html`** in the library root.
7. Create the **Batch Review Tracking** list (below) — the "fill out a form" surface.

### Batch Review Tracking (SharePoint List — the "forms" piece)

One row per batch, updated through the list's built-in form (no app needed):

| Column | Type | Notes |
|---|---|---|
| Batch ID | Single line / Number | key |
| Reviewer(s) | Person (multi) | who's assigned |
| Status | Choice | Not started / In progress / Reviewed / Consolidated |
| Documents | Number | count in the batch |
| Folder link | Hyperlink | to `batch_<ID>/` |
| Notes | Multi-line | anything worth flagging |

(SharePoint adds Modified / Modified By automatically for a light audit trail.)
A SharePoint List/Microsoft Form can capture this workflow metadata, but **cannot do the
span-highlighting NER annotation** — that stays in the local `annotator.html`.

## Reviewer steps

1. Sync the `ner` library (once).
2. Double-click `annotator.html` (opens in Chrome).
3. Load `batch_<ID>/input/batch_<ID>.json`; annotate; set OFAC IDs; **Mark reviewed**.
4. Export / Save progress into `batch_<ID>/annotations/` as `batch_<ID>.<yourname>.json`.
   OneDrive syncs it to SharePoint.
5. Update the batch's row in **Batch Review Tracking**.

## Parsing step — `extract_training_data.py`

Turns **finished** annotations into Comprehend training data. It reads a single export
(e.g. `Batch_3.json`) or a whole `annotations/` folder, pulls only the documents with
**`humanReviewRequired == false`** (so a *partially* reviewed file contributes just its
done docs), and partitions them into ≤5000-byte training docs + `annotations.csv`,
reusing `training_prep/partition.py`.

```bash
# a single file:
python sharepoint/extract_training_data.py .../ner/batch_3/annotations/Batch_3.json .../batch_3/training
# or a whole folder of reviewer exports:
python sharepoint/extract_training_data.py .../ner/batch_3/annotations   .../batch_3/training
```

Output: `training/docs/<file>.txt` (one per part) + `training/annotations.csv`
(`File,Line,Begin Offset,End Offset,Type`). Reviewed **no-hit** docs are kept as valid
zero-entity training examples; docs still `true` are skipped. Re-run any time as more docs
get reviewed — it's idempotent. Same doc in two files is de-duplicated (last wins).

**Quick path vs. full consolidation:** this parser is the fast "annotated → training"
route and needs **no Comprehend input batch**. Use **[`consolidation/`](../consolidation)**
when you also want the hit/no-hit × reviewed/unreviewed **categories**, the multi-reviewer
**merge summary**, and **conflict** reporting (latest-mtime-wins). Both reuse the same
`partition.py`, so the training output is identical.

## Files

| file | what it is |
|---|---|
| `extract_training_data.py` | pull `humanReviewRequired==false` docs → training docs + `annotations.csv` |
| `test_extract_training_data.py` | offline test (partial files, no-hit examples, de-dup) |
| `sample/Batch_3.json` | example export: 2 reviewed (a hit + a confirmed clean) + 1 unreviewed |

## Verification

```bash
python sharepoint/test_extract_training_data.py
python sharepoint/extract_training_data.py sharepoint/sample sharepoint/sample/_out
```

## Caveats

- **Chrome/Chromium only** for the direct-folder writes (File System Access API); other
  browsers fall back to manual upload/download in the library.
- OneDrive is local-first and syncs up in seconds — run the parsing step / consolidation
  after sync settles; library **versioning** covers any conflict.
- Each reviewer writes their **own** file, so two reviewers never collide on one file.
