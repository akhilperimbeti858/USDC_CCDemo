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
launcher = _load("launcher_handler", "lambdas/launch_labeling_job/handler.py")


def _worker(worker_id, entities):
    return {
        "workerId": worker_id,
        "annotationData": {"content": json.dumps({"annotatedResult": {"entities": entities}})},
    }


# ---------------------------------------------------------------------------
# Pre-annotation Lambda
# ---------------------------------------------------------------------------
def test_pre_passes_initial_entities_and_metadata():
    event = {
        "version": "2018-10-16",
        "labelingJobArn": "arn:test",
        "dataObject": {
            "source": "Acme Corp wired funds.",
            "labels": {"labels": [{"label": "PERSON"}, {"label": "ORG"}]},
            "initialEntities": [{"startOffset": 0, "endOffset": 4, "label": "ORG"}],
            "metaData": [{"startOffset": 0, "endOffset": 4, "confidence": 0.99, "ofacID": "FILL"}],
        },
    }
    out = pre.lambda_handler(event, None)
    assert out["isHumanAnnotationRequired"] == "true"
    ti = out["taskInput"]
    assert ti["taskObject"] == "Acme Corp wired funds."
    assert ti["metaData"][0]["confidence"] == 0.99
    assert ti["metaData"][0]["ofacID"] == "FILL"
    assert ti["initialEntities"][0]["label"] == "ORG"
    # Per-record label-set config is passed through to taskInput.labels.
    assert {"label": "PERSON"} in ti["labels"]


def test_pre_defaults_metadata_to_empty():
    # Record with no metaData/initialEntities -> taskInput gets empty arrays.
    out = pre.lambda_handler({"dataObject": {"source": "no metadata here"}}, None)
    assert out["taskInput"]["metaData"] == []
    assert out["taskInput"]["initialEntities"] == []


def test_pre_ignores_legacy_field_names():
    # Legacy `ofacMetadata`/`initialValue`/`ofac_metadata` keys are NOT honored.
    event = {"dataObject": {
        "source": "x",
        "ofacMetadata": [{"startOffset": 0, "endOffset": 1, "ofacId": "SDN-9"}],
        "initialValue": [{"label": "ORG", "startOffset": 0, "endOffset": 1}],
    }}
    ti = pre.lambda_handler(event, None)["taskInput"]
    assert ti["metaData"] == []
    assert ti["initialEntities"] == []


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
def _worker_with_meta(worker_id, entities, meta):
    """Worker submission carrying the hidden-field `metaData` JSON string."""
    return {
        "workerId": worker_id,
        "annotationData": {"content": json.dumps({
            "annotatedResult": {"entities": entities},
            "metaData": json.dumps(meta),
        })},
    }


def test_post_single_outputs_entities_and_metadata():
    # Annotator entered an OFAC ID for a seeded span -> it appears in metaData,
    # alongside the (worker- or seed-supplied) confidence.
    dataset = [{
        "datasetObjectId": "0",
        "dataObject": {
            "source": "Acme Corp",
            "metaData": [{"startOffset": 0, "endOffset": 4, "confidence": 0.99, "ofacID": "FILL"}],
        },
        "annotations": [_worker_with_meta(
            "w1",
            [{"label": "ORG", "startOffset": 0, "endOffset": 4}],
            [{"startOffset": 0, "endOffset": 4, "confidence": 0.99, "ofacID": "OFAC_1234"}],
        )],
    }]
    post_single._read_s3_json = lambda uri: dataset  # type: ignore
    event = {"labelAttributeName": "ner-labels", "payload": {"s3Uri": "s3://b/k"}}
    content = post_single.lambda_handler(event, None)[0]["consolidatedAnnotation"]["content"]["ner-labels"]

    assert content["entities"][0] == {"startOffset": 0, "endOffset": 4, "label": "ORG"}
    meta = content["metaData"][0]
    assert meta["confidence"] == 0.99
    assert meta["ofacID"] == "OFAC_1234"


def test_post_single_drops_unfilled_ofac():
    # A span left as "FILL" -> ofacID is dropped from the output; confidence stays.
    dataset = [{
        "datasetObjectId": "0",
        "dataObject": {"source": "Acme Corp",
                       "metaData": [{"startOffset": 0, "endOffset": 4, "confidence": 0.99, "ofacID": "FILL"}]},
        "annotations": [_worker_with_meta(
            "w1",
            [{"label": "ORG", "startOffset": 0, "endOffset": 4}],
            [{"startOffset": 0, "endOffset": 4, "confidence": 0.99, "ofacID": "FILL"}],
        )],
    }]
    post_single._read_s3_json = lambda uri: dataset  # type: ignore
    event = {"labelAttributeName": "ner-labels", "payload": {"s3Uri": "s3://b/k"}}
    meta = post_single.lambda_handler(event, None)[0]["consolidatedAnnotation"]["content"]["ner-labels"]["metaData"][0]
    assert "ofacID" not in meta and meta["confidence"] == 0.99


