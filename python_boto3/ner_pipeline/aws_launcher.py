"""boto3 launcher for the SageMaker Ground Truth custom NER labeling job.

This is the Python/boto3 equivalent of the Terraform ``null_resource`` that runs
``aws sagemaker create-labeling-job``. It uploads the input manifest + UI template
to S3 and calls ``sagemaker.create_labeling_job``.

It deliberately does NOT create IAM roles or Lambda functions — those are managed
by the Terraform stack (or can be created once by hand). Pass their ARNs in via
``LabelingJobConfig``. This keeps the launcher safe to run repeatedly and easy to
unit-test (every AWS client is injectable).
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class LabelingJobConfig:
    job_name: str
    region: str
    s3_bucket: str
    role_arn: str                       # Ground Truth execution role ARN
    workteam_arn: str                   # private workteam ARN
    pre_lambda_arn: str
    post_lambda_arn: str                # selected single/merge consolidation Lambda
    label_attribute_name: str = "ner-labels"
    manifest_local_path: str = "../manifests/input.manifest.example"
    ui_template_local_path: str = "../ui/ner-template.liquid.html"
    manifest_key: str = "input/input.manifest"
    ui_template_key: str = "templates/ner-template.liquid.html"
    output_prefix: str = "output/"
    workers_per_object: int = 1
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
            "NumberOfHumanWorkersPerDataObject": cfg.workers_per_object,
            "TaskTimeLimitInSeconds": cfg.task_time_limit_seconds,
            "TaskAvailabilityLifetimeInSeconds": cfg.task_availability_lifetime_seconds,
            "MaxConcurrentTaskCount": cfg.max_concurrent_task_count,
            "AnnotationConsolidationConfig": {
                "AnnotationConsolidationLambdaArn": cfg.post_lambda_arn
            },
        },
    }


def upload_assets(cfg: LabelingJobConfig, s3_client=None) -> None:
    """Upload the input manifest and UI template to S3."""
    if s3_client is None:
        import boto3  # imported lazily so the module loads/tests without boto3

        s3_client = boto3.client("s3", region_name=cfg.region)
    s3_client.upload_file(
        cfg.manifest_local_path, cfg.s3_bucket, cfg.manifest_key,
        ExtraArgs={"ContentType": "application/json"},
    )
    s3_client.upload_file(
        cfg.ui_template_local_path, cfg.s3_bucket, cfg.ui_template_key,
        ExtraArgs={"ContentType": "text/html"},
    )


def launch(cfg: LabelingJobConfig, sagemaker_client=None, s3_client=None,
           upload: bool = True) -> dict:
    """Upload assets (optional) and create the labeling job. Returns the API response."""
    if upload:
        upload_assets(cfg, s3_client=s3_client)
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
