# Python / boto3 NER Labeling Pipeline

A Python implementation of the same SageMaker Ground Truth custom NER workflow
defined in [`../terraform`](../terraform), focused on **local testability**.

The pre-annotation and consolidation logic are pure functions with **no AWS
dependency**, so you can run and unit-test the whole pipeline offline. `boto3` is
only needed when you actually launch a job. The launcher uploads nothing — the
manifest and UI template must already exist in S3.

## Layout

```
python_boto3/
├── ner_pipeline/
│   ├── pre_annotation.py        build_task_input()           (mirror of pre Lambda)
│   ├── consolidation.py         consolidate_single()         (mirror of post Lambda)
│   ├── comprehend_to_manifest.py  Comprehend output -> manifest records
│   ├── local_simulator.py       run the full flow offline
│   └── aws_launcher.py          boto3 create_labeling_job (references existing S3)
├── build_manifest_from_comprehend.py  CLI: Comprehend output -> input manifest
├── run_local_simulation.py      CLI: end-to-end offline simulation
├── launch_labeling_job.py       CLI: real boto3 launch (supports --dry-run)
├── sample_data/                 simulated worker answers (single-worker)
└── tests/test_pipeline.py
```

## Run the offline simulation

No install, no AWS, no credentials:

```bash
python run_local_simulation.py
```

It reads the shared manifest (`../manifests/input.manifest.example`) plus simulated
worker answers from `sample_data/`, runs pre-annotation + single-worker
consolidation, and prints the `taskInput`s and the consolidated output manifest
entries.

You can also drive it programmatically:

```python
from ner_pipeline.local_simulator import load_manifest, simulate

records = load_manifest("../manifests/input.manifest.example")
worker_answers = [[[{"label": "ORG", "startOffset": 0, "endOffset": 9}]]]  # [obj][worker][entities]
report = simulate(records[:1], worker_answers)
print(report["consolidated"])
```

## Launch a real labeling job

The launcher assumes the IAM role, the Lambdas, the S3 bucket, **and** the input
manifest + UI template already exist (from the Terraform stack or by hand) — it
uploads nothing. Pass the ARNs and object keys in:

```bash
pip install -r requirements.txt

# Preview the create-labeling-job request without calling AWS:
python launch_labeling_job.py --dry-run \
    --job-name usdc-ner-demo --bucket my-existing-bucket \
    --role-arn arn:aws:iam::ACCOUNT:role/usdc-ner-gt-execution-role \
    --workteam-arn arn:aws:sagemaker:us-east-1:ACCOUNT:workteam/private-crowd/my-team \
    --pre-lambda-arn  arn:aws:lambda:us-east-1:ACCOUNT:function:usdc-ner-SageMaker-pre-annotation \
    --post-lambda-arn arn:aws:lambda:us-east-1:ACCOUNT:function:usdc-ner-SageMaker-post-single

# Real launch (references the manifest/template already in S3, then creates the job):
python launch_labeling_job.py --job-name usdc-ner-demo --bucket my-existing-bucket ...
```

Useful flags: `--manifest-key`, `--ui-template-key`, `--label-attribute-name`,
`--region`.

## Tests

```bash
python tests/test_pipeline.py     # or: python -m pytest tests/
```

The launcher test injects a fake `boto3` client, so the full suite runs without
boto3 installed and without touching AWS.

## Relationship to Terraform

| Concern | Terraform | Python/boto3 |
|---------|-----------|--------------|
| IAM roles, Lambdas | created | assumed to exist (pass ARNs) |
| S3 bucket + manifest + template | referenced (must pre-exist) | referenced (must pre-exist) |
| Create labeling job | `null_resource` + AWS CLI | `aws_launcher.launch` (boto3) |
| Local testing of the logic | — | `local_simulator` + tests |

Use Terraform to stand up the durable infra; use this package to iterate on and
test the pre/post-annotation logic quickly, and optionally to launch jobs from
Python.