def test_post_single_new_span_carries_entered_ofac_id():
    # Worker added a brand-new span (no seed/confidence) and entered an OFAC ID ->
    # it reaches the output with confidence null.
    dataset = [{
        "datasetObjectId": "0",
        "dataObject": {"source": "Funds routed via a new shell company offshore."},  # no metaData
        "annotations": [_worker_with_meta(
            "w1",
            [{"label": "ORG", "startOffset": 17, "endOffset": 35}],
            [{"startOffset": 17, "endOffset": 35, "confidence": None, "ofacID": "OFAC_NEW"}],
        )],
    }]
    post_single._read_s3_json = lambda uri: dataset  # type: ignore
    event = {"labelAttributeName": "ner-labels", "payload": {"s3Uri": "s3://b/k"}}
    meta = post_single.lambda_handler(event, None)[0]["consolidatedAnnotation"]["content"]["ner-labels"]["metaData"][0]
    assert meta["ofacID"] == "OFAC_NEW"
    assert meta["confidence"] is None


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
    assert lines[0]["initialEntities"] == [{"startOffset": 0, "endOffset": 4, "label": "OFAC_ORG"}]  # DATE dropped
    assert lines[0]["labels"] == {"labels": [{"label": l} for l in ["OFAC_ORG", "OFAC_POI", "FTO"]]}
    # metaData is parallel to initialEntities: confidence from Score, ofacID placeholder.
    assert lines[0]["metaData"] == [{"startOffset": 0, "endOffset": 4, "confidence": 0.99, "ofacID": "FILL"}]
    assert lines[1]["initialEntities"] == [] and lines[1]["metaData"] == []  # doc2 had no entities


def test_comprehend_resolve_location_event_shapes():
    # S3 notification (key is URL-encoded) and EventBridge both resolve.
    s3_evt = {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "a+b/output.tar.gz"}}}]}
    assert comprehend._resolve_output_location(s3_evt) == ("b", "a b/output.tar.gz")
    eb_evt = {"detail": {"bucket": {"name": "b2"}, "object": {"key": "out/output.tar.gz"}}}
    assert comprehend._resolve_output_location(eb_evt) == ("b2", "out/output.tar.gz")
    assert comprehend._resolve_output_location({"output_s3_uri": "s3://b3/k/output.tar.gz"}) == ("b3", "k/output.tar.gz")


def test_launch_labeling_job_builds_request():
    env = {
        "ROLE_ARN": "arn:aws:iam::111122223333:role/usdc-ner-gt-execution-role",
        "WORKTEAM_ARN": "arn:aws:sagemaker:us-east-1:111122223333:workteam/private-crowd/team",
        "PRE_LAMBDA_ARN": "arn:aws:lambda:us-east-1:111122223333:function:usdc-ner-SageMaker-pre-annotation",
        "POST_LAMBDA_ARN": "arn:aws:lambda:us-east-1:111122223333:function:usdc-ner-SageMaker-post-single",
        "MANIFEST_S3_URI": "s3://gt-bucket/input/input.manifest",
        "UI_TEMPLATE_S3_URI": "s3://gt-bucket/templates/ner-template.liquid.html",
        "OUTPUT_S3_URI": "s3://gt-bucket/output/",
        "JOB_NAME_PREFIX": "usdc-ner",
        "MAX_CONCURRENT_TASK_COUNT": "250",
        "WORKERS_PER_OBJECT": "1",
        "TASK_KEYWORDS": json.dumps(["NER", "OFAC"]),
    }
    captured = {}
    launcher._create_job = lambda request: captured.update(request=request) or {}  # type: ignore

    os.environ.update(env)
    try:
        # A manifest-landed EventBridge event; config comes from env, not the event.
        evt = {"detail": {"bucket": {"name": "gt-bucket"}, "object": {"key": "input/input.manifest"}}}
        out = launcher.lambda_handler(evt, None)
    finally:
        for k in env:
            os.environ.pop(k, None)

    req = captured["request"]
    assert out["labeling_job_name"].startswith("usdc-ner-")
    assert req["LabelingJobName"] == out["labeling_job_name"]
    assert req["RoleArn"] == env["ROLE_ARN"]
    assert req["LabelAttributeName"] == "ner-labels"  # default
    assert req["InputConfig"]["DataSource"]["S3DataSource"]["ManifestS3Uri"] == env["MANIFEST_S3_URI"]
    assert req["OutputConfig"]["S3OutputPath"] == env["OUTPUT_S3_URI"]
    htc = req["HumanTaskConfig"]
    assert htc["WorkteamArn"] == env["WORKTEAM_ARN"]
    assert htc["UiConfig"]["UiTemplateS3Uri"] == env["UI_TEMPLATE_S3_URI"]
    assert htc["PreHumanTaskLambdaArn"] == env["PRE_LAMBDA_ARN"]
    assert htc["AnnotationConsolidationConfig"]["AnnotationConsolidationLambdaArn"] == env["POST_LAMBDA_ARN"]
    assert htc["TaskKeywords"] == ["NER", "OFAC"]
    assert htc["NumberOfHumanWorkersPerDataObject"] == 1
    assert htc["MaxConcurrentTaskCount"] == 250  # from env, as int
    assert htc["TaskTimeLimitInSeconds"] == 3600  # default, as int


def test_launch_labeling_job_requires_config():
    # Missing required env vars must fail fast rather than create a bad job.
    launcher._create_job = lambda request: (_ for _ in ()).throw(AssertionError("should not be called"))  # type: ignore
    raised = False
    try:
        launcher.build_create_labeling_job_request("usdc-ner-test")
    except KeyError:
        raised = True
    assert raised


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
