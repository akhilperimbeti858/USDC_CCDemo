"""boto3 launcher for the SageMaker Ground Truth custom NER labeling job.

This is the Python/boto3 equivalent of the Terraform ``null_resource`` that runs
``aws sagemaker create-labeling-job``. It references the input manifest + UI
template that already exist in S3 and calls ``sagemaker.create_labeling_job``.

It deliberately does NOT create IAM roles, Lambda functions, or upload any assets
— those are managed elsewhere (the Terraform stack, or by hand). The manifest and
UI template must already be present in the bucket at ``manifest_key`` /
``ui_template_key``. Pass the ARNs in via ``LabelingJobConfig``. This keeps the
launcher safe to run repeatedly and easy to unit-test (the client is injectable).
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class LabelingJobConfig:
    job_name: str
    region: str
    s3_bucket: str
    role_arn: str                       # Ground Truth execution role ARN
    workteam_arn: str                   # private workteam ARN
    pre_lambda_arn: str
    post_lambda_arn: str                # single-worker consolidation Lambda
    label_attribute_name: str = "ner-labels"
    manifest_key: str = "input/input.manifest"            # existing object in s3_bucket
    ui_template_key: str = "templates/ner-template.liquid.html"  # existing object in s3_bucket
    output_prefix: str = "output/"
    task_title: str = "Label named entities and confirm OFAC IDs"
    task_description: str = "Highlight each named entity and confirm an OFAC ID when prompted."
    task_keywords: List[str] = field(default_factory=lambda: ["NER", "OFAC"])
    task_time_limit_seconds: int = 3600
    task_availability_lifetime_seconds: int = 864000
    max_concurrent_task_count: int = 1000

    def s3_uri(self, key: str) -> str:
        return f"s3://{self.s3_bucket}/{key}"


def build_create_labeling_job_request(cfg: LabelingJobConfig) -> dict:
    """Build the kwargs for ``sagemaker.create_labeling_job`` (pure / testable)."""
    return {
        "LabelingJobName": cfg.job_name,
        "LabelAttributeName": cfg.label_attribute_name,
        "InputConfig": {
            "DataSource": {
                "S3DataSource": {"ManifestS3Uri": cfg.s3_uri(cfg.manifest_key)}
            }
        },
        "OutputConfig": {"S3OutputPath": cfg.s3_uri(cfg.output_prefix)},
        "RoleArn": cfg.role_arn,
        "HumanTaskConfig": {
            "WorkteamArn": cfg.workteam_arn,
            "UiConfig": {"UiTemplateS3Uri": cfg.s3_uri(cfg.ui_template_key)},
            "PreHumanTaskLambdaArn": cfg.pre_lambda_arn,
            "TaskKeywords": cfg.task_keywords,
            "TaskTitle": cfg.task_title,
            "TaskDescription": cfg.task_description,
            "NumberOfHumanWorkersPerDataObject": 1,
            "TaskTimeLimitInSeconds": cfg.task_time_limit_seconds,
            "TaskAvailabilityLifetimeInSeconds": cfg.task_availability_lifetime_seconds,
            "MaxConcurrentTaskCount": cfg.max_concurrent_task_count,
            "AnnotationConsolidationConfig": {
                "AnnotationConsolidationLambdaArn": cfg.post_lambda_arn
            },
        },
    }


def launch(cfg: LabelingJobConfig, sagemaker_client=None) -> dict:
    """Create the labeling job (assets must already be in S3). Returns the API response."""
    if sagemaker_client is None:
        import boto3

        sagemaker_client = boto3.client("sagemaker", region_name=cfg.region)
    return sagemaker_client.create_labeling_job(**build_create_labeling_job_request(cfg))


def describe(job_name: str, region: str, sagemaker_client=None) -> dict:
    """Return current status for a launched labeling job."""
    if sagemaker_client is None:
        import boto3

        sagemaker_client = boto3.client("sagemaker", region_name=region)
    resp = sagemaker_client.describe_labeling_job(LabelingJobName=job_name)
    return {
        "status": resp.get("LabelingJobStatus"),
        "counters": resp.get("LabelCounters"),
        "output": resp.get("LabelingJobOutput"),
    }
