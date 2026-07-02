"""Consolidation logic for the off-AWS NER review loop (pure Python, no AWS).

WHY
---
Multiple reviewers annotate the same Comprehend batch. Instead of shuffling files
between "hits" / "no-hits" / "reviewed" / "unreviewed" folders (and re-downloading to
save progress), we keep **one flat pile of files** and **compute** each document's
category at consolidation time:

    input/       one Comprehend-derived batch (every doc; has ``initialEntities``)
    annotations/ one export per reviewer  (batch_<ID>.<annotatorID>.json, partial or full)

From those two facts alone every document's four-way status is *derived*, never stored
as a location:

    comprehend_hit = the input seeded entities for this doc (initialEntities non-empty)
    reviewed       = a reviewer marked it done (humanReviewRequired == false)
    final_hit      = reviewed AND the final entity list is non-empty

    ┌───────────────┬───────────────────────┬──────────────────────────┐
    │               │ reviewed              │ not reviewed             │
    ├───────────────┼───────────────────────┼──────────────────────────┤
    │ final_hit     │ hits_reviewed         │ (n/a — hit is post-review)│
    │ not final_hit │ no_hits_reviewed      │ hits_unreviewed  (if seed)│
    │               │                       │ no_hits_unreviewed (else) │
    └───────────────┴───────────────────────┴──────────────────────────┘

The transitions the user cares about need **zero file moves**:
  * a doc Comprehend missed (``no_hits_unreviewed``) where the human adds spans →
    becomes ``hits_reviewed`` simply because ``final_hit`` is now true.
  * a Comprehend false positive (``hits_unreviewed``) the human clears →
    becomes ``no_hits_reviewed``.

This module is AWS-free and unit-tested; the Databricks glue (globbing files, reading
their mtimes, writing outputs, partitioning) lives in ``consolidate_notebook.py``.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Bucket names (computed categories) — the vocabulary the whole loop speaks.
# ---------------------------------------------------------------------------
HITS_REVIEWED = "hits_reviewed"
NO_HITS_REVIEWED = "no_hits_reviewed"
HITS_UNREVIEWED = "hits_unreviewed"
NO_HITS_UNREVIEWED = "no_hits_unreviewed"
BUCKETS = (HITS_REVIEWED, NO_HITS_REVIEWED, HITS_UNREVIEWED, NO_HITS_UNREVIEWED)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def doc_id(rec):
    """Stable document id. Accepts ``id`` or ``file`` (basename), like the rest of the repo."""
    for key in ("id", "file", "File"):
        if rec.get(key):
            return str(rec[key]).rsplit("/", 1)[-1]
    return None


def _review_required(rec):
    """``humanReviewRequired`` with either casing; default True (unseen)."""
    for key in ("humanReviewRequired", "HumanReviewRequired"):
        if key in rec and rec[key] is not None:
            return bool(rec[key])
    return True


# ---------------------------------------------------------------------------
# Step 1 — base records from the Comprehend input batch
# ---------------------------------------------------------------------------
def base_records(input_batch):
    """One base record per document, keyed by id.

    ``comprehend_hit`` is fixed here (did Comprehend seed any entity?) and never changes
    — it's what lets us tell a human-found hit from a machine-found one later.
    """
    docs = input_batch if isinstance(input_batch, list) else input_batch.get("documents", [])
    out = {}
    for d in docs:
        did = doc_id(d)
        if did is None:
            continue
        seeds = list(d.get("initialEntities") or d.get("entities") or [])
        out[did] = {
            "id": did,
            "text": d.get("text") or d.get("source") or "",
            "comprehend_hit": len(seeds) > 0,
            "comprehend_entities": seeds,
            # filled by review merge; defaults describe an untouched (unreviewed) doc
            "reviewed": False,
            "entities": None,
            "metaData": [],
            "annotatorID": None,
        }
    return out


# ---------------------------------------------------------------------------
# Step 2 — merge reviewer annotation files over the base
# ---------------------------------------------------------------------------
def merge_annotations(base, annotation_sources):
    """Overlay reviewer exports onto ``base`` (from :func:`base_records`), in place.

    ``annotation_sources`` is an iterable of dicts::

        {"source": "batch_7.alice.json", "mtime": 1719_000_000, "documents": [ ... ]}

    Merge rules:
      * Only **reviewed** docs (``humanReviewRequired == false``) override the base;
        an unreviewed doc in a partial save never clobbers a finished one.
      * Sources are applied **oldest → newest by mtime**, so the **latest** review wins.
      * When >1 *distinct* annotator reviews the same doc, it's recorded in ``conflicts``
        (the latest-mtime annotator is the resolved winner).

    Returns ``(consolidated_list, conflicts)`` where ``consolidated_list`` is the base
    records (now merged) sorted by id, each with a computed ``final_hit`` + ``category``.
    """
    reviewers_by_doc = {}   # id -> ordered list of annotator ids that reviewed it
    ordered = sorted(annotation_sources, key=lambda s: (s.get("mtime", 0), s.get("source", "")))

    for src in ordered:
        annotator = src.get("annotatorID") or src.get("source") or "?"
        for d in src.get("documents", []):
            did = doc_id(d)
            if did is None or did not in base:
                continue
            if _review_required(d):          # partial / unreviewed — never overrides
                continue
            rec = base[did]
            rec["reviewed"] = True
            rec["entities"] = list(d.get("entities") or [])
            rec["metaData"] = list(d.get("metaData") or [])
            rec["annotatorID"] = d.get("annotatorID") or annotator
            if d.get("text"):               # keep text authoritative if the export carried it
                rec["text"] = d["text"]
            reviewers_by_doc.setdefault(did, [])
            who = rec["annotatorID"]
            if who not in reviewers_by_doc[did]:
                reviewers_by_doc[did].append(who)

    conflicts = [
        {"id": did, "annotators": names, "resolved": base[did]["annotatorID"]}
        for did, names in reviewers_by_doc.items() if len(names) > 1
    ]

    consolidated = []
    for did in sorted(base):
        rec = base[did]
        rec["final_hit"] = bool(rec["reviewed"] and rec["entities"])
        rec["category"] = categorize(rec)
        consolidated.append(rec)
    return consolidated, conflicts


# ---------------------------------------------------------------------------
# Step 3 — categorize / bucketize / summarize
# ---------------------------------------------------------------------------
def categorize(rec):
    """Return one of :data:`BUCKETS` for a consolidated record."""
    if rec["reviewed"]:
        return HITS_REVIEWED if rec.get("final_hit") else NO_HITS_REVIEWED
    return HITS_UNREVIEWED if rec.get("comprehend_hit") else NO_HITS_UNREVIEWED


def bucketize(consolidated):
    """Group consolidated records into the four computed buckets."""
    out = {b: [] for b in BUCKETS}
    for rec in consolidated:
        out[rec["category"]].append(rec)
    return out


def summarize(consolidated, conflicts):
    """Counts per bucket + review progress + conflicts — the ``summary.json`` payload."""
    buckets = bucketize(consolidated)
    reviewed = sum(1 for r in consolidated if r["reviewed"])
    total = len(consolidated)
    return {
        "total": total,
        "reviewed": reviewed,
        "unreviewed": total - reviewed,
        "counts": {b: len(buckets[b]) for b in BUCKETS},
        "conflicts": conflicts,
    }


# ---------------------------------------------------------------------------
# Step 4 — the training set = every reviewed document
# ---------------------------------------------------------------------------
def training_records(consolidated):
    """Manifest-shaped records for partitioning: **all reviewed docs**.

    Both ``hits_reviewed`` and ``no_hits_reviewed`` belong — a confirmed no-hit is a
    valid zero-entity training example. Unreviewed docs are excluded (not yet trusted).
    Each record is ``{"file", "text", "entities"}`` — exactly what
    ``training_prep.partition.partition_document`` consumes.
    """
    out = []
    for rec in consolidated:
        if not rec["reviewed"]:
            continue
        out.append({
            "file": rec["id"],
            "text": rec["text"],
            "entities": list(rec["entities"] or []),
        })
    return out


# ---------------------------------------------------------------------------
# Convenience: run the whole thing from already-parsed inputs
# ---------------------------------------------------------------------------
def consolidate(input_batch, annotation_sources):
    """End-to-end (pure): base → merge → buckets/summary/training.

    Returns a dict with ``consolidated``, ``buckets``, ``summary`` and
    ``training`` — ready for the notebook to serialize and partition.
    """
    base = base_records(input_batch)
    consolidated, conflicts = merge_annotations(base, annotation_sources)
    return {
        "consolidated": consolidated,
        "buckets": bucketize(consolidated),
        "summary": summarize(consolidated, conflicts),
        "training": training_records(consolidated),
    }
