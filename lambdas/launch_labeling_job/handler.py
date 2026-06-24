"""Lambda: launch a SageMaker Ground Truth labeling job when the manifest lands.

WHAT THIS DOES
--------------
This is the event-driven launcher in the handoff pipeline:

    Comprehend output.tar.gz  --(EventBridge)-->  comprehend_to_manifest Lambda
        --> writes input/input.manifest  --(EventBridge)-->  THIS Lambda
        --> sagemaker.create_labeling_job(...)

It is triggered by an EventBridge "Object Created" rule that matches the input
manifest object. When it fires, it builds a ``create-labeling-job`` request from
its environment configuration and creates a fresh, uniquely-named labeling job
that points at the manifest the converter just wrote.

It deliberately creates NOTHING else: no IAM, no Lambdas, no S3 assets. The
execution role, workteam, UI template, and the pre/post Lambdas all already exist
(deployed by the same Terraform stack) and are passed in as environment variables.

WHY A LAMBDA INSTEAD OF A null_resource
---------------------------------------
The manifest is produced asynchronously (after the external Comprehend job
finishes and the converter runs), so it does not exist at ``terraform apply``
time. An event-driven launcher removes that chicken-and-egg problem: the job is
created only once the manifest actually exists.

CONFIGURATION (environment variables)
-------------------------------------
  ROLE_ARN              (required)  Ground Truth execution role ARN.
  WORKTEAM_ARN          (required)  Private workteam ARN.
  PRE_LAMBDA_ARN        (required)  Pre-annotation (PreHumanTask) Lambda ARN.
  POST_LAMBDA_ARN       (required)  Annotation-consolidation Lambda ARN.
  MANIFEST_S3_URI       (required)  s3:// URI of the input manifest.
  UI_TEMPLATE_S3_URI    (required)  s3:// URI of the Crowd-HTML UI template.
  OUTPUT_S3_URI         (required)  s3:// output path for the job.
  LABEL_ATTRIBUTE_NAME  (optional)  Output manifest key. Default ``ner-labels``.
  JOB_NAME_PREFIX       (optional)  Prefix for the (unique) job name. Default ``usdc-ner``.
  TASK_TITLE / TASK_DESCRIPTION       (optional)  Shown to workers.
  TASK_KEYWORDS                       (optional)  JSON array. Default ["NER","OFAC"].
  TASK_TIME_LIMIT_SECONDS             (optional)  Default 3600.
  TASK_AVAILABILITY_LIFETIME_SECONDS  (optional)  Default 864000.
  MAX_CONCURRENT_TASK_COUNT           (optional)  Default 1000.
  WORKERS_PER_OBJECT                  (optional)  Default 1 (single-worker workflow).

The request shape mirrors python_boto3/ner_pipeline/aws_launcher.py. Lambdas are
zipped per-directory, so that module cannot be imported here; the small builder is
duplicated below (the same handler/mirror pattern used elsewhere in this repo).
"""

import json
import os
import time


# Defaults kept in one place so both the handler and tests agree.
_DEFAULT_KEYWORDS = ["NER", "OFAC"]


def _env(name, default=None, required=False):
    """Read an environment variable, optionally enforcing presence."""
    val = os.environ.get(name)
    if required and (val is None or val == ""):
        raise KeyError(f"Missing required environment variable: {name}")
    return val if (val is not None and val != "") else default


def _keywords():
    """Parse TASK_KEYWORDS (JSON array); fall back to the default list."""
    raw = os.environ.get("TASK_KEYWORDS")
    if not raw:
        return list(_DEFAULT_KEYWORDS)
    try:
        kw = json.loads(raw)
        return kw if isinstance(kw, list) and kw else list(_DEFAULT_KEYWORDS)
    except (ValueError, TypeError):
        return list(_DEFAULT_KEYWORDS)


def _job_name(prefix, now=None):
    """A unique, immutable job name: ``<prefix>-<UTC timestamp>``.

    Ground Truth job names must be unique; a new analysis run -> new manifest ->
    new job. ``now`` is injectable so tests are deterministic.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now))
    return f"{prefix}-{stamp}"


def build_create_labeling_job_request(job_name):
    """Build the kwargs for ``sagemaker.create_labeling_job`` from env (pure/testable)."""
    return {
        "LabelingJobName": job_name,
        "LabelAttributeName": _env("LABEL_ATTRIBUTE_NAME", "ner-labels"),
        "InputConfig": {
            "DataSource": {
                "S3DataSource": {"ManifestS3Uri": _env("MANIFEST_S3_URI", required=True)}
            }
        },
        "OutputConfig": {"S3OutputPath": _env("OUTPUT_S3_URI", required=True)},
        "RoleArn": _env("ROLE_ARN", required=True),
        "HumanTaskConfig": {
            "WorkteamArn": _env("WORKTEAM_ARN", required=True),
            "UiConfig": {"UiTemplateS3Uri": _env("UI_TEMPLATE_S3_URI", required=True)},
            "PreHumanTaskLambdaArn": _env("PRE_LAMBDA_ARN", required=True),
            "TaskKeywords": _keywords(),
            "TaskTitle": _env("TASK_TITLE", "Label named entities and confirm OFAC IDs"),
            "TaskDescription": _env(
                "TASK_DESCRIPTION",
                "Highlight each named entity, assign its type, and confirm an OFAC ID when prompted.",
            ),
            "NumberOfHumanWorkersPerDataObject": int(_env("WORKERS_PER_OBJECT", "1")),
            "TaskTimeLimitInSeconds": int(_env("TASK_TIME_LIMIT_SECONDS", "3600")),
            "TaskAvailabilityLifetimeInSeconds": int(_env("TASK_AVAILABILITY_LIFETIME_SECONDS", "864000")),
            "MaxConcurrentTaskCount": int(_env("MAX_CONCURRENT_TASK_COUNT", "1000")),
            "AnnotationConsolidationConfig": {
                "AnnotationConsolidationLambdaArn": _env("POST_LAMBDA_ARN", required=True)
            },
        },
    }


# Module-level so unit tests can monkeypatch it (no boto3 needed in tests).
def _create_job(request):
    """Call sagemaker.create_labeling_job; returns the API response."""
    import boto3  # provided by the Lambda runtime; imported lazily for testability

    return boto3.client("sagemaker").create_labeling_job(**request)


def lambda_handler(event, context):
    # The event (an EventBridge "Object Created" for the manifest) is only the
    # trigger; all job configuration comes from the environment, so the job is
    # built deterministically regardless of which event shape delivered it.
    job_name = _job_name(_env("JOB_NAME_PREFIX", "usdc-ner"))
    request = build_create_labeling_job_request(job_name)
    _create_job(request)

    return {
        "labeling_job_name": job_name,
        "manifest_s3_uri": request["InputConfig"]["DataSource"]["S3DataSource"]["ManifestS3Uri"],
        "output_s3_path": request["OutputConfig"]["S3OutputPath"],
    }
