# Terraform — self-contained, event-driven NER labeling pipeline (handoff stack)

A **single Terraform stack** an admin applies once. It deploys everything needed to turn
an AWS Comprehend analysis result into a launched **SageMaker Ground Truth** custom-NER
labeling job, **automatically**. After `terraform apply`, the pipeline is hands-off: when
a Comprehend `output.tar.gz` lands in the bucket, the input manifest is built and a
labeling job launches on its own. **The only file an operator edits is `terraform.tfvars`**
— every other value is variable-driven.

This stack **references** (does not create) the S3 bucket and the private workteam, and it
**consumes** Comprehend output produced outside the stack. It **creates** the IAM, the four
Lambdas, and the EventBridge wiring that links them.

---

## Table of contents
- [Architecture & flow](#architecture--flow)
- [What it creates vs. requires](#what-it-creates-vs-requires)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [The four Lambdas](#the-four-lambdas)
- [Hardcoded / load-bearing locations](#hardcoded--load-bearing-locations)
- [EventBridge wiring](#eventbridge-wiring)
- [Variables](#variables)
- [Outputs](#outputs)
- [`terraform destroy` — what is removed vs. what survives](#terraform-destroy--what-is-removed-vs-what-survives)
- [Operations & troubleshooting](#operations--troubleshooting)

---

## Architecture & flow

The "links" between steps are **S3 → EventBridge "Object Created" events**. Two Lambdas
listen to the *same bucket* via two rules; the first Lambda's output file is the second
Lambda's trigger.

```
 ┌──────────────────────────────┐
 │ External AWS Comprehend job  │   (you run this via boto3 — NOT created here)
 │ entity-detection analysis    │
 └──────────────┬───────────────┘
                │ writes  <prefix>/<MODEL-ID>-NER-<JOB-ID>/output/output.tar.gz
                ▼   into the EXISTING bucket
        ╔═══════════════════════╗
        ║   S3 bucket (existing)║  ── "Object Created" ──►  Amazon EventBridge
        ╚═══════════════════════╝                                  │
                                                                   │ rule #1: key suffix "output.tar.gz"
                                                                   ▼
                                              ┌─────────────────────────────────────┐
                                              │ Lambda 1: comprehend_to_manifest     │
                                              │  reads output.tar.gz, builds manifest│
                                              └──────────────┬──────────────────────┘
                                                             │ writes  input/input.manifest
                                                             ▼   (back into the same bucket)
                                              S3 "Object Created" ──► EventBridge
                                                             │ rule #2: key == "input/input.manifest"
                                                             ▼
                                              ┌─────────────────────────────────────┐
                                              │ Lambda 2: launch_labeling_job        │
                                              │  sagemaker.create_labeling_job(...)  │
                                              └──────────────┬──────────────────────┘
                                                             ▼
                       ┌─────────────────── Ground Truth labeling job ───────────────────┐
                       │  per data object:                                               │
                       │   Lambda 3: pre_annotation  → builds taskInput for the worker   │
                       │   Worker UI (ui/ner-template.liquid.html) → human labels + OFAC  │
                       │   Lambda 4: post_annotation_single → consolidates → output       │
                       └──────────────┬──────────────────────────────────────────────────┘
                                      ▼
                             output/  (labeled training/analysis data)
```

**No loop:** Lambda 1 writes only `input/input.manifest` (matches rule #2 only). Lambda 2
writes nothing to the bucket. Ground Truth writes only under `output/`. So the relay runs
forward exactly once per `output.tar.gz` and stops.

**No manifest needed at apply time:** the job is created by a Lambda when the manifest
actually lands, so there is no chicken-and-egg with `terraform apply`. Each new
`output.tar.gz` ⇒ new manifest ⇒ a new, uniquely-named labeling job.

---

## What it creates vs. requires

**Creates (managed by this stack):**
- 1 Ground Truth **execution role** + 4 **Lambda execution roles** (+ their policies).
- 4 **Lambda functions** (pre, post, converter, launcher).
- 4 **Lambda permissions** (SageMaker invoke ×2, EventBridge invoke ×2).
- 2 **EventBridge rules** (+ targets).
- Optionally, the **UI template object** uploaded into the bucket (`upload_ui_template`).
- Optionally, the **bucket EventBridge toggle** (`manage_bucket_eventbridge`, default off).

**Requires to already exist (NOT created here):**
- The **S3 bucket** (`s3_bucket_name`) — referenced via a data source.
- A **private workteam** (`private_workteam_arn`).
- The external **Comprehend analysis job** that writes `output.tar.gz` into the bucket.
- **EventBridge enabled on the bucket** (see prerequisites).

---

## Prerequisites

1. **An existing S3 bucket** (`s3_bucket_name`). It holds the UI template, receives the
   Comprehend `output.tar.gz`, and receives the generated manifest + job output.
2. **EventBridge notifications enabled on that bucket.** Because `manage_bucket_eventbridge`
   defaults to `false`, this stack does **not** flip it for you. Turn it on once:
   S3 → bucket → **Properties** → **Amazon EventBridge** → **On**. (Use that toggle — *not*
   the "Event notifications" section, which is a different, direct mechanism.) Do this
   **before** the analysis job writes `output.tar.gz`; S3 does not replay past events.
3. **An existing private workteam** (`private_workteam_arn`).
4. The **Comprehend job's `OutputDataConfig`** must point into this same bucket.
5. **AWS credentials** for `terraform apply` with permission to create IAM roles/policies,
   Lambda functions/permissions, EventBridge rules, and (if `upload_ui_template=true`) to
   `PutObject` the template. **AWS CLI is not required** — the labeling job is launched
   inside a Lambda, not via `local-exec`.

---

## Quick start

```bash
cd terraform-handoff
cp terraform.tfvars.example terraform.tfvars
#   edit terraform.tfvars: s3_bucket_name, private_workteam_arn, source_docs_s3_base
#   (everything else has a default)

terraform init
terraform plan
terraform apply        # deploys the pipeline; launches nothing yet

# Make sure the bucket's EventBridge toggle is ON, then run your Comprehend job.
# When output.tar.gz lands, the manifest is built and a labeling job launches automatically.
terraform output labeling_jobs_console_url
```

---

## The four Lambdas

All four are packaged by `archive_file` from `../lambdas/<name>/` into `build/<name>.zip`
and deployed with handler `handler.lambda_handler` on `var.lambda_runtime` (default
`python3.12`). Source code is shared with the other stacks in this repo.

### 1. `pre_annotation` — `${project_name}-SageMaker-pre-annotation`
- **Source:** `../lambdas/pre_annotation/handler.py`
- **Trigger:** invoked by **Ground Truth**, once per data object, before rendering.
- **Role:** `${project_name}-pre-lambda-role` — `AWSLambdaBasicExecutionRole` (logs) +
  `s3:GetObject` on `<bucket>/*` (to fetch `source-ref` document text).
- **Env:** `ENTITY_LABELS` = `jsonencode(var.entity_labels)`.
- **Does:** turns a manifest record into the `taskInput` (`taskObject`, `labels`,
  `initialEntities`, `metaData`) the Crowd-HTML template binds to.

### 2. `post_annotation_single` — `${project_name}-SageMaker-post-single`
- **Source:** `../lambdas/post_annotation_single/handler.py`
- **Trigger:** invoked by **Ground Truth** for annotation consolidation (per batch).
- **Role:** `${project_name}-post-lambda-role` — logs + `s3:GetObject/PutObject/ListBucket`
  on the bucket.
- **Env:** none (reads everything from the GT consolidation payload).
- **Does:** single-worker pass-through → emits `entities` + parallel `metaData`
  (carries `confidence`; drops `ofacID` when still the `"FILL"` placeholder).

### 3. `comprehend_to_manifest` (the converter) — `${project_name}-comprehend-to-manifest`
- **Source:** `../lambdas/comprehend_to_manifest/handler.py`
- **Trigger:** **EventBridge** rule #1 — S3 "Object Created" whose key ends in
  `output.tar.gz` (optionally scoped by `comprehend_output_key_prefix`).
- **Role:** `${project_name}-converter-lambda-role` — logs + `s3:GetObject` on
  `arn:aws:s3:::<comprehend_output_bucket>/*` + `s3:PutObject` on `<bucket>/*`.
- **Memory/timeout:** `var.converter_lambda_memory_mb` (512) / `var.converter_lambda_timeout_seconds` (300).
- **Env:**

  | Variable | Value |
  |----------|-------|
  | `SOURCE_DOCS_S3_BASE` | `var.source_docs_s3_base` |
  | `MANIFEST_S3_BUCKET`  | `var.s3_bucket_name` |
  | `MANIFEST_S3_KEY`     | `var.manifest_s3_key` (`input/input.manifest`) |
  | `ENTITY_LABELS`       | `jsonencode(var.entity_labels)` |
  | `MIN_SCORE`           | `var.min_score` |
- **Does:** downloads the whole `output.tar.gz`, parses every document, and writes the
  **complete** manifest in one `PutObject`.

### 4. `launch_labeling_job` (the launcher) — `${project_name}-launch-labeling-job`
- **Source:** `../lambdas/launch_labeling_job/handler.py`
- **Trigger:** **EventBridge** rule #2 — S3 "Object Created" with key == `var.manifest_s3_key`.
- **Role:** `${project_name}-launcher-lambda-role` — logs +
  `sagemaker:CreateLabelingJob` on `arn:aws:sagemaker:<region>:<acct>:labeling-job/*` +
  `iam:PassRole` on the GT execution role (condition: `iam:PassedToService = sagemaker.amazonaws.com`).
- **Env (the full create-labeling-job config):**

  | Variable | Source |
  |----------|--------|
  | `ROLE_ARN` | the GT execution role created here |
  | `WORKTEAM_ARN` | `var.private_workteam_arn` |
  | `PRE_LAMBDA_ARN` / `POST_LAMBDA_ARN` | the two GT Lambdas above |
  | `MANIFEST_S3_URI` / `UI_TEMPLATE_S3_URI` / `OUTPUT_S3_URI` | derived `s3://…` locals |
  | `LABEL_ATTRIBUTE_NAME` | `var.label_attribute_name` (`ner-labels`) |
  | `JOB_NAME_PREFIX` | `var.job_name_prefix` or `var.project_name` |
  | `TASK_TITLE` / `TASK_DESCRIPTION` / `TASK_KEYWORDS` | `var.task_*` |
  | `TASK_TIME_LIMIT_SECONDS` / `TASK_AVAILABILITY_LIFETIME_SECONDS` | `var.task_*` |
  | `MAX_CONCURRENT_TASK_COUNT` / `WORKERS_PER_OBJECT` | `var.*` |
- **Does:** builds the `create_labeling_job` request from env and calls SageMaker. Job name
  is `<JOB_NAME_PREFIX>-<UTC timestamp>` (unique per run; GT job names are immutable).

---

## Hardcoded / load-bearing locations

| Thing | Value | Why it matters |
|-------|-------|----------------|
| `SageMaker` token in pre/post **function names** | `${project_name}-SageMaker-pre-annotation` / `-post-single` | The `AmazonSageMakerGroundTruthExecution` managed policy only permits invoking Lambdas whose names contain a recognized token. **Don't rename it out.** |
| Default **manifest key** | `input/input.manifest` (`var.manifest_s3_key`) | Written by the converter, read by the job, and is the **exact-match key** for EventBridge rule #2. |
| Default **UI template key** | `templates/ner-template.liquid.html` (`var.ui_template_s3_key`) | Uploaded here (optional) and passed to the job as `UiTemplateS3Uri`. |
| Default **output prefix** | `output/` (`var.output_s3_prefix`) | `S3OutputPath` for the job. |
| EventBridge **match for the tarball** | key **suffix** `output.tar.gz` (+ optional `comprehend_output_key_prefix` wildcard) | Comprehend writes to a per-run path `<base>/<MODEL-ID>-NER-<JOB-ID>/output/output.tar.gz`; suffix matching tolerates the dynamic middle. |
| EventBridge **match for the manifest** | key **equals** `var.manifest_s3_key` | Exact key, so only the converter's output fires rule #2. |
| Managed policies | `AmazonSageMakerGroundTruthExecution`, `AWSLambdaBasicExecutionRole` | Attached to the GT role and each Lambda role respectively. |
| GT role **S3 scope** | `data.aws_s3_bucket.gt.arn` and `…/*` | Read/write on the existing bucket only. |
| Converter **read scope** | `arn:aws:s3:::<comprehend_output_bucket>/*` (`comprehend_output_bucket` → `s3_bucket_name` when empty) | Where it reads `output.tar.gz`. |
| Launcher **SageMaker scope** | `arn:aws:sagemaker:<region>:<account>:labeling-job/*` | Plus `iam:PassRole` on the GT role only. |
| **Job name** | `<job_name_prefix>-<UTC timestamp>` | Built inside the launcher Lambda; unique per run. |

---

## EventBridge wiring

Two `aws_cloudwatch_event_rule`s (+ targets) in `eventbridge.tf`:

- **`${project_name}-comprehend-output`** → target = converter Lambda. Pattern:
  `source = aws.s3`, `detail-type = Object Created`, `detail.bucket.name = [s3_bucket_name]`,
  `detail.object.key = [{ suffix = "output.tar.gz" }]` (or `[{ wildcard = "<prefix>*output.tar.gz" }]`
  when `comprehend_output_key_prefix` is set).
- **`${project_name}-manifest-created`** → target = launcher Lambda. Pattern: same, but
  `detail.object.key = [var.manifest_s3_key]` (exact).

Each Lambda gets an `aws_lambda_permission` allowing `events.amazonaws.com` (scoped to the
rule ARN) to invoke it. These rules are **not** authoritative over the bucket — they never
touch its notification config. The bucket's EventBridge toggle is the only piece left to
you (or set `manage_bucket_eventbridge = true`, but that's authoritative — see caveats).

---

## Variables

Full list with defaults lives in `variables.tf`. Required (no default): `s3_bucket_name`,
`private_workteam_arn`, `source_docs_s3_base`.

| Group | Variables (default) |
|-------|---------------------|
| **Core** | `aws_region` (`us-east-1`), `project_name` (`usdc-ner`), `managed_by_tag` (`terraform`), `workflow_tag` (`ground-truth-ner`) |
| **Bucket / keys** | `s3_bucket_name` *(req)*, `manifest_s3_key` (`input/input.manifest`), `ui_template_s3_key` (`templates/ner-template.liquid.html`), `output_s3_prefix` (`output/`) |
| **UI template upload** | `upload_ui_template` (`true`), `ui_template_local_path` (`../ui/ner-template.liquid.html`) |
| **EventBridge** | `manage_bucket_eventbridge` (`false`), `comprehend_output_suffix` (`output.tar.gz`), `comprehend_output_key_prefix` (`""`) |
| **Converter** | `source_docs_s3_base` *(req)*, `comprehend_output_bucket` (`""` → `s3_bucket_name`), `entity_labels` (`["OFAC_ORG","OFAC_POI","FTO"]`), `min_score` (`""`) |
| **GT job** | `private_workteam_arn` *(req)*, `label_attribute_name` (`ner-labels`), `job_name_prefix` (`""` → `project_name`), `task_title`, `task_description`, `task_keywords`, `task_time_limit_seconds` (3600), `task_availability_lifetime_seconds` (864000), `max_concurrent_task_count` (1000), `workers_per_object` (1) |
| **Lambda knobs** | `lambda_runtime` (`python3.12`), `pre_lambda_timeout_seconds` (60), `post_lambda_timeout_seconds` (300), `converter_lambda_timeout_seconds` (300), `converter_lambda_memory_mb` (512), `launcher_lambda_timeout_seconds` (60) |

---

## Outputs

From `outputs.tf`: `s3_bucket`, `manifest_s3_uri`, `ui_template_s3_uri`, `output_s3_uri`,
`ground_truth_role_arn`, `pre_annotation_lambda_arn`, `post_single_lambda_arn`,
`converter_lambda_arn`, `launcher_lambda_arn`, `comprehend_output_rule_arn`,
`manifest_created_rule_arn`, `job_name_prefix`, and `labeling_jobs_console_url`.

---

## `terraform destroy` — what is removed vs. what survives

### Removed (everything this stack manages)
- The **GT execution role** + 4 **Lambda execution roles**, with their inline policies and
  managed-policy attachments.
- All **4 Lambda functions** and their **4 invoke permissions**.
- Both **EventBridge rules** and their targets.
- The **uploaded UI template object** — ⚠️ `aws_s3_object.ui_template` is managed, so destroy
  **deletes `templates/ner-template.liquid.html` from the bucket** (when `upload_ui_template=true`).
- The **bucket EventBridge notification config** *only if* `manage_bucket_eventbridge=true`
  (default is `false`, so it isn't created or destroyed).
- Local `build/*.zip` are just artifacts (not AWS resources).

### Survives (NOT touched by destroy)
- The **S3 bucket itself** — it's referenced via a data source, never created here.
- The **input manifest**, the **`output.tar.gz`**, and all **job output under `output/`** —
  bucket objects this stack didn't create.
- ⚠️ **Any SageMaker labeling jobs the launcher created.** They are created *imperatively at
  runtime* and are **not in Terraform state**, so destroy does **not** stop or delete them.
  (Unlike the all-in-one `../terraform` stack, there is no destroy-time `stop-labeling-job`
  here.) **Stop running jobs yourself** via the console or
  `aws sagemaker stop-labeling-job --labeling-job-name <name>`.
- **CloudWatch log groups** `/aws/lambda/<function-name>` — auto-created on first invocation,
  not Terraform-managed; they persist (delete manually if you want them gone).
- The **bucket's EventBridge toggle** if you enabled it by hand in the GUI.

---

## Operations & troubleshooting

- **Nothing fires when `output.tar.gz` lands:** the bucket's EventBridge toggle is off, or it
  was turned on *after* the object was written. Turn it on, then re-drop the object (or re-run
  the Comprehend job) — S3 doesn't replay past events.
- **Re-run the pipeline:** drop a new `output.tar.gz` (or run another Comprehend job). The
  manifest at `manifest_s3_key` is overwritten; each run yields a new, uniquely-named job.
- **Watch it:** `terraform output labeling_jobs_console_url`. Logs are in CloudWatch under
  `/aws/lambda/${project_name}-comprehend-to-manifest` and
  `/aws/lambda/${project_name}-launch-labeling-job`.
- **`AccessDenied` writing the manifest / reading the tarball:** check that the bucket and the
  Comprehend output bucket match what `s3_bucket_name` / `comprehend_output_bucket` expect.
- **No `aws_s3_bucket_notification` here by default:** flipping `manage_bucket_eventbridge=true`
  makes Terraform own the bucket's notification config (**authoritative — it overwrites any
  other notifications** on a bucket the stack doesn't own). Prefer leaving it `false` and
  enabling EventBridge out-of-band.
- This stack is the create-everything counterpart to
  [`../terraform-job-referenced`](../terraform-job-referenced) (which references already-deployed
  shared Lambdas) and [`../terraform`](../terraform) (all-in-one with a CLI-launched job).
