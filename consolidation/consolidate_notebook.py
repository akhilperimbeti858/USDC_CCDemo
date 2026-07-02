# Databricks notebook source
# MAGIC %md
# MAGIC # NER consolidation — merge reviewer exports, compute categories, build the training set
# MAGIC
# MAGIC Runs **after** reviewers finish (or partway through) the local annotator. It reads a
# MAGIC per-batch folder, merges every reviewer's export into **one status record per document**,
# MAGIC computes the four categories (no folder juggling), and partitions the reviewed docs into
# MAGIC ≤5000-byte training docs.
# MAGIC
# MAGIC ```
# MAGIC  ner/batch_<ID>/
# MAGIC    input/        batch_<ID>.json                 # Comprehend-derived (all docs; has initialEntities)
# MAGIC    annotations/  batch_<ID>.<annotatorID>.json   # one export per reviewer (partial or full)
# MAGIC    consolidated/ consolidated.json               # merged, one record/doc (rebuildable, idempotent)
# MAGIC                  summary.json                     # counts per bucket + review progress + conflicts
# MAGIC                  views/{hits_reviewed,no_hits_reviewed,hits_unreviewed,no_hits_unreviewed}.json
# MAGIC    training/     docs/<file>.txt + annotations.csv   # from training_prep/partition.py
# MAGIC ```
# MAGIC
# MAGIC **Categories are computed, never stored as a location** (see `consolidation/consolidate.py`):
# MAGIC `comprehend_hit` = the input seeded entities; `reviewed` = a reviewer marked it done;
# MAGIC `final_hit` = reviewed AND the final entity list is non-empty. Re-runnable any time as
# MAGIC more reviewer files land — reviewers only ever add files under `annotations/`.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - This repo is on Databricks Repos so we can import `consolidation/consolidate.py` and
# MAGIC   `training_prep/partition.py`.
# MAGIC - The batch folder (below) is reachable as a filesystem path — e.g. a DBFS path you sync
# MAGIC   SharePoint into (`/dbfs/...`), or a mounted store. Set the widgets in the next cell.

# COMMAND ----------
# MAGIC %md ## Config (widgets)

# COMMAND ----------
import os

_defaults = {
    "batch_id": "",                       # e.g. 7  (folder is <root>/batch_<batch_id>)
    "root": "/dbfs/tmp/ner",              # parent of batch_<ID>/ (DBFS or a mount)
    "max_doc_bytes": "5000",
    "repo_root": "/Workspace/Repos/your.name/USDC_CCDemo",  # to import the two modules
    "push_training_s3": "",               # optional s3://bucket/prefix/ to also upload training/
    "aws_region": "us-east-1",
}
for k, v in _defaults.items():
    try:
        dbutils.widgets.text(k, v)
    except Exception:
        pass

def cfg(name):
    try:
        return dbutils.widgets.get(name)
    except Exception:
        return _defaults[name]

BATCH_ID = cfg("batch_id").strip()
assert BATCH_ID, "Set the batch_id widget."
BATCH_DIR = f'{cfg("root").rstrip("/")}/batch_{BATCH_ID}'
INPUT_DIR = f"{BATCH_DIR}/input"
ANN_DIR = f"{BATCH_DIR}/annotations"
CONS_DIR = f"{BATCH_DIR}/consolidated"
VIEWS_DIR = f"{CONS_DIR}/views"
TRAIN_DIR = f"{BATCH_DIR}/training"
for d in (CONS_DIR, VIEWS_DIR, TRAIN_DIR, f"{TRAIN_DIR}/docs"):
    os.makedirs(d, exist_ok=True)
print("batch dir:", BATCH_DIR)

# COMMAND ----------
# MAGIC %md ## Imports (repo modules)

# COMMAND ----------
import glob
import json
import sys

sys.path.append(os.path.join(cfg("repo_root"), "consolidation"))
sys.path.append(os.path.join(cfg("repo_root"), "training_prep"))
try:
    import consolidate as C
    from partition import partition_document, part_filenames
    print("Imported consolidation.consolidate and training_prep.partition.")
except Exception as e:
    raise RuntimeError(
        "Could not import repo modules. Set 'repo_root' to this repo's Databricks Repos path. "
        f"Original error: {e}"
    )

# COMMAND ----------
# MAGIC %md ## Load input batch + reviewer annotation files

# COMMAND ----------
input_path = f"{INPUT_DIR}/batch_{BATCH_ID}.json"
if not os.path.exists(input_path):
    # fall back to the only *.json in input/ if it wasn't named by convention
    candidates = sorted(glob.glob(f"{INPUT_DIR}/*.json"))
    assert candidates, f"No input batch found under {INPUT_DIR}/"
    input_path = candidates[0]
