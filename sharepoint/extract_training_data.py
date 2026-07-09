"""Parsing step: annotated JSON (full OR partial) -> Comprehend training data.

WHAT
----
Point this at a single annotator export (e.g. ``Batch_3.json``) or a whole
``annotations/`` folder. It pulls **only the documents that have been reviewed**
(``humanReviewRequired == false``) and partitions them into ≤5000-byte training docs
plus an ``annotations.csv`` — reusing ``training_prep/partition.py``. Documents still
awaiting review (``true``) are skipped, so a **partially** annotated file contributes its
finished docs and nothing else. Re-run it any time as more docs get reviewed.

WHY IT'S SEPARATE FROM consolidation/
-------------------------------------
This is the **quick path**: annotated files → training data, no Comprehend input batch
required, no category computation. Use ``consolidation/`` instead when you also want the
hit/no-hit × reviewed/unreviewed categories, the multi-reviewer merge summary, and
conflict reporting. Both reuse the same ``partition.py``, so training output is identical.

USAGE
-----
    python sharepoint/extract_training_data.py <path> [<out_dir>] [--max-bytes 5000]

    # a single file, or a folder of exports (SharePoint annotations/):
    python sharepoint/extract_training_data.py .../ner/batch_3/annotations
    python sharepoint/extract_training_data.py .../Batch_3.json .../batch_3/training

Writes ``<out_dir>/docs/<file>.txt`` (each part) + ``<out_dir>/annotations.csv``
(``File,Line,Begin Offset,End Offset,Type``). Default ``<out_dir>`` is ``./training``.
"""

import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "training_prep"))
from partition import partition_document, part_filenames


# ---------------------------------------------------------------------------
# Pure logic (no I/O) — easy to unit test
# ---------------------------------------------------------------------------
def _doc_id(d):
    for key in ("file", "File", "id"):
        if d.get(key):
            return str(d[key]).rsplit("/", 1)[-1]
    return None


def _is_reviewed(d):
    """True only when the reviewer explicitly marked the doc done (either casing)."""
    for key in ("humanReviewRequired", "HumanReviewRequired"):
        if key in d and d[key] is not None:
            return not bool(d[key])
    return False   # missing/unknown -> treat as NOT reviewed (don't train on it)


def reviewed_docs(*annotated_objects):
    """Reviewed docs across one or more parsed exports, as ``{file, text, entities}``.

    De-duplicated by document id (**last occurrence wins**) so the same doc appearing in
    two reviewer files doesn't produce duplicate training docs. For proper multi-reviewer
    merge (latest-mtime-wins + conflict reporting) use ``consolidation/`` instead.
    """
    picked = {}
    for obj in annotated_objects:
        docs = obj if isinstance(obj, list) else obj.get("documents", [])
        for d in docs:
            if not _is_reviewed(d):
                continue
            did = _doc_id(d)
            if did is None:
                continue
            picked[did] = {
                "file": did,
                "text": d.get("text") or d.get("source") or "",
                "entities": list(d.get("entities") or []),
            }
    return list(picked.values())


def to_training(reviewed, max_bytes=5000):
    """Partition reviewed docs → ``(files, csv_rows)``.

    ``files`` is ``[(name, text), ...]`` (one per ≤max_bytes part); ``csv_rows`` is the
    Comprehend annotations CSV including the header.
    """
    files, csv_rows = [], ["File,Line,Begin Offset,End Offset,Type"]
    for d in reviewed:
        parts = partition_document(d["text"], d.get("entities", []), max_bytes)
        for name, part in zip(part_filenames(d["file"], len(parts)), parts):
            files.append((name, part["text"]))
            for e in part["entities"]:
                csv_rows.append(f'{name},0,{e["startOffset"]},{e["endOffset"]},{e["label"]}')
    return files, csv_rows


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def _load_paths(path):
    """A file, or every ``*.json`` in a folder → list of parsed objects."""
    if os.path.isdir(path):
        paths = sorted(glob.glob(os.path.join(path, "*.json")))
    else:
        paths = [path]
    if not paths:
        raise SystemExit(f"No annotated JSON found at {path}")
    return [json.load(open(p)) for p in paths], paths


def run(path, out_dir="training", max_bytes=5000):
    objs, paths = _load_paths(path)
    reviewed = reviewed_docs(*objs)
    files, csv_rows = to_training(reviewed, max_bytes)

    os.makedirs(os.path.join(out_dir, "docs"), exist_ok=True)
    for name, text in files:
        with open(os.path.join(out_dir, "docs", name), "w") as f:
            f.write(text)
    with open(os.path.join(out_dir, "annotations.csv"), "w") as f:
        f.write("\n".join(csv_rows) + "\n")

    total_docs = sum(len(o if isinstance(o, list) else o.get("documents", [])) for o in objs)
    print(f"Read {len(paths)} file(s), {total_docs} docs; "
          f"{len(reviewed)} reviewed → {len(files)} training part(s).")
    print(f"Wrote {out_dir}/docs/ + {out_dir}/annotations.csv")
    return reviewed, files


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    max_bytes = 5000
    for a in sys.argv[1:]:
        if a.startswith("--max-bytes"):
            max_bytes = int(a.split("=", 1)[1]) if "=" in a else int(sys.argv[sys.argv.index(a) + 1])
    if not args:
        raise SystemExit(__doc__)
    run(args[0], args[1] if len(args) > 1 else "training", max_bytes)
