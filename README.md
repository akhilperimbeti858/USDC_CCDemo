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

- [Architecture & flow](#architecture--flow)
- [How custom labeling works](#how-custom-labeling-works)
- [The Lambda logic](#the-lambda-logic)
- [The custom UI template](#the-custom-ui-template)
- [Input manifest format](#input-manifest-format)
- [Repository layout](#repository-layout)
- [Option A — Terraform](#option-a--terraform-provision--launch)
- [Option B — Python/boto3](#option-b--pythonboto3-local-testing--launch)
- [Consolidation modes](#consolidation-modes-single-vs-merge)
- [Testing & verification](#testing--verification)
- [Prerequisites](#prerequisites)
- [Security notes](#security-notes)

---

## Architecture & flow

```
                          ┌──────────────────────────────────────────────┐
   input.manifest (S3) ──▶│            SageMaker Ground Truth             │
   ner-template (S3)   ──▶│              custom labeling job             │
                          └───────┬───────────────────────┬──────────────┘
                                  │ (per data object)      │ (after batch labeled)
                                  ▼                        ▼
                       ┌────────────────────┐   ┌────────────────────────────┐
                       │ Pre-annotation     │   │ Post-annotation /          │
                       │ Lambda             │   │ consolidation Lambda       │
                       │ build taskInput    │   │ single  ► pass-through     │
                       │ (text, labels,     │   │ merge   ► vote + threshold │
                       │  ofacMetadata)     │   │                            │
                       └─────────┬──────────┘   └─────────────┬──────────────┘
                                 ▼                            ▼
                      rendered custom UI            output manifest (S3)
                   (private workteam labels)      consolidated NER entities
```

1. The job reads a JSON-Lines **input manifest** from S3.
2. For **each** data object, Ground Truth calls the **pre-annotation Lambda**,
   which returns a `taskInput` object.
3. The **custom UI template** is rendered with that `taskInput` and shown to a
   worker on the **private workteam**.
4. Once all workers finish a batch, Ground Truth calls the **post-annotation
   (consolidation) Lambda**, which merges worker answers into a single label.
5. Consolidated labels are written to the **output manifest** in S3.

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
    "labels": [{ "label": "PERSON" }, { "label": "ORG" }, { "label": "LOC" }, { "label": "SANCTIONED_ENTITY" }],
    "initialValue": [],
    "ofacMetadata": [
      { "startOffset": 0, "endOffset": 9, "ofacId": "SDN-12345", "label": "ORG" }
    ]
  },
  "isHumanAnnotationRequired": "true"
}
```

### Post-annotation Lambda — two variants
Both read the consolidation payload from S3 and emit
`consolidatedAnnotation.content[labelAttributeName].entities`.

- **Single-worker** — `lambdas/post_annotation_single/handler.py`
  Passes the one worker's entity spans straight through, re-attaching the `ofacId`
  by `startOffset` if the crowd element dropped it.

- **Multi-worker merge** — `lambdas/post_annotation_merge/handler.py`
  1. Flattens all workers' spans.
  2. Clusters spans whose character ranges overlap.
  3. Per cluster: majority-vote the **label** and the exact `(start, end)` boundary.
  4. Keeps a span only if ≥ `AGREEMENT_RATIO` (default 0.5) of workers agreed.
  5. Emits each entity with a `confidence` and the majority `ofacId`.

---

## The custom UI template

`ui/ner-template.liquid.html` is a `<crowd-entity-annotation>` NER UI with:

- a right-hand **entity list panel** (click to scroll/flash the span in the doc), and
- an **OFAC ID modal** that prompts for an OFAC ID when a new, unknown entity is added.

It binds to four task inputs supplied by the pre-annotation Lambda:

| Binding                    | Source                                  |
|----------------------------|------------------------------------------|
| `task.input.taskObject`    | document text (`source`)                 |
| `task.input.labels`        | entity label set (record `labels` config) |
| `task.input.initialValue`  | seed spans, from `initialEntities` (may be `[]`) |
| `task.input.ofacMetadata`  | per-span OFAC records, from `ofac_metadata` (`ofacId` by offset) |

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
| `initialEntities`| seed entity spans shown to the worker (may be `[]`)                       |
| `ofac_metadata`  | per-span OFAC records — **empty `[]` for incoming analysis jobs, pre-populated for training jobs** |

`labels`, `initialEntities`, and `ofac_metadata` are optional; sensible defaults
apply when omitted. The legacy field names `initialValue`/`ofacMetadata` are
still accepted. For large documents you may use
`{"source-ref": "s3://bucket/key.txt"}` instead of inline `source`.

---

## Repository layout

```
.
├── README.md                       This file
├── terraform/                      IaC: provision infra + launch the job
│   ├── versions.tf  providers.tf  variables.tf  terraform.tfvars.example
│   ├── s3.tf  iam.tf  lambda.tf
│   ├── labeling_job.tf             null_resource that runs create-labeling-job
│   ├── create-labeling-job.json.tftpl
│   └── outputs.tf
├── lambdas/                        Lambda source (deployed by Terraform)
│   ├── pre_annotation/handler.py
│   ├── post_annotation_single/handler.py
│   └── post_annotation_merge/handler.py
├── ui/ner-template.liquid.html     Custom Crowd-HTML UI (OFAC-aware)
├── manifests/input.manifest.example
├── tests/test_lambdas.py           Unit tests for the Lambda handlers
└── python_boto3/                   Python/boto3 equivalent + local simulator
    ├── ner_pipeline/               pre-annotation, consolidation, simulator, launcher
    ├── run_local_simulation.py     run the whole flow offline
    ├── launch_labeling_job.py      boto3 create-labeling-job (with --dry-run)
    ├── sample_data/                simulated worker answers
    └── tests/test_pipeline.py
```

---

## Option A — Terraform (provision + launch)

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
#   edit terraform.tfvars: s3_bucket_name, private_workteam_arn, consolidation_mode, ...

terraform init
terraform plan
terraform apply        # builds infra AND launches the labeling job
```

This creates the S3 bucket (uploading the manifest + UI template), the Ground
Truth execution role, three Lambdas, and then runs `create-labeling-job`.

```bash
terraform output labeling_job_console_url   # open the job in the console
terraform destroy                           # stops the job + tears down infra
```

> Requires **AWS CLI v2** on the machine running `apply` (the launch step shells
> out to it), plus AWS credentials.

### Key variables (`terraform/variables.tf`)

| Variable | Purpose | Default |
|----------|---------|---------|
| `s3_bucket_name` | Globally-unique bucket | _(required)_ |
| `private_workteam_arn` | Existing private workteam ARN | _(required)_ |
| `consolidation_mode` | `single` or `merge` | `single` |
| `number_of_human_workers_per_object` | Workers per item | `1` |
| `entity_labels` | Label set | `["PERSON","ORG","LOC","SANCTIONED_ENTITY"]` |
| `label_attribute_name` | Output manifest key | `ner-labels` |
| `aws_region` | Region | `us-east-1` |

---

## Option B — Python/boto3 (local testing & launch)

Run the **entire pipeline offline** (no AWS, no credentials needed):

```bash
cd python_boto3
python run_local_simulation.py --mode single
python run_local_simulation.py --mode merge
```

Launch a real job via boto3 (infra/roles/Lambdas must already exist):

```bash
pip install -r requirements.txt
python launch_labeling_job.py --dry-run \      # preview the request first
    --job-name usdc-ner-demo --bucket my-bucket \
    --role-arn arn:... --workteam-arn arn:... \
    --pre-lambda-arn arn:... --post-lambda-arn arn:...

# drop --dry-run to actually upload assets + create the job
```

See [`python_boto3/README.md`](python_boto3/README.md) for details.

---

## Consolidation modes: single vs. merge

| | `single` | `merge` |
|---|----------|---------|
| Workers per object | 1 | N (>1) |
| Logic | pass-through | overlap-cluster + majority vote |
| Survives if | always | ≥ `AGREEMENT_RATIO` of workers agree |
| Output extras | — | `confidence` per entity |

Set `consolidation_mode` (Terraform) or `--mode` (simulator) to choose. Both
post-annotation Lambdas are always deployed; the job is wired to the selected one.

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
