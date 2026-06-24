# Terraform — SageMaker Ground Truth custom NER labeling job

This stack deploys the pieces Amazon SageMaker Ground Truth needs for a **custom
NER labeling job** and then **launches the job**. It is deliberately scoped:

**What it creates**
- An IAM **execution role** for the labeling job + two **Lambda execution roles**.
- Two **Lambda functions** — pre-annotation and single-worker consolidation.
- The **labeling job** itself (via the AWS CLI, since Terraform has no native
  Ground Truth labeling-job resource).
- **Optionally** (when `source_docs_s3_base` is set): a third Lambda + role that
  converts a Comprehend `output.tar.gz` into the input manifest. See
  [Optional converter Lambda](#optional-comprehend-converter-lambda).

**What it does NOT do (by design)**
- It does **not create the S3 bucket** and **uploads nothing**. The bucket, the
  input manifest, and the UI template must **already exist**; this stack only
  *references* them.
- It does **not create the workteam/workforce**. You pass in an existing
  workteam ARN.

---

## Mental model & apply order

```
versions.tf ─ pins Terraform + provider versions
providers.tf ─ configures the AWS provider (region, default tags)
variables.tf ─ declares inputs;  terraform.tfvars supplies values
        │
        ▼
s3.tf ──────► data source: looks up the EXISTING bucket; builds S3 URIs (locals)
iam.tf ─────► GT execution role + 2 Lambda roles & policies
lambda.tf ──► zips lambdas/ + deploys 2 functions + grants SageMaker invoke
labeling_job.tf + create-labeling-job.json.tftpl
        │     ► renders the create-labeling-job request and runs it via the AWS CLI
        ▼
outputs.tf ─ prints ARNs, URIs, job name, console URL
```

**Where values live**
- Variable *declarations* → `variables.tf`. Variable *values* → `terraform.tfvars`
  (copy `terraform.tfvars.example`). Required (no default): `s3_bucket_name`,
  `private_workteam_arn`.
- Derived values → `locals { }` in `s3.tf`, `lambda.tf`, `labeling_job.tf`.
- **State** → no `backend` block, so the default **local** backend writes
  `terraform.tfstate` here. Add a remote backend (S3 + DynamoDB) for teams.
- Generated artifacts → `build/` (the Lambda zip + the rendered request JSON).

---

## Prerequisites

1. **An existing S3 bucket** named in `s3_bucket_name`, already containing:
   - the input manifest at `manifest_s3_key` (default `input/input.manifest`), and
   - the UI template at `ui_template_s3_key` (default
     `templates/ner-template.liquid.html`).
   The bucket must allow **`GET` via CORS** so the worker browser can fetch the
   template, and the execution role needs read/write on it (granted here).
2. **An existing private workteam** (created once in the SageMaker console, backed
   by Cognito) — its ARN goes in `private_workteam_arn`.
3. **AWS CLI v2 + credentials** on the machine running `terraform apply` (the launch
   step shells out to the CLI).

---

## File-by-file

### `versions.tf` — version pinning
```hcl
terraform { required_version = ">= 1.5.0"
  required_providers {
    aws     = { source = "hashicorp/aws",     version = "~> 5.0" }  # AWS resources
    archive = { source = "hashicorp/archive", version = "~> 2.4" }  # zips the Lambdas
    local   = { source = "hashicorp/local",   version = "~> 2.4" }  # writes rendered JSON
    null    = { source = "hashicorp/null",    version = "~> 3.2" }  # runs the CLI launch
  }
}
```
- Requires Terraform ≥ 1.5.0. `~> 5.0` = ">= 5.0, < 6.0".
- Four providers, each with one job: `aws` (infra), `archive` (`archive_file` zips
  `lambdas/`), `local` (`local_file` writes the rendered request), `null`
  (`null_resource` shells out to the AWS CLI to launch the job).

### `providers.tf` — AWS provider
```hcl
provider "aws" {
  region = var.aws_region
  default_tags { tags = { Project = var.project_name, ManagedBy = "terraform", Workflow = "ground-truth-ner" } }
}
```
- Region from `var.aws_region`. `default_tags` auto-applies three tags to every
  taggable resource. Credentials are taken from the environment (not in code).

### `variables.tf` — input declarations
- `aws_region` (default `us-east-1`), `project_name` (default `usdc-ner`).
- **`s3_bucket_name`** (required) — the **existing** bucket.
- **`manifest_s3_key`** / **`ui_template_s3_key`** / **`output_s3_prefix`** — keys
  of the already-present objects (defaults `input/input.manifest`,
  `templates/ner-template.liquid.html`, `output/`).
- **`private_workteam_arn`** (required) — has a `validation` block:
  `can(regex("^arn:aws[a-z-]*:sagemaker:...:workteam/", ...))` fails fast if the ARN
  isn't a workteam ARN.
- `label_attribute_name` (default `ner-labels`) — the output manifest key.
- **`entity_labels`** (default `["OFAC_ORG","OFAC_POI","FTO"]`) — passed to the
  pre-annotation Lambda as the `ENTITY_LABELS` env var (its fallback label set).
- `task_title` / `task_description` — shown to workers.
- `task_time_limit_seconds` (3600) / `task_availability_lifetime_seconds` (864000).

### `terraform.tfvars.example` — values template
Copy to `terraform.tfvars`. Sets `s3_bucket_name` (existing), the three keys, the
workteam ARN, `label_attribute_name`, and `entity_labels`. Anything omitted uses the
`variables.tf` default.

### `s3.tf` — reference the existing bucket (no creation, no uploads)
```hcl
data "aws_s3_bucket" "gt" { bucket = var.s3_bucket_name }   # LOOK UP (don't create)

locals {
  manifest_s3_uri    = "s3://${data.aws_s3_bucket.gt.id}/${var.manifest_s3_key}"
  ui_template_s3_uri = "s3://${data.aws_s3_bucket.gt.id}/${var.ui_template_s3_key}"
  output_s3_uri      = "s3://${data.aws_s3_bucket.gt.id}/${var.output_s3_prefix}"
}
```
- A **data source** looks up the existing bucket (it must exist at plan time). The
  locals build the three S3 URIs from the bucket id + the key variables. There are
  **no** `aws_s3_bucket` / `aws_s3_object` resources — nothing is created or uploaded.
  Bucket settings (encryption, versioning, CORS) are the owner's responsibility.

### `iam.tf` — roles and policies
- `data "aws_caller_identity" "current"` — account lookup (available if needed).
- **Ground Truth execution role** (`aws_iam_role.ground_truth`):
  - Trust policy (`gt_assume`): only `sagemaker.amazonaws.com` may assume it.
  - Managed policy `AmazonSageMakerGroundTruthExecution` attached (GT runtime perms;
    also what permits invoking SageMaker-named Lambdas).
  - Inline policy (`gt_inline`): **S3 read/write** on the existing bucket
    (`data.aws_s3_bucket.gt.arn` + `/*`) and **`lambda:InvokeFunction`** on exactly
    the two functions (pre + post_single) — least privilege.
- **Lambda roles** (both trust `lambda.amazonaws.com` via `lambda_assume`):
  - **Pre-Lambda role**: `AWSLambdaBasicExecutionRole` (logs) + `s3:GetObject`
    (fetch `source-ref` document text at render time).
  - **Post-Lambda role**: logs + S3 read (annotations) + S3 write (output).
- Idiom: `data "aws_iam_policy_document"` builds the JSON; `aws_iam_role_policy`
  attaches it.

### `lambda.tf` — package & deploy the functions
```hcl
locals {
  pre_function_name         = "${var.project_name}-SageMaker-pre-annotation"
  post_single_function_name = "${var.project_name}-SageMaker-post-single"
}
```
- The literal **`SageMaker`** token in each name is load-bearing: the
  `AmazonSageMakerGroundTruthExecution` policy only allows invoking Lambdas whose
  names contain a recognized token.
- `data "archive_file" "pre"` / `"post_single"` zip each handler directory into
  `build/*.zip` at plan time (so `handler.py` is at the zip root).
- `aws_lambda_function.pre_annotation` — role `pre_lambda`, `handler =
  "handler.lambda_handler"`, runtime `python3.12`, 60s timeout, gets
  `ENTITY_LABELS = jsonencode(var.entity_labels)`. `source_code_hash` redeploys on
  code change.
- `aws_lambda_function.post_single` — role `post_lambda`, 300s timeout.
- `aws_lambda_permission.gt_invoke_pre` / `gt_invoke_post_single` — let the
  `sagemaker.amazonaws.com` principal invoke each function (the resource-based half
  of the invoke equation; the role policy is the other half).
- **Optional** (gated by `local.comprehend_enabled = var.source_docs_s3_base != ""`):
  `archive_file` + `aws_lambda_function.comprehend_to_manifest` (the Comprehend
  converter; no SageMaker token needed) + `aws_lambda_permission.s3_invoke_comprehend`
  (lets S3 invoke it). Its IAM role lives in `iam.tf` (also gated). See
  [Optional converter Lambda](#optional-comprehend-converter-lambda).

### `labeling_job.tf` — render the request and launch
```hcl
locals {
  config_hash = substr(sha256(join(":", [
    local.manifest_s3_uri, local.ui_template_s3_uri,
    data.archive_file.pre.output_base64sha256,
    aws_lambda_function.post_single.arn, var.label_attribute_name,
  ])), 0, 8)
  labeling_job_name = "${var.project_name}-ner-${local.config_hash}"
  cli_input_json = templatefile("${path.module}/create-labeling-job.json.tftpl", { ... })
}
```
- **`config_hash`**: 8-char fingerprint of the S3 URIs + pre-Lambda code hash + post
  ARN + label attribute. (Object **etags aren't used** anymore — the assets live in a
  bucket we don't manage, so editing the manifest's *content* won't change this hash;
  changing its key/URI or the Lambda code will.) A changed hash → a new unique job
  name, so re-applying after a config change launches a fresh job instead of
  colliding with the immutable old name.
- **`templatefile(...)`** fills the `.tftpl` placeholders with the manifest/output/
  template URIs, the GT role ARN, the workteam ARN, the pre + post_single ARNs, and
  the task settings.
- `local_file.cli_input` writes the rendered request to
  `build/create-labeling-job.<hash>.json` (inspectable / runnable by hand).
- `null_resource.labeling_job`:
  - `triggers` (config_hash, region, job_name) — a changed hash replaces the resource
    (re-runs create); region/job_name are stored so the **destroy** provisioner can
    read them via `self.triggers`.
  - `depends_on` — IAM, both Lambda invoke permissions, and the rendered file exist
    first.
  - **create** provisioner: `aws sagemaker create-labeling-job --cli-input-json
    file://<rendered>.json` (needs AWS CLI v2 + creds).
  - **destroy** provisioner (`when = destroy`, `on_failure = continue`): best-effort
    `aws sagemaker stop-labeling-job`.

### `create-labeling-job.json.tftpl` — the request template
A JSON template; every `${...}` is filled by the `templatefile` map. Maps directly to
the `create-labeling-job` API:
- `InputConfig.S3DataSource.ManifestS3Uri` ← `manifest_s3_uri`
- `OutputConfig.S3OutputPath` ← `output_s3_uri`
- `RoleArn` ← GT execution role
- `HumanTaskConfig.WorkteamArn` ← existing workteam
- `UiConfig.UiTemplateS3Uri` ← UI template in S3
- `PreHumanTaskLambdaArn` ← pre Lambda; `AnnotationConsolidationLambdaArn` ←
  post_single Lambda
- `NumberOfHumanWorkersPerDataObject` is hard-coded to **1** (single-worker only).
  `MaxConcurrentTaskCount` is hard-coded to 1000. Numeric fields are unquoted so they
  render as JSON numbers.

### `outputs.tf` — what you get back
`s3_bucket` (from the data source), `input_manifest_s3_uri`, `ui_template_s3_uri`,
`output_s3_uri`, `ground_truth_role_arn`, `pre_annotation_lambda_arn`,
`post_single_lambda_arn`, `labeling_job_name`, and a `labeling_job_console_url`
deep-link. These ARNs are exactly what the Python `launch_labeling_job.py` launcher
wants if you ever launch from boto3 instead.

---

## Usage

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # edit: existing bucket, keys, workteam ARN

terraform init
terraform plan
terraform apply        # deploys IAM + 2 Lambdas AND launches the job

terraform output labeling_job_console_url      # open it in the console
terraform destroy                              # stops the job + tears down infra
```

---

## Optional: Comprehend converter Lambda

Setting **`source_docs_s3_base`** (non-empty) enables a third Lambda,
`${project}-comprehend-to-manifest` (`lambdas/comprehend_to_manifest/handler.py`),
plus its IAM role. It downloads a Comprehend `output.tar.gz`, unzips it, and writes
the GT input manifest (to `s3_bucket_name` / `manifest_s3_key`). All of this is
**`count`-gated** — when the variable is empty (default), none of it is created and
the core stack is untouched.

This Lambda is a **one-shot batch** step: one invoke parses the whole
`output.tar.gz` and writes the *complete* manifest in a single `PutObject` (nothing
per-case). Launching the labeling job stays a **separate, deliberate step**
(`terraform apply` / boto3 launcher) so you can review the manifest first — the
**per-case fan-out is Ground Truth's job at runtime**, where it invokes the
pre-annotation Lambda once per manifest record. Convert and launch are intentionally
decoupled.

Relevant variables:

| Variable | Purpose | Default |
|----------|---------|---------|
| `source_docs_s3_base` | S3 prefix of the original docs (builds `source-ref`); **enables the Lambda** | `""` (disabled) |
| `comprehend_output_bucket` | Bucket the Lambda reads `output.tar.gz` from | `""` → `s3_bucket_name` |
| `min_score` | Optional Comprehend score floor | `""` (keep all) |

**Triggering.** The stack adds an `aws_lambda_permission` letting S3 invoke the
function, but it does **not** create the notification — it only *references* the
bucket, and `aws_s3_bucket_notification` is authoritative (it would overwrite any
existing notifications). Attach the trigger yourself, e.g.:

```bash
# Manual invoke:
aws lambda invoke --function-name usdc-ner-comprehend-to-manifest \
    --payload '{"bucket":"my-bucket","key":".../output.tar.gz"}' out.json

# Or wire an S3 ObjectCreated notification / EventBridge rule to the function ARN
# (terraform output comprehend_to_manifest_lambda_arn).
```

The handler also accepts S3-notification and EventBridge event shapes, so either
trigger works once attached.

## Notes & caveats
- **No remote backend** → local `terraform.tfstate`. Add S3 + DynamoDB for teams.
- **`terraform validate`** requires registry access to download providers; if your
  environment blocks it, run `init`/`validate` where the registry is reachable.
- **CORS / encryption / versioning** on the bucket are not managed here — ensure the
  existing bucket allows `GET` (CORS) for the worker UI.
- **`MaxConcurrentTaskCount`** and the single-worker count are hard-coded in the
  template; promote them to variables if you need them configurable.
- Re-applying after editing the manifest's *content in S3* will **not** relaunch the
  job (the hash can't see S3 object content). Change a key/URI or the Lambda code, or
  taint `null_resource.labeling_job`, to force a new job.
