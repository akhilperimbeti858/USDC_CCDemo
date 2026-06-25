"""Post-annotation (consolidation) Lambda - SINGLE-WORKER variant.

Ground Truth invokes this once a batch of dataset objects has been fully
annotated. With exactly one worker per object, consolidation is a straight
pass-through of that worker's entity spans, plus a parallel ``metaData`` array
carrying each span's ``confidence`` and (when the annotator entered one) its
``ofacID``.

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
        "dataObject": { "source": "...", "metaData": [ ... ] },
        "annotations": [
          { "workerId": "...", "annotationData": { "content": "<json string>" } }
        ]
      }
    ]

Output (per object) content::

    { "<labelAttr>": {
        "entities": [ {"startOffset":0,"endOffset":4,"label":"FTO"}, ... ],
        "metaData": [ {"startOffset":0,"endOffset":4,"confidence":0.99,"ofacID":"OFAC_1234"}, ... ]
    } }

``entities`` and ``metaData`` are parallel (one entry per worker span, aligned by
offset). ``ofacID`` is included ONLY when the annotator actually entered one; the
seeded "FILL" placeholder (or an empty value) is dropped from the output.
"""

import json
from urllib.parse import urlparse

# Placeholder OFAC ID the converter seeds and the UI shows. Dropped from the
# output unless the annotator replaced it with a real ID.
_FILL = "FILL"


def _read_s3_json(s3_uri):
    import boto3  # provided by the Lambda runtime; imported lazily for testability

    parsed = urlparse(s3_uri)
    bucket, key = parsed.netloc, parsed.path.lstrip("/")
    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


def _content(annotation):
    """Parse one worker's annotationData.content (a JSON string or already a dict)."""
    content = annotation.get("annotationData", {}).get("content", "{}")
    if isinstance(content, str):
        content = json.loads(content)
    return content if isinstance(content, dict) else {}


def _worker_entities(content):
    """Return the entity list from the crowd-entity-annotation submission.

    Serializes as ``{"annotatedResult": {"entities": [...]}}`` (or keyed by the
    element name); we defensively pull the first dict value carrying ``entities``.
    """
    if "entities" in content:
        return content.get("entities", [])
    for value in content.values():
        if isinstance(value, dict) and "entities" in value:
            return value.get("entities", [])
    return []


def _worker_meta_by_offset(content):
    """Annotator-authored metaData (hidden ``metaData`` field), keyed by startOffset.

    Carries ``confidence`` (seeded) and ``ofacID`` (entered/edited in the UI), and
    reflects spans the annotator added or removed. Tolerates a JSON-string value.
    """
    raw = content.get("metaData") or []
    if isinstance(raw, str):
        raw = json.loads(raw or "[]")
    return {m["startOffset"]: m for m in raw if isinstance(m, dict) and "startOffset" in m}


def _seed_meta_by_offset(data_object):
    """Manifest-seeded metaData (confidence) keyed by startOffset."""
    meta = data_object.get("metaData") or []
    return {m["startOffset"]: m for m in meta if isinstance(m, dict) and "startOffset" in m}


def _consolidate_one(dataset_object, label_attribute_name):
    annotations = dataset_object.get("annotations", [])
    seed_meta = _seed_meta_by_offset(dataset_object.get("dataObject", {}))

    entities, meta_data = [], []
    if annotations:
        # Single-worker job: take the first (only) worker's submission.
        content = _content(annotations[0])
        worker_meta = _worker_meta_by_offset(content)
        for ent in _worker_entities(content):
            start, end = ent.get("startOffset"), ent.get("endOffset")
            entities.append({"startOffset": start, "endOffset": end, "label": ent.get("label")})

            wm = worker_meta.get(start, {})
            # Prefer the worker-submitted confidence; fall back to the manifest seed.
            confidence = wm.get("confidence", seed_meta.get(start, {}).get("confidence"))
            meta_entry = {"startOffset": start, "endOffset": end, "confidence": confidence}

            # Include ofacID only if the annotator entered a real value (not FILL).
            ofac = wm.get("ofacID")
            if ofac and ofac != _FILL:
                meta_entry["ofacID"] = ofac
            meta_data.append(meta_entry)

    return {
        "datasetObjectId": dataset_object["datasetObjectId"],
        "consolidatedAnnotation": {
            "content": {
                label_attribute_name: {"entities": entities, "metaData": meta_data},
            }
        },
    }


def lambda_handler(event, context):
    label_attribute_name = event["labelAttributeName"]
    dataset_objects = _read_s3_json(event["payload"]["s3Uri"])
    return [_consolidate_one(obj, label_attribute_name) for obj in dataset_objects]
