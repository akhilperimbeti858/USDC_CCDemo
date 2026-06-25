"""Lambda: AWS Comprehend ``output.tar.gz`` -> Ground Truth input manifest.

WHAT THIS DOES
--------------
An Amazon Comprehend asynchronous entity-detection job writes its results to S3 as a
single gzipped tarball, ``output.tar.gz``. Inside is a JSON-Lines file with one record
per document, each shaped like::

    {"File": "doc1.txt", "Line": 0,
     "Entities": [{"Score": 0.99, "Type": "OFAC_ORG", "Text": "Acme Corp",
                   "BeginOffset": 0, "EndOffset": 9}, ...]}

This function downloads that tarball, unzips it in memory, turns each document into one
SageMaker Ground Truth manifest record, and writes the resulting JSON-Lines manifest to
S3 at the location the labeling flow reads (the GT bucket + ``input/input.manifest``).
The downstream pre-annotation Lambda then fetches each document's text via ``source-ref``
and renders it for a human reviewer.

It can be invoked TWO ways:
  * **Manually** - pass ``{"bucket": ..., "key": ...}`` (or ``{"output_s3_uri": ...}``)
    pointing at the ``output.tar.gz``.
  * **Automatically** - as the target of an S3 "ObjectCreated" notification or an
    EventBridge S3 event when the tarball lands in the bucket. Both event shapes are
    understood; the trigger itself is wired outside this code (it is intentionally not
    managed by the Terraform stack, which only *references* the bucket).

CONFIGURATION (environment variables)
-------------------------------------
  SOURCE_DOCS_S3_BASE  (required)  S3 prefix where the ORIGINAL documents live, e.g.
                                   ``s3://my-bucket/docs/``. Joined with each Comprehend
                                   ``File`` to build the manifest ``source-ref``.
  MANIFEST_S3_BUCKET   (required)  Bucket to write the generated manifest into.
  MANIFEST_S3_KEY      (optional)  Manifest object key. Default ``input/input.manifest``.
  ENTITY_LABELS        (optional)  JSON array of entity types to KEEP / use as the label
                                   set. Default ``["OFAC_ORG","OFAC_POI","FTO"]``.
  MIN_SCORE            (optional)  Drop entities whose Comprehend ``Score`` is below this.

The conversion logic here mirrors ``python_boto3/ner_pipeline/comprehend_to_manifest.py``.
Lambdas in this repo are zipped per-directory, so the handler cannot import that module
at runtime; the small pure functions are duplicated below (same handler/mirror pattern
the rest of the project uses).
"""

import io
import json
import os
import tarfile
from urllib.parse import urlparse, unquote_plus

# Default entity types kept from the Comprehend output and presented to workers.
_DEFAULT_LABELS = ["OFAC_ORG", "OFAC_POI", "FTO"]


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
def _allowed_labels():
    """Entity types to keep / the label set, from ENTITY_LABELS (JSON array)."""
    raw = os.environ.get("ENTITY_LABELS")
    if not raw:
        return list(_DEFAULT_LABELS)
    try:
        labels = json.loads(raw)
        return labels if isinstance(labels, list) and labels else list(_DEFAULT_LABELS)
    except (ValueError, TypeError):
        return list(_DEFAULT_LABELS)


def _min_score():
    """Optional confidence floor from MIN_SCORE; None means 'keep everything'."""
    raw = os.environ.get("MIN_SCORE", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Event parsing - support manual, S3-notification and EventBridge shapes
# ---------------------------------------------------------------------------
def _resolve_output_location(event):
    """Return (bucket, key) of the Comprehend ``output.tar.gz`` from the trigger event.

    Accepts three shapes so the function works however it is invoked:
      1. Manual: {"bucket": "...", "key": "..."} or {"output_s3_uri": "s3://b/k"}.
      2. S3 notification: {"Records": [{"s3": {"bucket": {"name": ...},
                                               "object": {"key": ...}}}]}.
      3. EventBridge S3 event: {"detail": {"bucket": {"name": ...},
                                           "object": {"key": ...}}}.
    """
    # 1a. Manual with an explicit s3:// URI.
    if event.get("output_s3_uri"):
        parsed = urlparse(event["output_s3_uri"])
        return parsed.netloc, parsed.path.lstrip("/")

    # 1b. Manual with explicit bucket + key.
    if event.get("bucket") and event.get("key"):
        return event["bucket"], event["key"]

    # 2. S3 bucket notification (one or more records; we take the first).
    records = event.get("Records")
    if records:
        s3 = records[0].get("s3", {})
        bucket = s3.get("bucket", {}).get("name")
        # Keys in S3 events are URL-encoded (spaces -> '+', etc.).
        key = unquote_plus(s3.get("object", {}).get("key", ""))
        if bucket and key:
            return bucket, key

    # 3. EventBridge "Object Created" event.
    detail = event.get("detail")
    if detail:
        bucket = detail.get("bucket", {}).get("name")
        key = detail.get("object", {}).get("key")
        if bucket and key:
            return bucket, key

    raise ValueError(
        "Could not determine the output.tar.gz location from the event. Provide "
        "{'bucket','key'} or {'output_s3_uri'}, or invoke via an S3/EventBridge trigger."
    )


# ---------------------------------------------------------------------------
# S3 I/O - module-level so unit tests can monkeypatch them (no boto3 needed)
# ---------------------------------------------------------------------------
def _download_s3(bucket, key):
    """Return the raw bytes of an S3 object."""
    import boto3  # provided by the Lambda runtime; imported lazily for testability

    return boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()


def _upload_s3(bucket, key, body, content_type="application/json"):
    """Write bytes/str to an S3 object."""
    import boto3

    if isinstance(body, str):
        body = body.encode("utf-8")
    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)


