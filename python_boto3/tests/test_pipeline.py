"""Tests for the Python/boto3 NER pipeline.

Run:  cd python_boto3 && python -m pytest tests/   (or: python tests/test_pipeline.py)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ner_pipeline.pre_annotation import build_task_input
from ner_pipeline.consolidation import consolidate_single, consolidate_merge
from ner_pipeline.local_simulator import load_manifest, simulate
from ner_pipeline.aws_launcher import LabelingJobConfig, build_create_labeling_job_request, launch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


# --- pre-annotation --------------------------------------------------------
def test_build_task_input_inline_and_ofac():
    ti = build_task_input({
        "source": "Acme Corp ",
        "labels": {"labels": [{"label": "PERSON"}, {"label": "ORG"}]},
        "initialEntities": [],
        "ofac_metadata": [{"startOffset": 0, "endOffset": 4, "ofacId": "SDN-1"}],
    })
    assert ti["taskObject"] == "Acme Corp"          # rstripped
    assert ti["ofacMetadata"][0]["ofacId"] == "SDN-1"
    assert ti["initialValue"] == []
    assert {"label": "ORG"} in ti["labels"]         # per-record label config


def test_build_task_input_accepts_legacy_field_names():
    ti = build_task_input({"source": "x", "ofacMetadata": [{"startOffset": 0, "endOffset": 1, "ofacId": "SDN-9"}],
                           "initialValue": [{"label": "ORG", "startOffset": 0, "endOffset": 1}]})
    assert ti["ofacMetadata"][0]["ofacId"] == "SDN-9"
    assert ti["initialValue"][0]["label"] == "ORG"


def test_build_task_input_source_ref_uses_reader():
    ti = build_task_input({"source-ref": "s3://b/k"}, source_ref_reader=lambda uri: "from s3")
    assert ti["taskObject"] == "from s3"


# --- consolidation ---------------------------------------------------------
def _worker(wid, ents):
    return {"workerId": wid, "annotationData": {"content": json.dumps({"annotatedResult": {"entities": ents}})}}


def test_consolidate_single_reattaches_ofac():
    objs = [{
        "datasetObjectId": "0",
        "dataObject": {"ofacMetadata": [{"startOffset": 0, "endOffset": 4, "ofacId": "SDN-1"}]},
        "annotations": [_worker("w1", [{"label": "ORG", "startOffset": 0, "endOffset": 4}])],
    }]
    out = consolidate_single(objs, "ner-labels")
    ent = out[0]["consolidatedAnnotation"]["content"]["ner-labels"]["entities"][0]
    assert ent["ofacId"] == "SDN-1"


def _worker_with_overrides(wid, ents, overrides):
    return {"workerId": wid, "annotationData": {"content": json.dumps({
        "annotatedResult": {"entities": ents},
        "ofacOverrides": json.dumps(overrides),
    })}}


def test_consolidate_single_carries_entered_ofac_id():
    # New span with no manifest OFAC record; annotator-entered ID must pass through.
    objs = [{
        "datasetObjectId": "0",
        "dataObject": {},
        "annotations": [_worker_with_overrides(
            "w1",
            [{"label": "ORG", "startOffset": 70, "endOffset": 78}],
            [{"startOffset": 70, "endOffset": 78, "ofacId": "SDN-NEW"}],
        )],
    }]
    out = consolidate_single(objs, "ner-labels")
    ent = out[0]["consolidatedAnnotation"]["content"]["ner-labels"]["entities"][0]
    assert ent["ofacId"] == "SDN-NEW"


def test_consolidate_merge_carries_entered_ofac_id_no_voting():
    objs = [{
        "datasetObjectId": "0",
        "dataObject": {},
        "annotations": [
            _worker_with_overrides(
                "w1",
                [{"label": "ORG", "startOffset": 0, "endOffset": 4}],
                [{"startOffset": 0, "endOffset": 4, "ofacId": "SDN-NEW"}],
            ),
            _worker("w2", [{"label": "ORG", "startOffset": 0, "endOffset": 4}]),
        ],
    }]
    out = consolidate_merge(objs, "ner-labels")
    ent = out[0]["consolidatedAnnotation"]["content"]["ner-labels"]["entities"][0]
    assert ent["ofacId"] == "SDN-NEW"


def test_consolidate_merge_majority_and_threshold():
    objs = [{
        "datasetObjectId": "0",
        "dataObject": {},
        "annotations": [
            _worker("w1", [{"label": "ORG", "startOffset": 0, "endOffset": 4, "ofacId": "SDN-1"},
                           {"label": "LOC", "startOffset": 13, "endOffset": 19}]),
            _worker("w2", [{"label": "ORG", "startOffset": 0, "endOffset": 4, "ofacId": "SDN-1"}]),
            _worker("w3", [{"label": "PERSON", "startOffset": 0, "endOffset": 4}]),
        ],
    }]
    out = consolidate_merge(objs, "ner-labels")
    ents = out[0]["consolidatedAnnotation"]["content"]["ner-labels"]["entities"]
    assert [(e["label"], e["startOffset"]) for e in ents] == [("ORG", 0)]  # LOC dropped (1/3)
    assert ents[0]["ofacId"] == "SDN-1"
    assert ents[0]["confidence"] == 1.0


# --- local simulator (uses the real shared manifest) -----------------------
def test_simulate_end_to_end_single():
    records = load_manifest(os.path.join(ROOT, "manifests", "input.manifest.example"))
    with open(os.path.join(HERE, "..", "sample_data", "worker_answers.single.json")) as f:
        answers = json.load(f)
    report = simulate(records, answers, mode="single")
    assert len(report["consolidated"]) == len(records)
    # First doc: ORG@0-9 should carry the manifest's OFAC id.
    first = report["consolidated"][0]["consolidatedAnnotation"]["content"]["ner-labels"]["entities"]
    assert any(e.get("ofacId") == "SDN-12345" for e in first)


def test_simulate_end_to_end_merge():
    records = load_manifest(os.path.join(ROOT, "manifests", "input.manifest.example"))
    with open(os.path.join(HERE, "..", "sample_data", "worker_answers.merge.json")) as f:
        answers = json.load(f)
    report = simulate(records, answers, mode="merge")
    ents = report["consolidated"][0]["consolidatedAnnotation"]["content"]["ner-labels"]["entities"]
    # 2/3 agree on ORG@0-9 -> kept; the lone PERSON loses the vote.
    assert any(e["label"] == "ORG" and e["startOffset"] == 0 for e in ents)


# --- aws launcher (request building + injected clients, no real AWS) -------
def _cfg():
    return LabelingJobConfig(
        job_name="job", region="us-east-1", s3_bucket="bucket",
        role_arn="arn:role", workteam_arn="arn:team",
        pre_lambda_arn="arn:pre", post_lambda_arn="arn:post",
    )


def test_build_request_shape():
    req = build_create_labeling_job_request(_cfg())
    assert req["HumanTaskConfig"]["UiConfig"]["UiTemplateS3Uri"] == "s3://bucket/templates/ner-template.liquid.html"
    assert req["HumanTaskConfig"]["PreHumanTaskLambdaArn"] == "arn:pre"
    assert req["HumanTaskConfig"]["AnnotationConsolidationConfig"]["AnnotationConsolidationLambdaArn"] == "arn:post"
    assert req["InputConfig"]["DataSource"]["S3DataSource"]["ManifestS3Uri"] == "s3://bucket/input/input.manifest"


def test_launch_with_injected_clients():
    calls = {}

    class FakeS3:
        def upload_file(self, path, bucket, key, ExtraArgs=None):
            calls.setdefault("uploads", []).append(key)

    class FakeSM:
        def create_labeling_job(self, **kwargs):
            calls["create"] = kwargs
            return {"LabelingJobArn": "arn:aws:sagemaker:...:labeling-job/job"}

    resp = launch(_cfg(), sagemaker_client=FakeSM(), s3_client=FakeS3(), upload=True)
    assert resp["LabelingJobArn"].endswith("labeling-job/job")
    assert "input/input.manifest" in calls["uploads"]
    assert "templates/ner-template.liquid.html" in calls["uploads"]
    assert calls["create"]["LabelingJobName"] == "job"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t(); print(f"PASS {t.__name__}")
        except AssertionError as e:
            failures += 1; print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    raise SystemExit(1 if _run_all() else 0)
