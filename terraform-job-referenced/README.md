# Terraform — launch a labeling job against **referenced** (shared) Lambdas

This is a thin, job-only variant of [`../terraform`](../terraform). It is meant for
the case where the **pre/post-annotation Lambdas are shared across many labeling
jobs**: you deploy those Lambdas **once** (separately), and each job stack here just
**references** them by ARN and launches a job.

## What it creates vs. references

**Creates**
- A Ground Truth **execution role** (`${project_name}-gt-execution-role`) with S3
  read/write on the existing bucket and `lambda:InvokeFunction` scoped to the two
  referenced Lambda ARNs.
- The **labeling job** itself (via the AWS CLI; Terraform has no native resource).

**References (does NOT create)**
- The **pre-annotation** and **post-annotation (consolidation) Lambdas** — passed in
  as `pre_annotation_lambda_arn` / `post_annotation_lambda_arn`.
- The **S3 bucket**, the input manifest, and the UI template (must already exist).
- The **private workteam**.

Compared to `../terraform`, this stack drops: the `archive_file` packaging, both
Lambda functions, their execution roles, the SageMaker invoke permissions
(`aws_lambda_permission`), and the optional Comprehend converter Lambda.

## Prerequisites for the referenced Lambdas

Because the job assumes the execution role and Ground Truth invokes the Lambdas, the
already-deployed Lambdas must satisfy two contracts:

1. **Name contains a `SageMaker` token** — the `AmazonSageMakerGroundTruthExecution`
   managed policy only permits invoking Lambdas whose names contain a recognized
   token (e.g. `usdc-ner-SageMaker-pre-annotation`).
2. **Resource-based policy allows `sagemaker.amazonaws.com` to invoke** — i.e. an
   `aws_lambda_permission` on each function. The `../terraform` stack (and any proper
   Lambda-deploy stack) already adds this when it creates the functions.

The `../terraform` stack creates exactly such Lambdas and prints their ARNs as
`pre_annotation_lambda_arn` / `post_single_lambda_arn` outputs — feed those in here.

## Usage

```bash
# 1. Deploy the shared Lambdas ONCE (e.g. via ../terraform, or your own stack).
#    Grab their ARNs (terraform output pre_annotation_lambda_arn / post_single_lambda_arn).

# 2. Launch a job that references them:
cd terraform-job-referenced
cp terraform.tfvars.example terraform.tfvars
#   edit: s3_bucket_name (existing), private_workteam_arn,
#         pre_annotation_lambda_arn, post_annotation_lambda_arn

terraform init
terraform plan
terraform apply        # creates the role + launches the job (no Lambdas built)

terraform output labeling_job_console_url   # open the job in the console
terraform destroy                           # stops the job + removes the role (Lambdas untouched)
```

Launch **another** job against the **same** Lambdas by re-applying with a different
manifest key / config (give it a distinct `project_name` so the execution role name
and job name don't collide), or run a separate working directory / workspace.

> Requires **AWS CLI v2** + credentials on the machine running `apply` (the launch
> step shells out to it). The referenced Lambdas must be in the same `aws_region`.

## Key variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `s3_bucket_name` | Existing bucket with the assets | _(required)_ |
| `private_workteam_arn` | Existing private workteam ARN | _(required)_ |
| `pre_annotation_lambda_arn` | **Referenced** pre-annotation Lambda ARN | _(required)_ |
| `post_annotation_lambda_arn` | **Referenced** consolidation Lambda ARN | _(required)_ |
| `manifest_s3_key` | Existing manifest object key | `input/input.manifest` |
| `ui_template_s3_key` | Existing UI template object key | `templates/ner-template.liquid.html` |
| `output_s3_prefix` | Output prefix in the bucket | `output/` |
| `label_attribute_name` | Output manifest key | `ner-labels` |
| `project_name` | Prefix for role + job name (keep unique per stack) | `usdc-ner` |
| `aws_region` | Region (must match the Lambdas) | `us-east-1` |

`entity_labels` is intentionally absent here: it configured the pre-annotation
Lambda at **creation** time, which this stack does not do. The label set now lives
with the referenced Lambda (its env var) and/or the manifest records.
