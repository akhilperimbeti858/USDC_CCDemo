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
        "ofacMetadata": [ ... ],                # optional, embedded per record
        "initialValue": [ ... ]                 # optional seed spans
      }
    }

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
_DEFAULT_LABELS = ["PERSON", "ORG", "LOC", "SANCTIONED_ENTITY"]


def _entity_labels():
    raw = os.environ.get("ENTITY_LABELS")
    if not raw:
        return list(_DEFAULT_LABELS)
    try:
        labels = json.loads(raw)
        return labels if isinstance(labels, list) else list(_DEFAULT_LABELS)
    except (ValueError, TypeError):
        return list(_DEFAULT_LABELS)


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
    # Default to [] when absent so the template always has a valid array.
    ofac_metadata = data_object.get("ofacMetadata") or []

    # Optional pre-seeded entity spans (e.g. from an upstream model). May be [].
    initial_value = data_object.get("initialValue") or []

    task_input = {
        "taskObject": text,
        "labels": _entity_labels(),
        "initialValue": initial_value,
        "ofacMetadata": ofac_metadata,
    }

    return {
        "taskInput": task_input,
        "isHumanAnnotationRequired": "true",
    }
