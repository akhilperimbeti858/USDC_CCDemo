# Databricks notebook source
# MAGIC %md
# MAGIC # NER labeling pipeline — off-AWS orchestration (Databricks)
# MAGIC
# MAGIC Drives the whole loop from a notebook. The **only** AWS service used is **Comprehend**
# MAGIC (custom entity recognizer) — no Cognito, no Ground Truth, no SageMaker.
# MAGIC
# MAGIC ```
# MAGIC  Phase 0  pull a batch from ecata.sentences
# MAGIC  Phase 1  run Comprehend recognizer (v7) -> output.tar.gz
# MAGIC  Phase 2  parse -> BATCH JSON                      ── hand to reviewer (SharePoint) ──┐
# MAGIC  Phase 3  human review in local_annotator/annotator.html  (OUTSIDE this notebook)    │
# MAGIC  Phase 4  consume annotated_batch.json -> partition to <=5000-byte training docs  ◄──┘
# MAGIC  Phase 5  (optional) train a new recognizer (v8)
# MAGIC ```
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - The cluster has AWS access to Comprehend + S3 (instance profile, or keys in the env).
# MAGIC - A trained recognizer ARN (v7) and a Comprehend **DataAccessRole** ARN.
# MAGIC - This repo is available (Databricks Repos) so we can import `training_prep/partition.py`.
# MAGIC - Set the widgets in the next cell.

# COMMAND ----------
# MAGIC %md ## Config (widgets)

# COMMAND ----------
import time

