"""Run consolidation over an on-disk batch folder — the same logic as the notebook, offline.

Usage:
    python consolidation/run_local.py <root> <batch_id>
    python consolidation/run_local.py consolidation/sample 7      # uses the bundled sample

Reads  <root>/batch_<ID>/{input,annotations}/  and writes
       <root>/batch_<ID>/{consolidated/{consolidated,summary,views/*},training/{docs,annotations.csv}}.
Useful for a quick local pass before running the Databricks notebook.
"""

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "training_prep"))

import consolidate as C
from partition import partition_document, part_filenames


def run(root, batch_id, max_bytes=5000):
    batch_dir = os.path.join(root, f"batch_{batch_id}")
    input_dir = os.path.join(batch_dir, "input")
    ann_dir = os.path.join(batch_dir, "annotations")
    cons_dir = os.path.join(batch_dir, "consolidated")
    views_dir = os.path.join(cons_dir, "views")
    train_dir = os.path.join(batch_dir, "training")
    for d in (cons_dir, views_dir, os.path.join(train_dir, "docs")):
        os.makedirs(d, exist_ok=True)

    input_path = os.path.join(input_dir, f"batch_{batch_id}.json")
    if not os.path.exists(input_path):
        cands = sorted(glob.glob(os.path.join(input_dir, "*.json")))
        if not cands:
            raise SystemExit(f"No input batch under {input_dir}/")
        input_path = cands[0]
    input_batch = json.load(open(input_path))

    sources = []
    for path in sorted(glob.glob(os.path.join(ann_dir, "*.json"))):
        base = os.path.basename(path)
        stem = base[:-5] if base.lower().endswith(".json") else base
        annotator = stem.split(".", 1)[1] if "." in stem else stem
        obj = json.load(open(path))
        sources.append({
            "source": base,
            "mtime": os.path.getmtime(path),
            "annotatorID": annotator,
            "documents": obj if isinstance(obj, list) else obj.get("documents", []),
        })

    result = C.consolidate(input_batch, sources)

    with open(os.path.join(cons_dir, "consolidated.json"), "w") as f:
        json.dump({"batch_id": batch_id, "documents": result["consolidated"]}, f, indent=2)
    with open(os.path.join(cons_dir, "summary.json"), "w") as f:
        json.dump(result["summary"], f, indent=2)
    for bucket, recs in result["buckets"].items():
        with open(os.path.join(views_dir, f"{bucket}.json"), "w") as f:
            json.dump({"batch_id": batch_id, "category": bucket, "documents": recs}, f, indent=2)

    csv_rows = ["File,Line,Begin Offset,End Offset,Type"]
    n_parts = 0
    for d in result["training"]:
        parts = partition_document(d["text"], d.get("entities", []), max_bytes)
        for name, part in zip(part_filenames(d["file"], len(parts)), parts):
            with open(os.path.join(train_dir, "docs", name), "w") as f:
                f.write(part["text"])
            for e in part["entities"]:
                csv_rows.append(f'{name},0,{e["startOffset"]},{e["endOffset"]},{e["label"]}')
            n_parts += 1
    with open(os.path.join(train_dir, "annotations.csv"), "w") as f:
        f.write("\n".join(csv_rows) + "\n")

    s = result["summary"]
    print(f"batch_{batch_id}: reviewed {s['reviewed']}/{s['total']}  counts={s['counts']}")
    if s["conflicts"]:
        for c in s["conflicts"]:
            print(f"  conflict {c['id']}: {c['annotators']} -> {c['resolved']}")
    print(f"  training: {len(result['training'])} reviewed docs -> {n_parts} parts")
    print(f"  wrote -> {cons_dir}/ and {train_dir}/")
    return result


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "sample")
    batch_id = sys.argv[2] if len(sys.argv) > 2 else "7"
    run(root, batch_id)
