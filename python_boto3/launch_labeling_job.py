#!/usr/bin/env python3
"""Launch the SageMaker Ground Truth NER labeling job via boto3.

This is the Python/boto3 alternative to `terraform apply` for *just the job*.
It assumes the IAM role, the two Lambdas, and the S3 bucket already exist (created
by the Terraform stack or by hand) and you pass their identifiers in as flags.

Dry run (prints the request, no AWS calls):
    python launch_labeling_job.py --dry-run \
        --job-name usdc-ner-demo --bucket my-bucket \
        --role-arn arn:... --workteam-arn arn:... \
        --pre-lambda-arn arn:... --post-lambda-arn arn:...

Real launch (requires AWS creds + boto3):
    python launch_labeling_job.py --job-name usdc-ner-demo --bucket my-bucket ...
"""

import argparse
import json
import os

from ner_pipeline.aws_launcher import (
    LabelingJobConfig,
    build_create_labeling_job_request,
    launch,
)

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job-name", required=True)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--role-arn", required=True)
    ap.add_argument("--workteam-arn", required=True)
    ap.add_argument("--pre-lambda-arn", required=True)
    ap.add_argument("--post-lambda-arn", required=True)
    ap.add_argument("--label-attribute-name", default="ner-labels")
    ap.add_argument("--workers-per-object", type=int, default=1)
    ap.add_argument("--manifest", default=os.path.join(HERE, "..", "manifests", "input.manifest.example"))
    ap.add_argument("--ui-template", default=os.path.join(HERE, "..", "ui", "ner-template.liquid.html"))
    ap.add_argument("--no-upload", action="store_true", help="Skip uploading manifest/template to S3.")
    ap.add_argument("--dry-run", action="store_true", help="Print the request and exit (no AWS calls).")
    args = ap.parse_args()

    cfg = LabelingJobConfig(
        job_name=args.job_name,
        region=args.region,
        s3_bucket=args.bucket,
        role_arn=args.role_arn,
        workteam_arn=args.workteam_arn,
        pre_lambda_arn=args.pre_lambda_arn,
        post_lambda_arn=args.post_lambda_arn,
        label_attribute_name=args.label_attribute_name,
        workers_per_object=args.workers_per_object,
        manifest_local_path=args.manifest,
        ui_template_local_path=args.ui_template,
    )

    if args.dry_run:
        print(json.dumps(build_create_labeling_job_request(cfg), indent=2))
        return

    resp = launch(cfg, upload=not args.no_upload)
    print("Created labeling job:", resp.get("LabelingJobArn"))


if __name__ == "__main__":
    main()
