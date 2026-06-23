# Python / boto3 NER Labeling Pipeline

A Python implementation of the same SageMaker Ground Truth custom NER workflow
defined in [`../terraform`](../terraform), focused on **local testability**.

The pre-annotation and consolidation logic are pure functions with **no AWS
dependency**, so you can run and unit-test the whole pipeline offline. `boto3` is
only needed when you actually launch a job or upload assets to S3.

## Layout

```
python_boto3/
├── ner_pipeline/
│   ├── pre_annotation.py        build_task_input()           (mirror of pre Lambda)
│   ├── consolidation.py         consolidate_single/merge()   (mirror of post Lambdas)
│   ├── comprehend_to_manifest.py  Comprehend output -> manifest records
│   ├── local_simulator.py       run the full flow offline
│   └── aws_launcher.py          boto3 create_labeling_job + S3 upload
├── build_manifest_from_comprehend.py  CLI: Comprehend output -> input manifest
├── run_local_simulation.py      CLI: end-to-end offline simulation
├── launch_labeling_job.py       CLI: real boto3 launch (supports --dry-run)
├── sample_data/                 simulated worker answers (single + merge)
└── tests/test_pipeline.py
```

## Run the offline simulation

No install, no AWS, no credentials:

```bash
python run_local_simulation.py --mode single
python run_local_simulation.py --mode merge
```

It reads the shared manifest (`../manifests/input.manifest.example`) plus simulated
worker answers from `sample_data/`, runs pre-annotation + consolidation, and prints
the `taskInput`s and the consolidated output manifest entries.

You can also drive it programmatically:

```python
from ner_pipeline.local_simulator import load_manifest, simulate

records = load_manifest("../manifests/input.manifest.example")
worker_answers = [[[{"label": "ORG", "startOffset": 0, "endOffset": 9}]]]  # [obj][worker][entities]
report = simulate(records[:1], worker_answers, mode="single")
print(report["consolidated"])
```

## Launch a real labeling job

The launcher assumes the IAM role, the two Lambdas, and the S3 bucket already
exist (from the Terraform stack or created by hand) — pass their ARNs in:

```bash
pip install -r requirements.txt

# Preview the create-labeling-job request without calling AWS:
python launch_labeling_job.py --dry-run \
    --job-name usdc-ner-demo --bucket my-bucket \
    --role-arn arn:aws:iam::ACCOUNT:role/usdc-ner-gt-execution-role \
    --workteam-arn arn:aws:sagemaker:us-east-1:ACCOUNT:workteam/private-crowd/my-team \
    --pre-lambda-arn  arn:aws:lambda:us-east-1:ACCOUNT:function:usdc-ner-SageMaker-pre-annotation \
    --post-lambda-arn arn:aws:lambda:us-east-1:ACCOUNT:function:usdc-ner-SageMaker-post-single

# Real launch (uploads manifest + template, then creates the job):
python launch_labeling_job.py --job-name usdc-ner-demo --bucket my-bucket ...
```

Useful flags: `--workers-per-object N`, `--label-attribute-name`, `--no-upload`
(skip uploading assets), `--region`.

## Tests

```bash
python tests/test_pipeline.py     # or: python -m pytest tests/
```

The launcher tests inject fake `boto3` clients, so the full suite runs without
boto3 installed and without touching AWS.

## Relationship to Terraform

| Concern | Terraform | Python/boto3 |
|---------|-----------|--------------|
| IAM roles, Lambdas, S3 bucket | created | assumed to exist (pass ARNs) |
| Upload manifest + UI template | `aws_s3_object` | `aws_launcher.upload_assets` |
| Create labeling job | `null_resource` + AWS CLI | `aws_launcher.launch` (boto3) |
| Local testing of the logic | — | `local_simulator` + tests |

Use Terraform to stand up the durable infra; use this package to iterate on and
test the pre/post-annotation logic quickly, and optionally to launch jobs from
Python.
