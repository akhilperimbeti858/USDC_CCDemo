# Terraform — self-contained, event-driven NER labeling pipeline (handoff stack)

A **single, admin-applyable** Terraform stack. It deploys everything needed to turn
an AWS Comprehend analysis result into a launched SageMaker Ground Truth labeling
job, **automatically**. The **only file you edit is `terraform.tfvars`**.

## The pipeline (fully automated after one apply)

```
[ external Comprehend entity-detection job ]   <-- you run this; NOT created here
        │ writes output.tar.gz into the bucket
        ▼  EventBridge: Object Created (suffix output.tar.gz)
comprehend_to_manifest Lambda ──► writes input/input.manifest
        ▼  EventBridge: Object Created (key input/input.manifest)
launch_labeling_job Lambda ──► sagemaker.create_labeling_job
        ▼
Ground Truth job ──► pre-annotation Lambda (per object) ──► worker UI ──► consolidation ──► output/
```

No manifest needs to exist at apply time — the launcher Lambda creates the job only
once the manifest actually lands. Each new `output.tar.gz` ⇒ new manifest ⇒ a new,
uniquely-named labeling job (`<job_name_prefix>-<UTC timestamp>`).

## What it creates vs. requires

**Creates:** the Ground Truth execution role; four Lambdas (pre-annotation,
consolidation, Comprehend→manifest converter, job launcher) + their IAM roles; the
two EventBridge rules; the Lambda invoke permissions; (optionally) uploads the UI
template into the bucket.

**Requires to already exist (NOT created here):**
- the **S3 bucket** (`s3_bucket_name`) — referenced, not created;
- a **private workteam** (`private_workteam_arn`);
- the external **Comprehend analysis job** that produces `output.tar.gz`.

## ⚠️ One prerequisite: EventBridge on the bucket

The links are S3 **Object Created** events, which require **EventBridge notifications
enabled on the bucket**. Two ways:

- **Recommended:** enable it out-of-band once (S3 console → bucket → *Properties* →
  *Amazon EventBridge* → *On*, or `aws s3api put-bucket-notification-configuration`),
  and keep `manage_bucket_eventbridge = false`.
- **Or** set `manage_bucket_eventbridge = true` to have this stack enable it — but
  note `aws_s3_bucket_notification` is **authoritative** and will **overwrite** other
  notification configuration on a bucket this stack does not own.

## Usage

```bash
cd terraform-handoff
cp terraform.tfvars.example terraform.tfvars
#   edit terraform.tfvars: s3_bucket_name, private_workteam_arn, source_docs_s3_base
#   (everything else has a default)

terraform init
terraform plan
terraform apply        # deploys the whole pipeline; launches nothing yet

# Then, whenever your Comprehend job drops output.tar.gz in the bucket, the
# manifest is built and a labeling job launches automatically.
terraform output labeling_jobs_console_url
```

> Requires AWS credentials with permission to create the above. AWS CLI is **not**
> needed (the launch happens inside a Lambda, not via local-exec).

## Key variables (full list + defaults in `variables.tf`)

| Variable | Purpose | Default |
|----------|---------|---------|
| `s3_bucket_name` | Existing bucket (referenced) | _(required)_ |
| `private_workteam_arn` | Existing private workteam ARN | _(required)_ |
| `source_docs_s3_base` | Prefix of original docs → `source-ref` | _(required)_ |
| `manifest_s3_key` | Manifest object key (trigger + job input) | `input/input.manifest` |
| `ui_template_s3_key` | UI template key | `templates/ner-template.liquid.html` |
| `comprehend_output_suffix` | Key suffix that triggers the converter | `output.tar.gz` |
| `comprehend_output_key_prefix` | Optional prefix to scope that trigger | `""` |
| `entity_labels` | Kept entity types / label set | `["OFAC_ORG","OFAC_POI","FTO"]` |
| `min_score` | Optional Comprehend score floor | `""` (keep all) |
| `upload_ui_template` | Upload `../ui/ner-template.liquid.html` | `true` |
| `manage_bucket_eventbridge` | Stack enables bucket EventBridge (authoritative) | `false` |
| `label_attribute_name` | Output manifest key | `ner-labels` |
| `task_*`, `max_concurrent_task_count`, `workers_per_object` | Job task settings | see `variables.tf` |
| `lambda_runtime`, `*_timeout_seconds`, `converter_lambda_memory_mb` | Lambda knobs | see `variables.tf` |

## Notes

- **Re-runs:** drop a new `output.tar.gz` (or re-run Comprehend) → a fresh job. The
  manifest at `manifest_s3_key` is overwritten each time; job names stay unique.
- **No loop:** the converter writes only the manifest; Ground Truth writes only under
  `output/`. Neither re-matches the trigger rules.
- **Single-worker** consolidation (`workers_per_object = 1`), matching the bundled
  post-annotation Lambda.
- This stack is the create-everything counterpart to
  [`../terraform-job-referenced`](../terraform-job-referenced) (which instead
  references already-deployed, shared Lambdas).
