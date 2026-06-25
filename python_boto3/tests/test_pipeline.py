"""Tests for the Python/boto3 NER pipeline.

Run:  cd python_boto3 && python -m pytest tests/   (or: python tests/test_pipeline.py)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ner_pipeline.pre_annotation import build_task_input
from ner_pipeline.consolidation import consolidate_single
from ner_pipeline.local_simulator import load_manifest, simulate
from ner_pipeline.aws_launcher import LabelingJobConfig, build_create_labeling_job_request, launch
from ner_pipeline.comprehend_to_manifest import (
    comprehend_doc_to_record, comprehend_to_records, extract_comprehend_docs, OFAC_LABELS,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


# --- pre-annotation --------------------------------------------------------
def test_build_task_input_inline_and_metadata():
    ti = build_task_input({
        "source": "Acme Corp ",
        "labels": {"labels": [{"label": "PERSON"}, {"label": "ORG"}]},
        "initialEntities": [{"startOffset": 0, "endOffset": 4, "label": "ORG"}],
        "metaData": [{"startOffset": 0, "endOffset": 4, "confidence": 0.99, "ofacID": "FILL"}],
    })
    assert ti["taskObject"] == "Acme Corp"          # rstripped
    assert ti["metaData"][0]["confidence"] == 0.99
    assert ti["metaData"][0]["ofacID"] == "FILL"
    assert ti["initialEntities"][0]["label"] == "ORG"
    assert {"label": "ORG"} in ti["labels"]         # per-record label config


def test_build_task_input_ignores_legacy_field_names():
    ti = build_task_input({"source": "x", "ofacMetadata": [{"startOffset": 0, "endOffset": 1, "ofacId": "SDN-9"}],
                           "initialValue": [{"label": "ORG", "startOffset": 0, "endOffset": 1}]})
    assert ti["metaData"] == []
    assert ti["initialEntities"] == []


def test_build_task_input_source_ref_uses_reader():
    ti = build_task_input({"source-ref": "s3://b/k"}, source_ref_reader=lambda uri: "from s3")
    assert ti["taskObject"] == "from s3"


# --- comprehend -> manifest converter --------------------------------------
def _comprehend_doc():
    return {
        "File": "doc1.txt",
        "Entities": [
            {"Score": 0.99, "Type": "OFAC_ORG", "Text": "Acme Corp", "BeginOffset": 0, "EndOffset": 9},
            {"Score": 0.97, "Type": "FTO", "Text": "Tehran", "BeginOffset": 46, "EndOffset": 52},
            {"Score": 0.88, "Type": "DATE", "Text": "last March", "BeginOffset": 53, "EndOffset": 63},
        ],
    }


def test_comprehend_maps_offsets_labels_and_drops_unknown_types():
    rec = comprehend_doc_to_record(_comprehend_doc(), "s3://bucket/docs/")
    # source-ref built from base + File (single slash).
    assert rec["source-ref"] == "s3://bucket/docs/doc1.txt"
    # OFAC types kept and mapped; DATE dropped.
    assert rec["initialEntities"] == [
        {"startOffset": 0, "endOffset": 9, "label": "OFAC_ORG"},
        {"startOffset": 46, "endOffset": 52, "label": "FTO"},
    ]
    # Parallel metaData: confidence from Score, ofacID placeholder for the annotator.
    assert rec["metaData"] == [
        {"startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "FILL"},
        {"startOffset": 46, "endOffset": 52, "confidence": 0.97, "ofacID": "FILL"},
    ]
    assert rec["labels"] == {"labels": [{"label": l} for l in OFAC_LABELS]}


def test_comprehend_min_score_filters_low_confidence():
    rec = comprehend_doc_to_record(_comprehend_doc(), "s3://bucket/docs", min_score=0.98)
    # Only the 0.99 OFAC_ORG survives (FTO 0.97 dropped; DATE not an OFAC type).
    assert [e["label"] for e in rec["initialEntities"]] == ["OFAC_ORG"]


def _make_tar_gz(docs):
    """Build an in-memory Comprehend-style output.tar.gz (one JSON line per doc)."""
    import io
    import tarfile
    body = "".join(json.dumps(d) + "\n" for d in docs).encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="output")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def test_extract_comprehend_docs_parses_tar_gz():
    docs = [
        {"File": "doc1.txt", "Line": 0, "Entities": [
            {"Score": 0.99, "Type": "OFAC_ORG", "Text": "Acme", "BeginOffset": 0, "EndOffset": 4}]},
        {"File": "doc2.txt", "Line": 0, "Entities": []},
    ]
    out = extract_comprehend_docs(_make_tar_gz(docs))
    assert [d["File"] for d in out] == ["doc1.txt", "doc2.txt"]
    assert out[0]["Entities"][0]["Type"] == "OFAC_ORG"
    assert out[1]["Entities"] == []


def test_comprehend_record_round_trips_through_build_task_input():
    rec = comprehend_to_records([_comprehend_doc()], "s3://bucket/docs/")[0]
    ti = build_task_input(rec, source_ref_reader=lambda uri: "Acme Corp wired funds to Tehran.")
    assert ti["taskObject"] == "Acme Corp wired funds to Tehran."
    assert ti["labels"] == [{"label": l} for l in OFAC_LABELS]
    assert ti["initialEntities"] == rec["initialEntities"]
    assert ti["metaData"] == rec["metaData"]


def test_comprehend_no_entities_included_with_empty_seeds():
    # Docs with no detections (empty list, null, or missing key) are still included
    # as valid records with empty seeds -- the reviewer labels them from scratch.
    for doc in ({"File": "d.txt", "Entities": []},
                {"File": "d.txt", "Entities": None},
                {"File": "d.txt"}):
        rec = comprehend_doc_to_record(doc, "s3://bucket/docs/")
        assert rec["source-ref"] == "s3://bucket/docs/d.txt"
        assert rec["initialEntities"] == []
        assert rec["metaData"] == []
        assert rec["labels"] == {"labels": [{"label": l} for l in OFAC_LABELS]}
        # Still round-trips to a valid taskInput.
        ti = build_task_input(rec, source_ref_reader=lambda uri: "some doc text")
        assert ti["initialEntities"] == []


# --- consolidation ---------------------------------------------------------
def _worker(wid, ents):
    return {"workerId": wid, "annotationData": {"content": json.dumps({"annotatedResult": {"entities": ents}})}}


def _worker_with_meta(wid, ents, meta):
    return {"workerId": wid, "annotationData": {"content": json.dumps({
        "annotatedResult": {"entities": ents},
        "metaData": json.dumps(meta),
    })}}


def test_consolidate_single_outputs_metadata():
    # Entities pass through; metaData carries confidence + the entered ofacID.
    objs = [{
        "datasetObjectId": "0",
        "dataObject": {"metaData": [{"startOffset": 0, "endOffset": 4, "confidence": 0.95, "ofacID": "FILL"}]},
        "annotations": [_worker_with_meta(
            "w1",
            [{"label": "ORG", "startOffset": 0, "endOffset": 4}],
            [{"startOffset": 0, "endOffset": 4, "confidence": 0.95, "ofacID": "OFAC_1"}],
        )],
    }]
    content = consolidate_single(objs, "ner-labels")[0]["consolidatedAnnotation"]["content"]["ner-labels"]
    assert content["entities"][0] == {"startOffset": 0, "endOffset": 4, "label": "ORG"}
    assert content["metaData"][0]["confidence"] == 0.95
    assert content["metaData"][0]["ofacID"] == "OFAC_1"


def test_consolidate_single_drops_unfilled_ofac():
    # A span left as "FILL" -> ofacID dropped from output; confidence retained.
    objs = [{
        "datasetObjectId": "0",
        "dataObject": {"metaData": [{"startOffset": 0, "endOffset": 4, "confidence": 0.9, "ofacID": "FILL"}]},
        "annotations": [_worker_with_meta(
            "w1",
            [{"label": "ORG", "startOffset": 0, "endOffset": 4}],
            [{"startOffset": 0, "endOffset": 4, "confidence": 0.9, "ofacID": "FILL"}],
        )],
    }]
    meta = consolidate_single(objs, "ner-labels")[0]["consolidatedAnnotation"]["content"]["ner-labels"]["metaData"][0]
    assert "ofacID" not in meta and meta["confidence"] == 0.9


def test_consolidate_single_carries_entered_ofac_id():
    # New span with no seed/confidence; annotator-entered ID must pass through.
    objs = [{
        "datasetObjectId": "0",
        "dataObject": {},
        "annotations": [_worker_with_meta(
            "w1",
            [{"label": "ORG", "startOffset": 70, "endOffset": 78}],
            [{"startOffset": 70, "endOffset": 78, "confidence": None, "ofacID": "OFAC_NEW"}],
        )],
    }]
    meta = consolidate_single(objs, "ner-labels")[0]["consolidatedAnnotation"]["content"]["ner-labels"]["metaData"][0]
    assert meta["ofacID"] == "OFAC_NEW"
    assert meta["confidence"] is None


# --- local simulator (uses the real shared manifest) -----------------------
def test_simulate_end_to_end_single():
    records = load_manifest(os.path.join(ROOT, "manifests", "input.manifest.example"))
    with open(os.path.join(HERE, "..", "sample_data", "worker_answers.single.json")) as f:
        answers = json.load(f)
    report = simulate(records, answers)
    assert len(report["consolidated"]) == len(records)
    # First doc: ORG@0-9 passes through; its metaData carries the seeded confidence
    # (ofacID stays FILL in the simulator -> dropped from output).
    first = report["consolidated"][0]["consolidatedAnnotation"]["content"]["ner-labels"]
    assert first["entities"][0] == {"startOffset": 0, "endOffset": 9, "label": "ORG"}
    assert first["metaData"][0]["confidence"] == 0.99
    assert "ofacID" not in first["metaData"][0]


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


def test_launch_with_injected_client_uploads_nothing():
    calls = {}

    class FakeSM:
        def create_labeling_job(self, **kwargs):
            calls["create"] = kwargs
            return {"LabelingJobArn": "arn:aws:sagemaker:...:labeling-job/job"}

    # No S3 client and no upload step -- assets must already be in the bucket.
    resp = launch(_cfg(), sagemaker_client=FakeSM())
    assert resp["LabelingJobArn"].endswith("labeling-job/job")
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
