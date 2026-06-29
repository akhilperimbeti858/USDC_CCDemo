# training_prep — split labeled docs into ≤5000-byte training parts

`partition.py` is the **post-annotation** step of the off-AWS flow. After a reviewer
finishes in the [local annotator](../local_annotator), this code takes the labeled
documents and splits any that exceed Amazon Comprehend's **per-document byte limit**
(default 5000) into **balanced** parts, **rebasing every entity's offsets** to its part
and renaming files `training_doc_X.txt` → `training_doc_X_part_1.txt`, `_part_2.txt`, …

It is plain Python — usable two ways:
- **As a Databricks/notebook module** (the off-AWS path): import the pure functions and
  feed them the annotator's exported documents. No AWS needed.
- **As an AWS Lambda** (`lambda_handler`, optional): reads a JSON-Lines manifest from S3
  and writes parts back to S3. Kept for completeness; not used in the Databricks flow.

## Pure functions (no I/O — use these from a notebook)
- `partition_document(text, entities, max_bytes)` → `[{"text": <part>, "entities": [...]}, …]`
  - Part count = `ceil(byte_len(text) / max_bytes)` (balanced: 9,000 → 2×~4,500).
  - Splits land on **whitespace** and **never cut an entity span**; offsets are rebased
    to each part. A single entity larger than `max_bytes` can't be split (over-limit part).
- `part_filenames(file_name, n)` → `["<stem>.txt"]` when `n==1`, else
  `["<stem>_part_1.txt", …, "<stem>_part_n.txt"]`.

## Example (Databricks)
```python
from partition import partition_document, part_filenames
import json

annotated = json.load(open("annotated_batch.json"))   # exported by the annotator
out_docs, out_annotations = [], []
for d in annotated["documents"]:
    parts = partition_document(d["text"], d["entities"], 5000)
    names = part_filenames(d["file"], len(parts))
    for name, part in zip(names, parts):
        out_docs.append((name, part["text"]))                 # write to DBFS / SharePoint
        out_annotations.append({"file": name, "entities": part["entities"]})
# out_docs -> training_doc_*_part_N.txt ; out_annotations -> Comprehend training annotations
```

> Note: the annotator export carries `entities` as character offsets; the limit is measured
> in **UTF-8 bytes**. For ASCII text byte≈char, so they coincide; for multibyte text the
> split still respects the byte limit while keeping entity offsets character-based.

## Where it sits in the full flow
```
ecata.sentences ─► Comprehend recognizer v7 ─► output.tar.gz ─► BATCH JSON
                                                                    │
                                            local_annotator/annotator.html (human review)
                                                                    │
                                                          ANNOTATED JSON (export)
                                                                    │
                                          training_prep/partition.py  ◄── you are here
                                                                    │
                              training_doc_*_part_N.txt + rebased annotations
                                                                    │
                                          retrain Comprehend recognizer (v8)
```