input_batch = json.load(open(input_path))
print("input batch:", input_path, "→", len(input_batch.get("documents", [])), "docs")

# One entry per reviewer export. mtime orders them (latest review wins); the annotatorID
# is recovered from the filename (batch_<ID>.<annotatorID>.json) as a fallback.
annotation_sources = []
for path in sorted(glob.glob(f"{ANN_DIR}/*.json")):
    base = os.path.basename(path)
    stem = base[:-5] if base.lower().endswith(".json") else base
    annotator = stem.split(".", 1)[1] if "." in stem else stem   # batch_7.alice -> alice
    obj = json.load(open(path))
    annotation_sources.append({
        "source": base,
        "mtime": os.path.getmtime(path),
        "annotatorID": annotator,
        "documents": obj if isinstance(obj, list) else obj.get("documents", []),
    })
print("reviewer files:", [s["source"] for s in annotation_sources])

# COMMAND ----------
# MAGIC %md ## Consolidate (merge → compute categories)

# COMMAND ----------
result = C.consolidate(input_batch, annotation_sources)

# consolidated.json — one status record per document (rebuildable, idempotent)
with open(f"{CONS_DIR}/consolidated.json", "w") as f:
    json.dump({"batch_id": BATCH_ID, "documents": result["consolidated"]}, f, indent=2)

# summary.json — counts per bucket + review progress + conflicts
with open(f"{CONS_DIR}/summary.json", "w") as f:
    json.dump(result["summary"], f, indent=2)

# views/*.json — convenience slices (derived; safe to delete/regenerate)
for bucket, recs in result["buckets"].items():
    with open(f"{VIEWS_DIR}/{bucket}.json", "w") as f:
        json.dump({"batch_id": BATCH_ID, "category": bucket, "documents": recs}, f, indent=2)

s = result["summary"]
print(f"Reviewed {s['reviewed']}/{s['total']}  |  counts: {s['counts']}")
if s["conflicts"]:
    print("CONFLICTS (reviewed by >1 annotator; latest mtime won):")
    for c in s["conflicts"]:
        print(f"  {c['id']}: {c['annotators']} -> {c['resolved']}")

# COMMAND ----------
# MAGIC %md ## Partition the reviewed docs → training set
# MAGIC The training set is **every reviewed document** — hits and confirmed no-hits both
# MAGIC belong (a confirmed no-hit is a valid zero-entity example). Unreviewed docs are skipped.

# COMMAND ----------
MAX = int(cfg("max_doc_bytes"))
training = result["training"]

# Comprehend training annotations CSV: File, Line, Begin Offset, End Offset, Type
csv_rows = ["File,Line,Begin Offset,End Offset,Type"]
n_parts = n_split = 0
for d in training:
    parts = partition_document(d["text"], d.get("entities", []), MAX)
    names = part_filenames(d["file"], len(parts))
    if len(parts) > 1:
        n_split += 1
    for name, part in zip(names, parts):
        with open(f"{TRAIN_DIR}/docs/{name}", "w") as f:
            f.write(part["text"])
        for e in part["entities"]:
            csv_rows.append(f'{name},0,{e["startOffset"]},{e["endOffset"]},{e["label"]}')
        n_parts += 1

annotations_csv = "\n".join(csv_rows) + "\n"
with open(f"{TRAIN_DIR}/annotations.csv", "w") as f:
    f.write(annotations_csv)

print(f"Training set: {len(training)} reviewed docs → {n_parts} parts "
      f"({n_split} were split). Wrote {TRAIN_DIR}/docs/ + annotations.csv")

# COMMAND ----------
# MAGIC %md ## (optional) push the training set to S3 for Comprehend training

# COMMAND ----------
dst = cfg("push_training_s3").strip()
if dst:
    import boto3
    from urllib.parse import urlparse
    p = urlparse(dst)
    bkt, pfx = p.netloc, p.path.lstrip("/").rstrip("/")
    pfx = f"{pfx}/batch_{BATCH_ID}" if pfx else f"batch_{BATCH_ID}"
    s3 = boto3.client("s3", region_name=cfg("aws_region"))
    for path in glob.glob(f"{TRAIN_DIR}/docs/*.txt"):
        s3.put_object(Bucket=bkt, Key=f"{pfx}/docs/{os.path.basename(path)}",
                      Body=open(path, "rb").read())
    s3.put_object(Bucket=bkt, Key=f"{pfx}/annotations.csv",
                  Body=annotations_csv.encode("utf-8"), ContentType="text/csv")
    print(f"Uploaded training set → s3://{bkt}/{pfx}/")
else:
    print("push_training_s3 is blank — training set kept locally only.")