# Define widgets once; safe to re-run.
_defaults = {
    "source_table": "ecata.sentences",
    "id_col": "",                 # column to use as the doc id; blank -> row index
    "text_col": "text",           # column holding the sentence/document text
    "batch_size": "200",
    "batch_id": time.strftime("%Y%m%d-%H%M%S"),
    "aws_region": "us-east-1",
    "recognizer_arn": "",         # arn:aws:comprehend:...:entity-recognizer/...-v7
    "data_access_role_arn": "",   # Comprehend DataAccessRole (reads input, writes output in S3)
    "comprehend_input_s3": "",    # s3://bucket/prefix/  (we upload the batch docs here)
    "comprehend_output_s3": "",   # s3://bucket/prefix/  (Comprehend writes output.tar.gz here)
    "training_s3": "",            # s3://bucket/prefix/  (Phase 4 writes training docs + annotations)
    "work_dir": "/dbfs/tmp/ner",  # local (DBFS) working dir for batch/annotated/training files
    "allowed_labels": "OFAC_ORG,OFAC_POI,FTO",
    "min_score": "",              # blank -> keep all
    "max_doc_bytes": "5000",
    "repo_root": "/Workspace/Repos/your.name/USDC_CCDemo",  # to import training_prep
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

BATCH_ID = cfg("batch_id")
WORK = f'{cfg("work_dir").rstrip("/")}/{BATCH_ID}'
import os
os.makedirs(WORK, exist_ok=True)
print("batch_id:", BATCH_ID, "| work dir:", WORK)

# COMMAND ----------
# MAGIC %md ## Setup (clients + helpers)

# COMMAND ----------
import io
import json
import tarfile
from urllib.parse import urlparse

import boto3

REGION = cfg("aws_region")
s3 = boto3.client("s3", region_name=REGION)
comprehend = boto3.client("comprehend", region_name=REGION)

def _split_s3(uri):
    p = urlparse(uri)
    return p.netloc, p.path.lstrip("/")

def allowed_labels():
    return [x.strip() for x in cfg("allowed_labels").split(",") if x.strip()]

def min_score():
    raw = cfg("min_score").strip()
    return float(raw) if raw else None

# --- Comprehend doc -> annotator record (mirror of comprehend_to_manifest) ---
def doc_to_record(doc_id, text, entities):
    allowed = set(allowed_labels())
    ms = min_score()
    initial, meta = [], []
    for ent in (entities or []):
        if ent.get("Type") not in allowed:
            continue
        if ms is not None and ent.get("Score", 1.0) < ms:
            continue
        a, b = ent["BeginOffset"], ent["EndOffset"]
        initial.append({"startOffset": a, "endOffset": b, "label": ent["Type"]})
        meta.append({"startOffset": a, "endOffset": b, "confidence": ent.get("Score"), "ofacID": "FILL"})
    # humanReviewRequired starts true (unseen); the annotator flips it to false when done.
    return {"id": doc_id, "text": text, "humanReviewRequired": True,
            "initialEntities": initial, "metaData": meta}

# --- partition functions from the repo (training_prep/partition.py) ---
import sys
sys.path.append(os.path.join(cfg("repo_root"), "training_prep"))
try:
    from partition import partition_document, part_filenames
    print("Imported partition_document / part_filenames from training_prep.")
except Exception as e:
    raise RuntimeError(
        "Could not import training_prep/partition.py. Set the 'repo_root' widget to this "
        f"repo's path in Databricks Repos. Original error: {e}"
    )

# COMMAND ----------
# MAGIC %md ## Phase 0 — pull a batch from `ecata.sentences`

# COMMAND ----------
id_col, text_col = cfg("id_col"), cfg("text_col")
n = int(cfg("batch_size"))

sdf = spark.table(cfg("source_table"))
cols = [text_col] + ([id_col] if id_col else [])
rows = sdf.select(*cols).limit(n).collect()

docs = []
for i, r in enumerate(rows):
    raw_id = (r[id_col] if id_col else i)
    doc_id = f"training_doc_{raw_id}.txt"
    text = (r[text_col] or "").replace("\r\n", "\n")
    docs.append({"id": doc_id, "text": text})

print(f"Pulled {len(docs)} documents from {cfg('source_table')}.")
docs[:2]

# COMMAND ----------
# MAGIC %md ## Phase 1 — run Comprehend recognizer (v7)
# MAGIC Upload the batch as **one document per line** to S3, start an async
# MAGIC `StartEntitiesDetectionJob`, and poll until it completes.

# COMMAND ----------
in_bucket, in_prefix = _split_s3(cfg("comprehend_input_s3"))
out_bucket, out_prefix = _split_s3(cfg("comprehend_output_s3"))

# One doc per line; line index i corresponds to docs[i]. Newlines inside a doc are
# flattened so each document stays on a single line (ONE_DOC_PER_LINE requirement).
input_key = f'{in_prefix.rstrip("/")}/{BATCH_ID}/input.txt'
payload = "\n".join(d["text"].replace("\n", " ") for d in docs) + "\n"
s3.put_object(Bucket=in_bucket, Key=input_key, Body=payload.encode("utf-8"))
print(f"Uploaded input -> s3://{in_bucket}/{input_key}")

job = comprehend.start_entities_detection_job(
    JobName=f"ner-{BATCH_ID}",
    EntityRecognizerArn=cfg("recognizer_arn"),
    InputDataConfig={"S3Uri": f"s3://{in_bucket}/{input_key}", "InputFormat": "ONE_DOC_PER_LINE"},
    OutputDataConfig={"S3Uri": f's3://{out_bucket}/{out_prefix.rstrip("/")}/{BATCH_ID}/'},
    DataAccessRoleArn=cfg("data_access_role_arn"),
    LanguageCode="en",
)
job_id = job["JobId"]
print("Started job:", job_id)

# COMMAND ----------
# Poll until the job finishes.
while True:
    desc = comprehend.describe_entities_detection_job(JobId=job_id)["EntitiesDetectionJobProperties"]
    status = desc["JobStatus"]
    print(time.strftime("%H:%M:%S"), status)
    if status in ("COMPLETED", "FAILED", "STOP_REQUESTED", "STOPPED"):
        break
    time.sleep(30)

assert status == "COMPLETED", f"Comprehend job did not complete: {status} ({desc.get('Message')})"
output_uri = desc["OutputDataConfig"]["S3Uri"]   # .../output.tar.gz
print("Output:", output_uri)

# COMMAND ----------
# MAGIC %md ## Phase 2 — parse `output.tar.gz` → BATCH JSON

# COMMAND ----------
ob, ok = _split_s3(output_uri)
tar_bytes = s3.get_object(Bucket=ob, Key=ok)["Body"].read()

records_by_line = {}
with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
    for m in tar.getmembers():
        if not m.isfile():
            continue
        for line in tar.extractfile(m).read().decode("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            records_by_line[int(rec.get("Line", len(records_by_line)))] = rec

batch_docs = []
for i, d in enumerate(docs):
    ents = records_by_line.get(i, {}).get("Entities", [])
    batch_docs.append(doc_to_record(d["id"], d["text"], ents))

batch_json = {"labels": allowed_labels(), "documents": batch_docs}
batch_path = f"{WORK}/batch.json"
with open(batch_path, "w") as f:
    json.dump(batch_json, f, indent=2)

seeded = sum(len(b["initialEntities"]) for b in batch_docs)
print(f"BATCH JSON: {len(batch_docs)} docs, {seeded} seeded entities -> {batch_path}")

# COMMAND ----------
# MAGIC %md
# MAGIC ### Phase 3 — human review (OUTSIDE this notebook)
# MAGIC 1. Download **`batch.json`** from `WORK` (or copy it to SharePoint).
# MAGIC 2. Open **`local_annotator/annotator.html`** in a browser; **Load batch JSON** (+ the OFAC list).
# MAGIC 3. Highlight/confirm entities, set OFAC IDs, **Export** → `annotated_batch.json`.
# MAGIC 4. Put `annotated_batch.json` back where this notebook can read it (set `annotated_path` below).

# COMMAND ----------
# MAGIC %md ## Phase 4 — consume the annotated export → partition to ≤5000-byte training docs

# COMMAND ----------
annotated_path = f"{WORK}/annotated_batch.json"   # <- point at the reviewer's export
assert os.path.exists(annotated_path), f"Place the reviewer's export at {annotated_path} first."

annotated = json.load(open(annotated_path))
MAX = int(cfg("max_doc_bytes"))
tr_bucket, tr_prefix = _split_s3(cfg("training_s3"))
tr_prefix = f'{tr_prefix.rstrip("/")}/{BATCH_ID}'

# The annotator export includes `text`; if an older export omits it, fall back to the
# original batch.json (keyed by file/id) so partitioning always has the document text.
batch_lookup = {}
if os.path.exists(batch_path):
    for bd in json.load(open(batch_path)).get("documents", []):
        batch_lookup[bd.get("id") or bd.get("file")] = bd.get("text", "")

def _doc_text(d):
    t = d.get("text")
    if t is None:
        t = batch_lookup.get(d.get("file") or d.get("id"))
    if t is None:
        raise KeyError(f'No text for {d.get("file") or d.get("id")}: include "text" in the '
                       f'export or keep batch.json at {batch_path}.')
    return t

# Comprehend training annotations CSV: File, Line, Begin Offset, End Offset, Type
csv_rows = ["File,Line,Begin Offset,End Offset,Type"]
n_parts = n_split = 0
for d in annotated["documents"]:
    parts = partition_document(_doc_text(d), d.get("entities", []), MAX)
    names = part_filenames(d.get("file") or d.get("id"), len(parts))
    if len(parts) > 1:
        n_split += 1
    for name, part in zip(names, parts):
        s3.put_object(Bucket=tr_bucket, Key=f"{tr_prefix}/docs/{name}",
                      Body=part["text"].encode("utf-8"))
        for e in part["entities"]:
            csv_rows.append(f'{name},0,{e["startOffset"]},{e["endOffset"]},{e["label"]}')
        n_parts += 1

annotations_csv = "\n".join(csv_rows) + "\n"
s3.put_object(Bucket=tr_bucket, Key=f"{tr_prefix}/annotations.csv",
              Body=annotations_csv.encode("utf-8"))
# also keep a local copy
with open(f"{WORK}/annotations.csv", "w") as f:
    f.write(annotations_csv)

print(f"Wrote {n_parts} training docs ({n_split} were split) + annotations.csv "
      f"-> s3://{tr_bucket}/{tr_prefix}/")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Phase 5 — (optional) train a new recognizer (v8)
# MAGIC Guarded by the `DO_TRAIN` flag below so it never runs by accident. Requires a
# MAGIC `DataAccessRole` and an entity list matching the labels used above.

# COMMAND ----------
DO_TRAIN = False  # flip to True to kick off training

if DO_TRAIN:
    resp = comprehend.create_entity_recognizer(
        RecognizerName=f"ner-recognizer-{BATCH_ID}",
        LanguageCode="en",
        DataAccessRoleArn=cfg("data_access_role_arn"),
        InputDataConfig={
            "EntityTypes": [{"Type": t} for t in allowed_labels()],
            "Documents": {"S3Uri": f"s3://{tr_bucket}/{tr_prefix}/docs/"},
            "Annotations": {"S3Uri": f"s3://{tr_bucket}/{tr_prefix}/annotations.csv"},
        },
    )
    print("Training started:", resp["EntityRecognizerArn"])
else:
    print("DO_TRAIN is False — skipping training. Set it True to create recognizer v8.")