# ---------------------------------------------------------------------------
# Tar extraction + conversion (mirror of ner_pipeline/comprehend_to_manifest.py)
# ---------------------------------------------------------------------------
def _extract_docs(tar_bytes):
    """Unzip an ``output.tar.gz`` (in memory) into a list of Comprehend doc objects.

    The tarball holds one or more text members in JSON-Lines format. We read every
    regular-file member and parse each non-empty line as one document object
    (``{"File": ..., "Entities": [...]}``). Blank lines are skipped.
    """
    docs = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            text = extracted.read().decode("utf-8")
            for line in text.splitlines():
                line = line.strip()
                if line:
                    docs.append(json.loads(line))
    return docs


def _source_ref(s3_docs_base, file_name):
    """Join the S3 docs prefix with a Comprehend ``File`` value into an S3 URI."""
    return s3_docs_base.rstrip("/") + "/" + str(file_name).lstrip("/")


def _doc_to_record(doc, s3_docs_base, allowed_labels, min_score):
    """Convert one Comprehend document object into a GT manifest record.

    Output shape (the contract the pre-annotation Lambda consumes)::

        {"source-ref": "s3://docs/doc1.txt",
         "labels": {"labels": [{"label": "OFAC_ORG"}, ...]},
         "initialEntities": [{"startOffset": 0, "endOffset": 9, "label": "OFAC_ORG"}, ...],
         "metaData":        [{"startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "FILL"}, ...]}

    ``initialEntities`` and ``metaData`` are PARALLEL: one entry each per kept
    entity, aligned by offset. ``metaData`` carries the Comprehend ``Score`` as
    ``confidence`` and a placeholder ``ofacID`` of ``"FILL"`` that the human
    annotator replaces in the UI (e.g. with ``"OFAC_1234"``). This comes straight
    from an analysis job, so there are no pre-existing OFAC IDs.

    Only entities whose ``Type`` is in ``allowed_labels`` are kept (and, if set,
    those scoring >= ``min_score``). Documents with no kept entities produce a
    valid record with both arrays empty.
    """
    allowed = set(allowed_labels)
    initial_entities = []
    meta_data = []
    # `or []` tolerates a missing key or an explicit JSON null for Entities.
    for ent in (doc.get("Entities") or []):
        if ent.get("Type") not in allowed:
            continue
        if min_score is not None and ent.get("Score", 1.0) < min_score:
            continue
        start, end = ent["BeginOffset"], ent["EndOffset"]
        initial_entities.append({
            "startOffset": start,
            "endOffset": end,
            "label": ent["Type"],
        })
        meta_data.append({
            "startOffset": start,
            "endOffset": end,
            "confidence": ent.get("Score"),
            "ofacID": "FILL",
        })

    return {
        "source-ref": _source_ref(s3_docs_base, doc["File"]),
        "labels": {"labels": [{"label": label} for label in allowed_labels]},
        "initialEntities": initial_entities,
        "metaData": meta_data,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def lambda_handler(event, context):
    # 1. Required configuration.
    source_docs_base = os.environ["SOURCE_DOCS_S3_BASE"]      # KeyError if unset (fail fast)
    manifest_bucket = os.environ["MANIFEST_S3_BUCKET"]
    manifest_key = os.environ.get("MANIFEST_S3_KEY", "input/input.manifest")
    allowed_labels = _allowed_labels()
    min_score = _min_score()

    # 2. Find and download the Comprehend output tarball.
    src_bucket, src_key = _resolve_output_location(event)
    tar_bytes = _download_s3(src_bucket, src_key)

    # 3. Unzip + parse, then 4. convert each document to a manifest record.
    docs = _extract_docs(tar_bytes)
    records = [_doc_to_record(d, source_docs_base, allowed_labels, min_score) for d in docs]

    # 5. Serialize to JSON-Lines (one record per line) and write the manifest to S3.
    manifest_body = "".join(json.dumps(r) + "\n" for r in records)
    _upload_s3(manifest_bucket, manifest_key, manifest_body)

    # 6. Return a summary (useful for manual invokes / CloudWatch logs).
    seeded = sum(len(r["initialEntities"]) for r in records)
    return {
        "records": len(records),
        "entities": seeded,
        "manifest_s3_uri": f"s3://{manifest_bucket}/{manifest_key}",
        "source_tar": f"s3://{src_bucket}/{src_key}",
    }
