"""Unit tests for the Ground Truth pre/post-annotation Lambdas.

Run with either:
    python tests/test_lambdas.py
    pytest tests/test_lambdas.py
"""

import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, relpath):
    """Load a handler module by file path (they all share the name 'handler')."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pre = _load("pre_handler", "lambdas/pre_annotation/handler.py")
post_single = _load("post_single_handler", "lambdas/post_annotation_single/handler.py")
comprehend = _load("comprehend_handler", "lambdas/comprehend_to_manifest/handler.py")


def _worker(worker_id, entities):
    return {
        "workerId": worker_id,
        "annotationData": {"content": json.dumps({"annotatedResult": {"entities": entities}})},
    }


# ---------------------------------------------------------------------------
# Pre-annotation Lambda
# ---------------------------------------------------------------------------
def test_pre_inline_source_passes_through_ofac():
    event = {
        "version": "2018-10-16",
        "labelingJobArn": "arn:test",
        "dataObject": {
            "source": "Acme Corp wired funds.",
            "labels": {"labels": [{"label": "PERSON"}, {"label": "ORG"}]},
            "initialEntities": [],
            "ofac_metadata": [{"startOffset": 0, "endOffset": 4, "ofacId": "SDN-1", "label": "ORG"}],
        },
    }
    out = pre.lambda_handler(event, None)
    assert out["isHumanAnnotationRequired"] == "true"
    ti = out["taskInput"]
    assert ti["taskObject"] == "Acme Corp wired funds."
    assert ti["ofacMetadata"][0]["ofacId"] == "SDN-1"
    assert ti["initialValue"] == []
    # Per-record label-set config is passed through to taskInput.labels.
    assert {"label": "PERSON"} in ti["labels"]


def test_pre_defaults_ofac_to_empty():
    # Analysis-job record: no ofac_metadata -> taskInput gets an empty array.
    out = pre.lambda_handler({"dataObject": {"source": "no metadata here"}}, None)
    assert out["taskInput"]["ofacMetadata"] == []


def test_pre_accepts_legacy_field_names():
    # Legacy `ofacMetadata`/`initialValue` keys still work.
    event = {"dataObject": {
        "source": "x",
        "ofacMetadata": [{"startOffset": 0, "endOffset": 1, "ofacId": "SDN-9"}],
        "initialValue": [{"label": "ORG", "startOffset": 0, "endOffset": 1}],
    }}
    ti = pre.lambda_handler(event, None)["taskInput"]
    assert ti["ofacMetadata"][0]["ofacId"] == "SDN-9"
    assert ti["initialValue"][0]["label"] == "ORG"


def test_pre_falls_back_to_entity_labels_env_without_record_labels():
    # When the record carries no `labels` config, ENTITY_LABELS is the source.
    os.environ["ENTITY_LABELS"] = json.dumps(["A", "B"])
    try:
        out = pre.lambda_handler({"dataObject": {"source": "x"}}, None)
        assert out["taskInput"]["labels"] == ["A", "B"]
    finally:
        del os.environ["ENTITY_LABELS"]


# ---------------------------------------------------------------------------
# Post-annotation Lambda - single worker
# ---------------------------------------------------------------------------
def test_post_single_pass_through_and_reattach_ofac(monkeypatch=None):
    dataset = [{
        "datasetObjectId": "0",
        "dataObject": {
            "source": "Acme Corp",
            "ofacMetadata": [{"startOffset": 0, "endOffset": 4, "ofacId": "SDN-1", "label": "ORG"}],
        },
        "annotations": [_worker("w1", [{"label": "ORG", "startOffset": 0, "endOffset": 4}])],
    }]
    post_single._read_s3_json = lambda uri: dataset  # type: ignore
    event = {"labelAttributeName": "ner-labels", "payload": {"s3Uri": "s3://b/k"}}
    out = post_single.lambda_handler(event, None)

    entities = out[0]["consolidatedAnnotation"]["content"]["ner-labels"]["entities"]
    assert entities[0]["label"] == "ORG"
    assert entities[0]["ofacId"] == "SDN-1"  # re-attached from dataObject


def _worker_with_overrides(worker_id, entities, overrides):
    """Worker submission carrying a hidden-field `ofacOverrides` JSON string."""
    return {
        "workerId": worker_id,
        "annotationData": {"content": json.dumps({
            "annotatedResult": {"entities": entities},
            "ofacOverrides": json.dumps(overrides),
        })},
    }


def test_post_single_new_span_carries_entered_ofac_id():
    # Worker added a brand-new span (offset not in the manifest) and typed an OFAC
    # ID in the modal -> it must reach the output (training data).
    dataset = [{
        "datasetObjectId": "0",
        "dataObject": {"source": "Funds routed via a new shell company offshore."},  # no ofac_metadata
        "annotations": [_worker_with_overrides(
            "w1",
            [{"label": "ORG", "startOffset": 17, "endOffset": 35}],
            [{"startOffset": 17, "endOffset": 35, "ofacId": "SDN-NEW", "label": "ORG"}],
        )],
    }]
    post_single._read_s3_json = lambda uri: dataset  # type: ignore
    event = {"labelAttributeName": "ner-labels", "payload": {"s3Uri": "s3://b/k"}}
    out = post_single.lambda_handler(event, None)
    ent = out[0]["consolidatedAnnotation"]["content"]["ner-labels"]["entities"][0]
    assert ent["ofacId"] == "SDN-NEW"


# ---------------------------------------------------------------------------
# Comprehend output.tar.gz -> manifest Lambda
# ---------------------------------------------------------------------------
def _make_tar_gz(docs):
    import io
    import tarfile
    body = "".join(json.dumps(d) + "\n" for d in docs).encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="output")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def test_comprehend_lambda_writes_manifest_from_tar():
    docs = [
        {"File": "doc1.txt", "Entities": [
            {"Score": 0.99, "Type": "OFAC_ORG", "Text": "Acme", "BeginOffset": 0, "EndOffset": 4},
            {"Score": 0.80, "Type": "DATE", "Text": "March", "BeginOffset": 10, "EndOffset": 15}]},
        {"File": "doc2.txt", "Entities": []},  # no detections -> empty seeds, still included
    ]
    captured = {}
    comprehend._download_s3 = lambda b, k: _make_tar_gz(docs)            # type: ignore
    comprehend._upload_s3 = lambda b, k, body, content_type="application/json": captured.update(  # type: ignore
        bucket=b, key=k, body=body)

    os.environ["SOURCE_DOCS_S3_BASE"] = "s3://docs-bucket/docs/"
    os.environ["MANIFEST_S3_BUCKET"] = "gt-bucket"
    try:
        out = comprehend.lambda_handler({"bucket": "out-bucket", "key": "x/output.tar.gz"}, None)
    finally:
        del os.environ["SOURCE_DOCS_S3_BASE"], os.environ["MANIFEST_S3_BUCKET"]

    assert out["records"] == 2 and out["entities"] == 1
    assert captured["bucket"] == "gt-bucket" and captured["key"] == "input/input.manifest"
    lines = [json.loads(l) for l in captured["body"].splitlines()]
    assert lines[0]["source-ref"] == "s3://docs-bucket/docs/doc1.txt"
    assert lines[0]["initialEntities"] == [{"label": "OFAC_ORG", "startOffset": 0, "endOffset": 4}]  # DATE dropped
    assert lines[0]["labels"] == {"labels": [{"label": l} for l in ["OFAC_ORG", "OFAC_POI", "FTO"]]}
    assert lines[0]["ofac_metadata"] == []
    assert lines[1]["initialEntities"] == []  # doc2 had no entities


def test_comprehend_resolve_location_event_shapes():
    # S3 notification (key is URL-encoded) and EventBridge both resolve.
    s3_evt = {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "a+b/output.tar.gz"}}}]}
    assert comprehend._resolve_output_location(s3_evt) == ("b", "a b/output.tar.gz")
    eb_evt = {"detail": {"bucket": {"name": "b2"}, "object": {"key": "out/output.tar.gz"}}}
    assert comprehend._resolve_output_location(eb_evt) == ("b2", "out/output.tar.gz")
    assert comprehend._resolve_output_location({"output_s3_uri": "s3://b3/k/output.tar.gz"}) == ("b3", "k/output.tar.gz")


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    raise SystemExit(1 if _run_all() else 0)
