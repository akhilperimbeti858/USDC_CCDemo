# databricks — pipeline orchestration notebook

`ner_pipeline_notebook.py` is a **Databricks notebook** (source format) that drives the
off-AWS NER labeling loop. The only AWS service it touches is **Comprehend**.

## Import into Databricks
- With **Databricks Repos**, clone this repo; the notebook appears at
  `…/USDC_CCDemo/databricks/ner_pipeline_notebook.py`. (It also lets the notebook import
  `training_prep/partition.py` — set the `repo_root` widget to the repo path.)
- Or **Workspace → Import** the `.py` file (it has the `# Databricks notebook source`
  header and `# COMMAND ----------` cell markers).

## What it does (cell by cell)
| Phase | Cell | Action |
|------|------|--------|
| 0 | pull | `spark.table("ecata.sentences").limit(batch_size)` → `[{id, text}]` (filename `training_doc_<id>.txt`) |
| 1 | Comprehend | upload batch as ONE_DOC_PER_LINE to S3 → `StartEntitiesDetectionJob` (recognizer **v7**) → poll to COMPLETED |
| 2 | parse | download/parse `output.tar.gz` → **BATCH JSON** (`initialEntities` + `metaData{confidence, ofacID:"FILL"}`) → `WORK/batch.json` |
| 3 | review | **manual** — open `../local_annotator/annotator.html`, load `batch.json`, export `annotated_batch.json` |
| 4 | partition | read the export → `partition_document(...)` (≤5000-byte balanced parts, rebased offsets, `_part_N.txt` naming) → write training docs + `annotations.csv` to S3 |
| 5 | train | **optional** (guarded by `DO_TRAIN`) — `create_entity_recognizer` → v8 |

## Prerequisites
- Cluster AWS access to **Comprehend + S3** (instance profile or env creds).
- A trained recognizer ARN (**v7**) and a Comprehend **DataAccessRole** ARN.
- Set the widgets: `source_table`, `id_col`/`text_col`, `recognizer_arn`,
  `data_access_role_arn`, `comprehend_input_s3`, `comprehend_output_s3`, `training_s3`,
  `repo_root`, plus `allowed_labels` / `min_score` / `max_doc_bytes`.

## Notes
- **Schema continuity:** the BATCH JSON it emits and the `annotated_batch.json` it consumes
  are exactly the [annotator](../local_annotator) contracts; the partition step is
  [`training_prep/partition.py`](../training_prep). No translation anywhere.
- Phase 3 is intentionally manual (the reviewer step). SharePoint is fine as the drop point
  for `batch.json` / `annotated_batch.json`; automating that transfer is out of scope here.
- Training output is Comprehend's **CSV annotations** format (`File,Line,Begin Offset,End
  Offset,Type`) with each part as its own document (Line 0).
