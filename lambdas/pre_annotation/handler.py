"""Pre-annotation Lambda for the SageMaker Ground Truth custom NER workflow.

Ground Truth invokes this function once per dataset object, *before* the task is
rendered to a worker. Its job is to transform a raw manifest record into the
``taskInput`` object that the custom Crowd-HTML template binds to:

    task.input.taskObject    -> the document text to annotate
    task.input.labels        -> the entity label set
    task.input.initialValue  -> seed entity spans (may be empty)
    task.input.ofacMetadata  -> per-span OFAC records used by the entity panel / modal

Input event shape (Ground Truth, version 2018-10-16)::

    {
      "version": "2018-10-16",
      "labelingJobArn": "...",
      "dataObject": {
        "source": "raw text to annotate",      # OR "source-ref": "s3://bucket/key"
        "labels": { "labels": [ {"label": "PERSON"}, ... ] },  # per-record label set
        "initialEntities": [ ... ],             # optional seed spans (may be [])
        "ofac_metadata": [ ... ]                # [] for analysis jobs, populated for training
      }
    }

`initialEntities`/`ofac_metadata` are the current manifest field names; the
legacy `initialValue`/`ofacMetadata` names are still accepted as a fallback.
The renames stay at the manifest layer — the returned ``taskInput`` keeps the
keys the Crowd-HTML template binds to (``initialValue``/``ofacMetadata``).

Return shape::

    {
      "taskInput": { "taskObject": ..., "labels": ..., "initialValue": ..., "ofacMetadata": ... },
      "isHumanAnnotationRequired": "true"
    }
"""

import json
import os

# Default label set, overridable via the ENTITY_LABELS env var (JSON array).
# Kept in sync with the labeling job's `entity_labels` Terraform variable.
# OFAC categories emitted by the upstream Comprehend custom recognizer.
_DEFAULT_LABELS = ["OFAC_ORG", "OFAC_POI", "FTO"]


def _entity_labels():
    raw = os.environ.get("ENTITY_LABELS")
    if not raw:
        return list(_DEFAULT_LABELS)
    try:
        labels = json.loads(raw)
        return labels if isinstance(labels, list) else list(_DEFAULT_LABELS)
    except (ValueError, TypeError):
        return list(_DEFAULT_LABELS)


def _labels_for(data_object):
    """Resolve the entity label set for one record.

    The label set now travels in the manifest as a config object::

        "labels": { "labels": [ {"label": "PERSON"}, {"label": "ORG"}, ... ] }

    We pass that inner array straight through to the crowd element (it accepts
    both ``{"label": ...}`` objects and bare strings). A bare array is tolerated
    too. When the record carries no label set we fall back to the
    ``ENTITY_LABELS`` env var / built-in defaults.
    """
    cfg = data_object.get("labels")
    if isinstance(cfg, dict) and isinstance(cfg.get("labels"), list):
        return cfg["labels"]
    if isinstance(cfg, list):
        return cfg
    return _entity_labels()


def _read_source_ref(s3_uri):
    """Fetch and decode a text object referenced by a `source-ref` S3 URI."""
    import boto3  # provided by the Lambda runtime; imported lazily for testability

    assert s3_uri.startswith("s3://"), f"Unexpected source-ref: {s3_uri}"
    bucket, _, key = s3_uri[len("s3://"):].partition("/")
    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return body.decode("utf-8")


def _extract_text(data_object):
    """Support both inline `source` text and `source-ref` S3 references."""
    if "source" in data_object and data_object["source"] is not None:
        return data_object["source"]
    if "source-ref" in data_object and data_object["source-ref"]:
        return _read_source_ref(data_object["source-ref"])
    raise KeyError("dataObject must contain either 'source' or 'source-ref'")


def lambda_handler(event, context):
    data_object = event["dataObject"]

    text = _extract_text(data_object)
    # Normalize trailing/leading whitespace without disturbing internal offsets.
    text = text.rstrip()

    # OFAC metadata travels with the record (decision: embedded in the manifest).
    # Empty for incoming analysis jobs, pre-populated for training jobs. Default
    # to [] when absent so the template always has a valid array. The legacy
    # `ofacMetadata` key is still honored.
    ofac_metadata = data_object.get("ofac_metadata")
    if ofac_metadata is None:
        ofac_metadata = data_object.get("ofacMetadata")
    ofac_metadata = ofac_metadata or []

    # Optional pre-seeded entity spans (e.g. from an upstream model). May be [].
    # Current name is `initialEntities`; `initialValue` is the legacy fallback.
    initial_value = data_object.get("initialEntities")
    if initial_value is None:
        initial_value = data_object.get("initialValue")
    initial_value = initial_value or []

    task_input = {
        "taskObject": text,
        "labels": _labels_for(data_object),
        "initialValue": initial_value,
        "ofacMetadata": ofac_metadata,
    }

    return {
        "taskInput": task_input,
        "isHumanAnnotationRequired": "true",
    }
