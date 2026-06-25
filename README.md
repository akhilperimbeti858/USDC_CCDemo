# Custom NER Labeling on SageMaker Ground Truth (Terraform + Python/boto3)

Infrastructure-as-code and a locally-testable Python implementation for running a
**custom Named Entity Recognition (NER)** labeling job on **Amazon SageMaker
Ground Truth**, using a **custom Crowd-HTML UI** with OFAC sanctions-screening
metadata, a **private workteam**, and **pre/post-annotation Lambda** functions.

There are two ways to run the same workflow:

1. **`terraform/`** — provisions all AWS infrastructure **and launches the labeling
   job** (`terraform apply`).
2. **`python_boto3/`** — the same logic in Python, built for **local testing/
   simulation** plus a boto3 job launcher.

---

## Table of contents

- [Architecture & data flow](#architecture--data-flow)
- [How custom labeling works](#how-custom-labeling-works)
- [The Lambda logic](#the-lambda-logic)
- [The custom UI template](#the-custom-ui-template)
- [Input manifest format](#input-manifest-format)
- [Repository layout](#repository-layout)
- [Option A — Terraform](#option-a--terraform-provision--launch)
- [Option B — Python/boto3](#option-b--pythonboto3-local-testing--launch)
- [Testing & verification](#testing--verification)
- [Prerequisites](#prerequisites)
- [Security notes](#security-notes)

---

## Architecture & data flow

Two entry points launch the **same** Ground Truth job against the same assets
(input manifest + UI template that **already exist in your S3 bucket**, two Lambdas):

- **`terraform/`** — provisions IAM + the 2 Lambdas, **references** the existing
  bucket/manifest/template (no uploads), **and** launches the job.
- **`python_boto3/`** — runs the pipeline **locally** (no AWS) for testing, or
  launches the job via boto3 against already-deployed infra.

### Runtime flow (what Ground Truth executes — identical for both)

```
input/input.manifest   (one JSON record per line)
        │  GT reads each record's dataObject
        ▼
┌─────────────────────────────────────────────┐
│ PRE-annotation Lambda    (per data object)   │  lambdas/pre_annotation/handler.py
│   text            ← source / source-ref      │
│   labels          ← record `labels` config/env
│   initialEntities ← `initialEntities`        │
│   metaData        ← `metaData`               │
│   → { taskInput, isHumanAnnotationRequired } │
└─────────────────────────────────────────────┘
        │ taskInput
        ▼
┌─────────────────────────────────────────────┐
│ Worker UI       ui/ner-template.liquid.html  │
│   highlight/label spans; add & remove freely │
│   click an entity's OFAC ID to set/edit it   │
│   hidden `metaData` field ← confidence+ofacID│
│   → submits { annotatedResult, metaData }    │
└─────────────────────────────────────────────┘
        │ all workers' annotations (S3 payload)
        ▼
┌─────────────────────────────────────────────┐
│ POST-annotation Lambda   (consolidation)     │
│   single-worker pass-through;                │  lambdas/post_annotation_single
│   emits entities + metaData (drops "FILL")   │
└─────────────────────────────────────────────┘
        │
        ▼
output/   output manifest — entities + metaData (confidence; ofacID where set)
```

**OFAC ID flow:** the manifest seeds each detected span with a `metaData` record
carrying its model `confidence` and a placeholder `ofacID` of `"FILL"`. In the UI
the annotator adds/removes spans freely and clicks any entity to enter/edit its
OFAC ID; the current `metaData` (confidence + entered IDs) rides back in a hidden
field. The post-Lambda emits a parallel `metaData` array, keeping each annotator-set
`ofacID` and **dropping any span still left as `"FILL"`**.

### Who does what

| Step                                | Terraform                          | Python local sim                 | Python launcher                     |
|-------------------------------------|------------------------------------|----------------------------------|-------------------------------------|
| Create IAM / deploy Lambdas         | ✅                                  | ❌                                | ❌ (assumes they exist)             |
| S3 bucket + manifest + template     | **referenced** (must pre-exist)    | reads local manifest             | **referenced** (must pre-exist)     |
| Build create-labeling-job request   | `create-labeling-job.json.tftpl`   | —                                | `build_create_labeling_job_request` |
| Launch the job                      | `null_resource` → AWS CLI          | ❌                                | `sagemaker.create_labeling_job`     |
| Run pre/post logic in a **real** job| deployed Lambdas                   | local `ner_pipeline/` (test only)| deployed Lambdas                    |

> The Python `ner_pipeline/*.py` functions **mirror** the Lambda handlers for
> offline testing; a real job always runs the deployed `lambdas/*/handler.py`.
> Terraform and the boto3 launcher build the same request and point Ground Truth at
> the same deployed Lambda ARNs.

---

## How custom labeling works

Ground Truth has no Terraform/CloudFormation resource for labeling jobs, so the
job is created via the AWS API:

- **Terraform** uses a `null_resource` + `local-exec` running
  `aws sagemaker create-labeling-job` (with a destroy-time `stop-labeling-job`).
- **Python** uses `boto3` `sagemaker.create_labeling_job`.

The Lambda function **names contain `SageMaker`** so the
`AmazonSageMakerGroundTruthExecution` managed policy on the job's execution role is
permitted to invoke them.

---

## The Lambda logic

### Pre-annotation Lambda — `lambdas/pre_annotation/handler.py`
Invoked **once per data object** before rendering. It:

1. Reads the document text from `dataObject.source` (inline) or fetches
   `dataObject.source-ref` from S3.
2. Resolves the entity **label set** from the record's `labels` config
   (falling back to the `ENTITY_LABELS` env var / built-in defaults).
3. Passes through the two **parallel** per-span arrays from the manifest record:
   the seed spans (`initialEntities`, default `[]`) and their `metaData` (default
   `[]`) carrying `confidence` + a placeholder `ofacID` of `"FILL"`.
4. Returns the `taskInput` the template binds to. The manifest keys
   `initialEntities`/`metaData` pass straight through under the same names (no
   legacy variants):

```json
{
  "taskInput": {
    "taskObject": "<document text>",
    "labels": [{ "label": "OFAC_ORG" }, { "label": "OFAC_POI" }, { "label": "FTO" }],
    "initialEntities": [
      { "startOffset": 0, "endOffset": 9, "label": "OFAC_ORG" }
    ],
    "metaData": [
      { "startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "FILL" }
    ]
  },
  "isHumanAnnotationRequired": "true"
}
```

### Post-annotation Lambda — single-worker consolidation
`lambdas/post_annotation_single/handler.py` reads the consolidation payload from S3
and emits `consolidatedAnnotation.content[labelAttributeName]` with two parallel
arrays: **`entities`** (the worker's spans) and **`metaData`**.

For each worker span it builds a `metaData` record `{startOffset, endOffset,
confidence[, ofacID]}`. `confidence` comes from the worker-submitted `metaData`
(falling back to the manifest seed). `ofacID` is included **only when the annotator
entered a real value** — a span left as the `"FILL"` placeholder has its `ofacID`
dropped. Spans the annotator added (no model score) carry `confidence: null`.

> A multi-worker merge/vote consolidator is intentionally out of scope for now; the
> workflow runs with one worker per object.

> **Annotator-entered OFAC IDs reach the output.** The UI submits the current
> `metaData` (seed confidence + any entered/edited `ofacID`) in a hidden `metaData`
> form field, keyed by `startOffset`. Consolidation carries real `ofacID`s into the
> output `metaData` so manually-tagged spans persist into the data we keep; if a
> worker edits a span's boundary *after* entering an ID, the record won't re-key to
> the new offset.

---

## The custom UI template

`ui/ner-template.liquid.html` is a `<crowd-entity-annotation>` NER UI inside a
`<crowd-form>`, with a right-hand entity panel and an OFAC-ID editor. The worker:

- **highlights and labels** entity spans with the configured label set, and can
  **add new spans or remove existing ones freely** (no forced prompts);
- sees a **right-hand entity list panel** — one card per span showing the text, its
  label, offsets, model `confidence`, and current OFAC ID. Clicking the span text
  scrolls to and flashes that span in the document;
- **clicks an entity's OFAC ID (or "edit")** to open a small modal and enter/edit
  the ID (pre-filled with the current value; `"FILL"` means not yet set and is shown
  highlighted). Leaving it blank keeps the `"FILL"` placeholder.

It binds to four task inputs supplied by the pre-annotation Lambda:

| Binding                       | Source                                            |
|-------------------------------|---------------------------------------------------|
| `task.input.taskObject`       | document text (`source`)                          |
| `task.input.labels`           | entity label set (record `labels` config)         |
| `task.input.initialEntities`  | seed spans `{startOffset, endOffset, label}` (may be `[]`) |
| `task.input.metaData`         | per-span `{startOffset, endOffset, confidence, ofacID}` (parallel to the spans) |

A JS `metaMap` (keyed by `startOffset`) holds the live per-span metadata, seeded
from `task.input.metaData`. On every change it is **reconciled** with the current
spans — new spans get a placeholder record (`confidence: null`, `ofacID: "FILL"`),
removed spans are dropped — and serialized into a hidden `metaData` form field. On
submit, the annotation carries both `annotatedResult` (the labeled spans) and
`metaData` (confidence + entered OFAC IDs, keyed by offset).

> Note: the original template was provided pre-truncated; the missing
> `<crowd-form>`/`<crowd-entity-annotation>` element, opening `<script>`, and
> `labelMap` were reconstructed to standard Crowd-HTML and verified to parse.

---

## Input manifest format

`manifests/input.manifest.example` — JSON Lines, one record per line. Each
record has the shape:

```json
{"source": "Acme Corp wired funds to a flagged account in Tehran last March.",
 "labels": {"labels": [{"label": "PERSON"}, {"label": "ORG"}, {"label": "LOC"}, {"label": "SANCTIONED_ENTITY"}]},
 "initialEntities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"}],
 "metaData": [{"startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "FILL"}]}
```

| Field            | Meaning                                                                 |
|------------------|-------------------------------------------------------------------------|
| `source`         | the document text to annotate (or use `source-ref` for an S3 URI)        |
| `labels`         | the entity label-set config for the record (`{"labels": [{"label": …}]}`) |
| `initialEntities`| seed entity spans `{startOffset, endOffset, label}` shown to the worker (may be `[]`) |
| `metaData`       | per-span `{startOffset, endOffset, confidence, ofacID}`, **parallel** to `initialEntities`; `confidence` is the model score and `ofacID` is the `"FILL"` placeholder the annotator replaces |

> **Invariant:** `initialEntities` and `metaData` are parallel and aligned by
> offset — one `metaData` entry per seed span. Both are empty together for a
> document with no detected entities.

`labels`, `initialEntities`, and `metaData` are optional; sensible defaults apply
when omitted. Only these field names are accepted (no legacy variants). For large
documents you may use `{"source-ref": "s3://bucket/key.txt"}` instead of inline
`source`.

### From AWS Comprehend output

In practice the manifest isn't hand-authored — it's generated from the output of an
AWS Comprehend **custom entity recognizer**. Parse that output into a JSON array of
per-document objects and convert it with the bundled tool:

```json
[ { "File": "doc1.txt",
    "Entities": [ { "Score": 0.99, "Type": "OFAC_ORG", "Text": "Acme Corp",
                    "BeginOffset": 0, "EndOffset": 9 }, ... ] }, ... ]
```

```bash
cd python_boto3
python build_manifest_from_comprehend.py \
    --comprehend ../manifests/comprehend_output.example.json \
    --s3-docs-base s3://my-bucket/docs/ \
    --out ../manifests/input.manifest          # [--min-score 0.5] [--labels OFAC_ORG OFAC_POI FTO]
```

For each document the converter (`ner_pipeline/comprehend_to_manifest.py`) emits one
record:

- **`source-ref`** = `--s3-docs-base` + the Comprehend `File` (the pre-annotation
  Lambda fetches the text from S3 at render time);
- **`initialEntities`** = each kept entity's `BeginOffset`/`EndOffset`/`Type` →
  `startOffset`/`endOffset`/`label`. Only the OFAC types (`OFAC_ORG`, `OFAC_POI`,
  `FTO`) are kept; `--min-score` optionally drops low-confidence spans;
- **`metaData`** = one record per kept entity, **parallel** to `initialEntities`:
  `{startOffset, endOffset, confidence, ofacID}`, where `confidence` is the
  Comprehend `Score` and `ofacID` is the `"FILL"` placeholder. This is an incoming
  **analysis** job (no pre-existing OFAC IDs); the reviewer confirms spans and fills
  the IDs in the UI.

Documents with **no detected entities** (`Entities: []`, null, or everything filtered
out) are still included — as records with `initialEntities: []` and `metaData: []` —
so a reviewer labels them from scratch and Comprehend false negatives aren't silently
dropped.

The result is the same JSON-Lines manifest described above, so the rest of the
pipeline is unchanged.

#### As a Lambda (raw `output.tar.gz` → manifest)

`lambdas/comprehend_to_manifest/handler.py` does the same conversion at runtime,
straight from the raw Comprehend async artifact: it downloads `output.tar.gz`,
unzips it in memory, and writes the manifest to S3. It can be **invoked manually**
or **triggered on S3 arrival** (S3 notification or EventBridge — all three event
shapes are handled). Configured via env vars: `SOURCE_DOCS_S3_BASE` (the
`--s3-docs-base` equivalent), `MANIFEST_S3_BUCKET` / `MANIFEST_S3_KEY`,
`ENTITY_LABELS`, optional `MIN_SCORE`.

**This is a one-shot batch step, not per-case.** A single invoke parses the
*entire* `output.tar.gz` (every document), builds *every* manifest record, and
writes the *complete* `input/input.manifest` in one `PutObject` — it never touches
cases individually. Launching the labeling job is then a **deliberate, separate
step** (`terraform apply` / the boto3 launcher) so you can review the finished
manifest first. The **per-case fan-out happens inside Ground Truth at job runtime**:
GT reads the manifest and invokes the pre-annotation Lambda **once per record**, so
each case flows individually through pre-annotation → UI → consolidation. So the
flow is: batch convert → review → launch → GT fans out per-case.

Manual invoke:

```bash
aws lambda invoke --function-name usdc-ner-comprehend-to-manifest \
    --payload '{"bucket":"my-bucket","key":"comprehend-output/.../output.tar.gz"}' out.json
```

Terraform deploys this Lambda + its IAM role **only when `source_docs_s3_base` is
set** (off by default). It does **not** create the S3 trigger — the stack only
references the bucket, so attach the ObjectCreated notification / EventBridge rule
yourself (an `aws_lambda_permission` for S3 is already in place). See
[`terraform/README.md`](terraform/README.md).

---

## Repository layout

```
.
├── README.md                       This file
├── terraform/                      IaC: IAM + Lambdas, reference existing S3, launch job
│   ├── versions.tf  providers.tf  variables.tf  terraform.tfvars.example
│   ├── s3.tf                       references the EXISTING bucket + objects (no uploads)
│   ├── iam.tf  lambda.tf
│   ├── labeling_job.tf             null_resource that runs create-labeling-job
│   ├── create-labeling-job.json.tftpl
│   └── outputs.tf
├── lambdas/                        Lambda source (deployed by Terraform)
│   ├── pre_annotation/handler.py
│   ├── post_annotation_single/handler.py
│   └── comprehend_to_manifest/handler.py   Comprehend output.tar.gz -> manifest (optional)
├── ui/ner-template.liquid.html     Custom Crowd-HTML UI (OFAC-aware)
├── manifests/
│   ├── input.manifest.example          hand-authored GT manifest (demo)
│   └── comprehend_output.example.json  sample parsed Comprehend output
├── tests/test_lambdas.py           Unit tests for the Lambda handlers
└── python_boto3/                   Python/boto3 equivalent + local simulator
    ├── ner_pipeline/               pre-annotation, consolidation, comprehend converter, simulator, launcher
    ├── build_manifest_from_comprehend.py  Comprehend output -> input manifest
    ├── run_local_simulation.py     run the whole flow offline
    ├── launch_labeling_job.py      boto3 create-labeling-job (with --dry-run)
    ├── sample_data/                simulated worker answers
    └── tests/test_pipeline.py
```

---

## Option A — Terraform (provision + launch)

> **Prerequisite:** the bucket named in `s3_bucket_name` must already exist and
> already contain the input manifest and the UI template at `manifest_s3_key` /
> `ui_template_s3_key`. Terraform references them (data source) and uploads nothing.
> The bucket must also allow `GET` via CORS so the worker UI can fetch the template.

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
#   edit terraform.tfvars: s3_bucket_name (existing), private_workteam_arn, keys, ...

terraform init
terraform plan
terraform apply        # deploys IAM + Lambdas AND launches the labeling job
```

This creates the Ground Truth execution role, two Lambdas (pre-annotation +
single-worker consolidation), and then runs `create-labeling-job` against the
existing S3 assets.

```bash
terraform output labeling_job_console_url   # open the job in the console
terraform destroy                           # stops the job + tears down infra
```

> Requires **AWS CLI v2** on the machine running `apply` (the launch step shells
> out to it), plus AWS credentials.

### Key variables (`terraform/variables.tf`)

| Variable | Purpose | Default |
|----------|---------|---------|
| `s3_bucket_name` | Name of the **existing** bucket holding the assets | _(required)_ |
| `private_workteam_arn` | Existing private workteam ARN | _(required)_ |
| `manifest_s3_key` | Existing manifest object key | `input/input.manifest` |
| `ui_template_s3_key` | Existing UI template object key | `templates/ner-template.liquid.html` |
| `output_s3_prefix` | Output prefix in the bucket | `output/` |
| `entity_labels` | Label set | `["OFAC_ORG","OFAC_POI","FTO"]` |
| `label_attribute_name` | Output manifest key | `ner-labels` |
| `aws_region` | Region | `us-east-1` |

---

## Option B — Python/boto3 (local testing & launch)

Run the **entire pipeline offline** (no AWS, no credentials needed):

```bash
cd python_boto3
python run_local_simulation.py
```

Launch a real job via boto3 (infra/roles/Lambdas **and** the S3 assets must
already exist — the launcher uploads nothing):

```bash
pip install -r requirements.txt
python launch_labeling_job.py --dry-run \      # preview the request first
    --job-name usdc-ner-demo --bucket my-existing-bucket \
    --role-arn arn:... --workteam-arn arn:... \
    --pre-lambda-arn arn:... --post-lambda-arn arn:...

# drop --dry-run to create the job (references the manifest/template already in S3)
```

See [`python_boto3/README.md`](python_boto3/README.md) for details.

---

## Testing & verification

```bash
# Lambda handler unit tests (no dependencies)
python tests/test_lambdas.py

# Python pipeline tests (no dependencies; boto3 only for real launches)
cd python_boto3 && python tests/test_pipeline.py

# Terraform
cd terraform && terraform fmt -check && terraform validate
```

---

## Prerequisites

- An AWS account with SageMaker Ground Truth access.
- An existing **private workteam** (create one in the Ground Truth console) and its ARN.
- **Terraform ≥ 1.5** and **AWS CLI v2** (for Option A).
- **Python ≥ 3.9** (Option B; `boto3` only needed to launch real jobs).

---

## Security notes

- Never commit credentials or PATs. Treat any token shared in plaintext as
  compromised and **rotate it immediately**.
- The S3 bucket blocks public access and enables SSE + versioning by default.
- IAM policies are scoped to the project bucket and the three labeling Lambdas.
