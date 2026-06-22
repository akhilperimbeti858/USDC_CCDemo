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
post_merge = _load("post_merge_handler", "lambdas/post_annotation_merge/handler.py")


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


# ---------------------------------------------------------------------------
# Post-annotation Lambda - multi-worker merge
# ---------------------------------------------------------------------------
def test_post_merge_majority_keeps_agreed_span():
    # 3 workers; 2 agree on ORG@0-4, the third disagrees on label.
    dataset = [{
        "datasetObjectId": "0",
        "dataObject": {"source": "Acme Corp"},
        "annotations": [
            _worker("w1", [{"label": "ORG", "startOffset": 0, "endOffset": 4, "ofacId": "SDN-1"}]),
            _worker("w2", [{"label": "ORG", "startOffset": 0, "endOffset": 4, "ofacId": "SDN-1"}]),
            _worker("w3", [{"label": "PERSON", "startOffset": 0, "endOffset": 4}]),
        ],
    }]
    post_merge._read_s3_json = lambda uri: dataset  # type: ignore
    event = {"labelAttributeName": "ner-labels", "payload": {"s3Uri": "s3://b/k"}}
    out = post_merge.lambda_handler(event, None)

    entities = out[0]["consolidatedAnnotation"]["content"]["ner-labels"]["entities"]
    assert len(entities) == 1
    assert entities[0]["label"] == "ORG"          # majority label
    assert entities[0]["ofacId"] == "SDN-1"        # majority OFAC id
    assert entities[0]["confidence"] == 1.0        # all 3 overlapped the cluster


def test_post_merge_drops_below_threshold_span():
    # Only 1 of 3 workers marked a second span -> below the 0.5 agreement ratio.
    dataset = [{
        "datasetObjectId": "0",
        "dataObject": {"source": "Acme Corp in Tehran"},
        "annotations": [
            _worker("w1", [{"label": "ORG", "startOffset": 0, "endOffset": 4},
                           {"label": "LOC", "startOffset": 13, "endOffset": 19}]),
            _worker("w2", [{"label": "ORG", "startOffset": 0, "endOffset": 4}]),
            _worker("w3", [{"label": "ORG", "startOffset": 0, "endOffset": 4}]),
        ],
    }]
    post_merge._read_s3_json = lambda uri: dataset  # type: ignore
    event = {"labelAttributeName": "ner-labels", "payload": {"s3Uri": "s3://b/k"}}
    out = post_merge.lambda_handler(event, None)

    entities = out[0]["consolidatedAnnotation"]["content"]["ner-labels"]["entities"]
    labels = [(e["label"], e["startOffset"]) for e in entities]
    assert ("ORG", 0) in labels        # kept (3/3)
    assert ("LOC", 13) not in labels   # dropped (1/3 < 0.5)


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


def test_post_merge_new_span_carries_entered_ofac_id_no_voting():
    # One worker entered an OFAC ID for the agreed span; with no voting it survives.
    dataset = [{
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
    post_merge._read_s3_json = lambda uri: dataset  # type: ignore
    event = {"labelAttributeName": "ner-labels", "payload": {"s3Uri": "s3://b/k"}}
    out = post_merge.lambda_handler(event, None)
    ent = out[0]["consolidatedAnnotation"]["content"]["ner-labels"]["entities"][0]
    assert ent["label"] == "ORG"
    assert ent["ofacId"] == "SDN-NEW"


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
