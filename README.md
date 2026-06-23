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
│   text         ← source / source-ref         │
│   labels       ← record `labels` config/env  │
│   initialValue ← `initialEntities`           │
│   ofacMetadata ← `ofac_metadata`             │
│   → { taskInput, isHumanAnnotationRequired } │
└─────────────────────────────────────────────┘
        │ taskInput
        ▼
┌─────────────────────────────────────────────┐
│ Worker UI       ui/ner-template.liquid.html  │
│   highlight + label spans; entity-list panel │
│   NEW span → OFAC modal prompts for an ID    │
│   hidden `ofacOverrides` field ← entered IDs │
│   → submits { annotatedResult, ofacOverrides}│
└─────────────────────────────────────────────┘
        │ all workers' annotations (S3 payload)
        ▼
┌─────────────────────────────────────────────┐
│ POST-annotation Lambda   (consolidation)     │
│   single-worker pass-through;                │  lambdas/post_annotation_single
│   entered OFAC IDs win / are re-attached     │
└─────────────────────────────────────────────┘
        │
        ▼
output/   output manifest — training data; entities carry `ofacId`
```

**OFAC ID flow:** the pre-Lambda seeds known IDs from the manifest; the UI prompts
for an ID whenever a worker labels a *new* span and submits it in the hidden
`ofacOverrides` field; the post-Lambda writes that ID onto the output entity's
`ofacId`. So manually-tagged spans persist into the output/training data.

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
3. Passes through the **OFAC metadata** embedded in the manifest record
   (`ofac_metadata`, default `[]` — empty for analysis jobs, populated for
   training) and the seed spans (`initialEntities`, default `[]`).
4. Returns the `taskInput` the template binds to (the manifest's
   `initialEntities`/`ofac_metadata` map to the template's existing
   `initialValue`/`ofacMetadata` keys):

```json
{
  "taskInput": {
    "taskObject": "<document text>",
    "labels": [{ "label": "OFAC_ORG" }, { "label": "OFAC_POI" }, { "label": "FTO" }],
    "initialValue": [],
    "ofacMetadata": [
      { "startOffset": 0, "endOffset": 9, "ofacId": "SDN-12345", "label": "OFAC_ORG" }
    ]
  },
  "isHumanAnnotationRequired": "true"
}
```

### Post-annotation Lambda — single-worker consolidation
`lambdas/post_annotation_single/handler.py` reads the consolidation payload from S3
and emits `consolidatedAnnotation.content[labelAttributeName].entities`.

It passes the one worker's entity spans straight through, attaching each span's
`ofacId` by `startOffset`. An OFAC ID the annotator typed in the UI modal
(submitted via the hidden `ofacOverrides` field) **wins** over the manifest seed
and is the only source for a brand-new span the manifest never knew about.

> A multi-worker merge/vote consolidator is intentionally out of scope for now; the
> workflow runs with one worker per object.

> **Annotator-entered OFAC IDs reach the output.** When a worker labels a new entity,
> the UI prompts for an OFAC ID and submits it in a hidden `ofacOverrides` form field.
> Consolidation writes that ID onto the output entity's `ofacId`, so manually-tagged
> spans become part of the training data we keep. OFAC overrides are keyed by
> `startOffset` (consistent with the rest of the pipeline); if a worker edits a span's
> boundary *after* entering an ID, the override won't re-key to the new offset.

---

## The custom UI template

`ui/ner-template.liquid.html` is a `<crowd-entity-annotation>` NER UI with:

- a right-hand **entity list panel** (click to scroll/flash the span in the doc),
- an **OFAC ID modal** that prompts for an OFAC ID when a new, unknown entity is added, and
- a hidden `ofacOverrides` form field (inside `<crowd-form>`) that carries the
  entered OFAC IDs into the submitted annotation so consolidation can persist them.

It binds to four task inputs supplied by the pre-annotation Lambda:

| Binding                    | Source                                  |
|----------------------------|------------------------------------------|
| `task.input.taskObject`    | document text (`source`)                 |
| `task.input.labels`        | entity label set (record `labels` config) |
| `task.input.initialValue`  | seed spans, from `initialEntities` (may be `[]`) |
| `task.input.ofacMetadata`  | per-span OFAC records, from `ofac_metadata` (`ofacId` by offset) |

On submit, the worker's annotation includes both `annotatedResult` (the labeled
entities) and `ofacOverrides` (the OFAC IDs entered in the modal, keyed by offset).

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
 "initialEntities": [{"label": "ORG", "startOffset": 0, "endOffset": 9}],
 "ofac_metadata": [{"startOffset": 0, "endOffset": 9, "ofacId": "SDN-12345", "label": "ORG"}]}
```

| Field            | Meaning                                                                 |
|------------------|-------------------------------------------------------------------------|
| `source`         | the document text to annotate (or use `source-ref` for an S3 URI)        |
| `labels`         | the entity label-set config for the record (`{"labels": [{"label": …}]}`) |
| `initialEntities`| seed entity spans shown to the worker (may be `[]` only when `ofac_metadata` is also empty) |
| `ofac_metadata`  | per-span OFAC records — **empty `[]` for incoming analysis jobs, pre-populated for training jobs** |

> **Invariant:** `initialEntities` is never empty while `ofac_metadata` is
> populated — every OFAC-flagged span is pre-seeded as an initial entity so the
> worker starts from the known sanctioned spans. Both are empty together for an
> incoming analysis job.

`labels`, `initialEntities`, and `ofac_metadata` are optional; sensible defaults
apply when omitted. The legacy field names `initialValue`/`ofacMetadata` are
still accepted. For large documents you may use
`{"source-ref": "s3://bucket/key.txt"}` instead of inline `source`.

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
- **`ofac_metadata`** = `[]` — this is an incoming **analysis** job; the human
  reviewer confirms the seeded spans and adds OFAC IDs in the UI (which then flow to
  the output via `ofacOverrides`).

Documents with **no detected entities** (`Entities: []`, null, or everything filtered
out) are still included — as records with `initialEntities: []` — so a reviewer labels
them from scratch and Comprehend false negatives aren't silently dropped.

The result is the same JSON-Lines manifest described above, so the rest of the
pipeline is unchanged.

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
│   └── post_annotation_single/handler.py
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
