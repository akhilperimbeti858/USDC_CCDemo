# Consolidation (off-AWS NER review loop)

Merges every reviewer's annotator export for a batch into **one status record per
document**, **computes** each document's category (no folder juggling), and assembles the
**training set** for the next Comprehend recognizer. Pure Python + a Databricks notebook —
no AWS services beyond the optional S3 upload of the finished training set.

This folder is self-contained: the merge logic (`consolidate.py`) is AWS-free and
unit-tested, `consolidate_notebook.py` is the Databricks wrapper, `run_local.py` runs the
exact same logic over an on-disk folder, and `sample/` is a runnable example.

## Why this exists — the two pains it fixes

1. **Clunky file exchange / re-download to save.** Reviewers no longer download an input
   file and re-download it every time they want to save. The annotator now **auto-saves to
   the browser** (localStorage) on every edit and has a **💾 Save progress** button; you
   only hand a file back when you're done (or want to share progress). See
   [Autosave & resume](#autosave--resume).
2. **Four folders that files migrate between.** Instead of physically moving docs between
   `hits-unreviewed` / `no-hits-unreviewed` / `hits-reviewed` / `no-hits-reviewed`, we keep
   one flat pile of files and **derive** the category. Transitions the user cares about
   (a no-hit the human turns into a hit; a false positive the human clears) need **zero
   file moves**.

## The model — categories are computed, never stored

Every document's four-way status falls out of **two facts** at consolidation time:

| fact | meaning | source |
|---|---|---|
| `comprehend_hit` | Comprehend seeded ≥1 entity for this doc | input batch `initialEntities` (immutable) |
| `reviewed` | a reviewer marked it done | export `humanReviewRequired == false` |
| `final_hit` | reviewed **and** the final entity list is non-empty | derived |

|                       | reviewed            | not reviewed          |
|-----------------------|---------------------|-----------------------|
| **final_hit**         | `hits_reviewed`     | *(n/a — hit is post-review)* |
| **not final_hit, seeded** | `no_hits_reviewed` | `hits_unreviewed`  |
| **not final_hit, unseeded** | `no_hits_reviewed` | `no_hits_unreviewed` |

The transitions become trivial because nothing moves:
- **no-hit → hit:** a doc Comprehend missed (`no_hits_unreviewed`) where the human adds a
  span is `reviewed && entities` → `hits_reviewed`. `comprehend_hit` stays `false`, so you
  can still tell it was a human-found hit.
- **false positive → no-hit:** a seeded doc (`hits_unreviewed`) the human clears is
  `reviewed && !entities` → `no_hits_reviewed`.

Because the category is computed, **re-running consolidation is idempotent** — run it as
often as you like as more reviewer files arrive.

## Folder layout (one per batch)

```
ner/batch_<ID>/
  input/        batch_<ID>.json                     # Comprehend-derived (all docs; has initialEntities)
  annotations/  batch_<ID>.<annotatorID>.json …     # ONE export per reviewer (partial or full)
  consolidated/ consolidated.json                   # merged, one status record per doc  (generated)
                summary.json                        # counts per bucket + progress + conflicts (generated)
                views/<bucket>.json                 # the four computed slices (generated)
  training/     docs/<file>.txt + annotations.csv   # partitioned reviewed docs (generated)
```

- **Reviewers only ever add files under `annotations/`.** Never overwrite `input/`.
- Naming is `batch_<ID>` + `<annotatorID>`, so N reviewers coexist with no collisions.
- Everything under `consolidated/` and `training/` is **generated** and rebuildable.
- Where the folder physically lives is up to you — a DBFS path you sync SharePoint into, a
  mount, or a local directory for `run_local.py`.

## Multi-reviewer merge rules

- Only **reviewed** docs (`humanReviewRequired == false`) override the base input — a
  partial save (a doc still `true`) never clobbers a finished one.
- Reviewer files are applied **oldest → newest by file mtime**, so the **latest** review
  wins on a document reviewed by more than one person.
- Any doc reviewed by **>1 distinct annotator** is recorded in `summary.json → conflicts`
  (with the resolved winner), so disagreements are visible, not silently dropped.

## The training set = every reviewed document

Both `hits_reviewed` **and** `no_hits_reviewed` are included — a confirmed no-hit is a
valid **zero-entity** training example that teaches the recognizer what *not* to flag.
Unreviewed docs are excluded. Partitioning reuses `training_prep/partition.py`
(`partition_document`, `part_filenames`) — the ≤5000-byte balanced, entity-safe split —
so there's no new split logic here.

## Run it

**Locally (offline), over the bundled sample:**
```bash
python consolidation/run_local.py consolidation/sample 7
# writes consolidation/sample/batch_7/{consolidated,training}/ (git-ignored)
```

**On Databricks:** open `consolidate_notebook.py`, set the widgets
(`batch_id`, `root`, `repo_root`, optionally `push_training_s3`), and run all. It reads
`<root>/batch_<ID>/{input,annotations}/` and writes `consolidated/` + `training/`.

**Tests:**
```bash
python consolidation/test_consolidate.py   # four buckets, both transitions, conflicts, partitioning
```

## Autosave & resume

The local annotator (`local_annotator/annotator.html` and `annotator_badged.html`) now:
- **Auto-saves** the full working state to the browser (localStorage) on every label add,
  removal, OFAC-ID edit, and review toggle — a subtle "Saved ✓" confirms it. A refresh,
  crash, or closed tab loses nothing.
- **💾 Save progress** downloads a re-loadable file (same schema as Export) whenever you
  want a copy or to hand partial work to another reviewer. Export and Save-progress files
  are interchangeable on re-load.
- **Resume:** the next time you load the same batch (badged: after entering the same Batch
  ID), it offers to restore your saved edits. The badged variant keys autosave by Batch ID;
  the plain variant uses a single `default` key.

The exported JSON schema is unchanged — consolidation consumes the same
`{file, text, humanReviewRequired, annotatorID?, entities, metaData}` per-doc contract the
annotator already produced.

## How it fits the pipeline

```
databricks/ner_pipeline_notebook.py  (Phase 2)   →  input/batch_<ID>.json
        │  reviewers open local_annotator/*.html, export/save …
        ▼
annotations/batch_<ID>.<annotatorID>.json  (one per reviewer)
        │  consolidation/consolidate_notebook.py  (or run_local.py)
        ▼
consolidated/ (status + views + summary)  +  training/ (partitioned reviewed docs)
        │
        ▼  Comprehend custom recognizer v8 (train on training/docs + annotations.csv)
```

## Files

| file | what it is |
|---|---|
| `consolidate.py` | pure merge/category/summary/training logic (AWS-free, unit-tested) |
| `consolidate_notebook.py` | Databricks wrapper — globs the batch folder, writes outputs, partitions |
| `run_local.py` | same logic over an on-disk folder; CLI for offline runs |
| `test_consolidate.py` | offline dry-run covering buckets, transitions, conflicts, partitioning |
| `sample/batch_7/` | runnable example: an `input/` batch + two reviewer `annotations/` files |
