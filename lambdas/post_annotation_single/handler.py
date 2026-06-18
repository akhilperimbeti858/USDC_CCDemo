"""Post-annotation (consolidation) Lambda - SINGLE-WORKER variant.

Ground Truth invokes this once a batch of dataset objects has been fully
annotated. With exactly one worker per object, consolidation is a straight
pass-through of that worker's entity spans, re-attaching OFAC IDs by start
offset where the crowd element dropped them.

Input event shape (Ground Truth, version 2018-10-16)::

    {
      "version": "2018-10-16",
      "labelingJobArn": "...",
      "labelAttributeName": "<job-label-attr>",
      "roleArn": "...",
      "payload": { "s3Uri": "s3://.../annotations.json" }
    }

The object at ``payload.s3Uri`` is a JSON list of dataset objects::

    [
      {
        "datasetObjectId": "0",
        "dataObject": { "source": "...", "ofacMetadata": [ ... ] },
        "annotations": [
          { "workerId": "...", "annotationData": { "content": "<json string>" } }
        ]
      }
    ]

Return: a list of consolidated label objects written by Ground Truth to the
output manifest.
"""

import json
from urllib.parse import urlparse


def _read_s3_json(s3_uri):
    import boto3  # provided by the Lambda runtime; imported lazily for testability

    parsed = urlparse(s3_uri)
    bucket, key = parsed.netloc, parsed.path.lstrip("/")
    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


def _parse_worker_content(annotation):
    """Return the entity list from one worker's annotationData content.

    The crowd-entity-annotation element serializes as either::

        {"crowd-entity-annotation": {"entities": [...]}}
    or  {"annotatedResult": {"entities": [...]}}   (keyed by the element name)

    so we defensively pull the first dict value that carries an ``entities`` key.
    """
    content = annotation.get("annotationData", {}).get("content", "{}")
    if isinstance(content, str):
        content = json.loads(content)
    if "entities" in content:
        return content.get("entities", [])
    for value in content.values():
        if isinstance(value, dict) and "entities" in value:
            return value.get("entities", [])
    return []


def _ofac_by_offset(data_object):
    meta = data_object.get("ofacMetadata") or []
    return {m["startOffset"]: m.get("ofacId") for m in meta}


def _consolidate_one(dataset_object, label_attribute_name):
    annotations = dataset_object.get("annotations", [])
    ofac_map = _ofac_by_offset(dataset_object.get("dataObject", {}))

    entities = []
    if annotations:
        # Single-worker job: take the first (only) worker's answer.
        for ent in _parse_worker_content(annotations[0]):
            # Re-attach the OFAC ID if the worker/crowd element dropped it.
            if "ofacId" not in ent and ent.get("startOffset") in ofac_map:
                ent = {**ent, "ofacId": ofac_map[ent["startOffset"]]}
            entities.append(ent)

    return {
        "datasetObjectId": dataset_object["datasetObjectId"],
        "consolidatedAnnotation": {
            "content": {
                label_attribute_name: {"entities": entities},
            }
        },
    }


def lambda_handler(event, context):
    label_attribute_name = event["labelAttributeName"]
    dataset_objects = _read_s3_json(event["payload"]["s3Uri"])
    return [_consolidate_one(obj, label_attribute_name) for obj in dataset_objects]
