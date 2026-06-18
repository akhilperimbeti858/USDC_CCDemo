"""Post-annotation (consolidation) Lambda - MULTI-WORKER MERGE variant.

Same Ground Truth I/O contract as the single-worker variant, but consolidates
the entity spans of *several* workers per object into one agreed answer.

Merge algorithm (all knobs are module-level constants):
  1. Collect every worker's entity spans for the object.
  2. Cluster spans whose character ranges overlap (union-find over intervals).
  3. For each cluster, majority-vote the label and pick the most-agreed
     (startOffset, endOffset) boundary.
  4. Keep the span only if at least AGREEMENT_RATIO of the workers contributed a
     span to that cluster.
  5. Emit the consolidated entity with a `confidence` (fraction of workers that
     agreed) and the majority `ofacId`.

Input/return shapes match `post_annotation_single`; see that module's docstring.
"""

import json
from collections import Counter
from urllib.parse import urlparse

# Fraction of workers that must contribute an overlapping span for it to survive.
# 0.5 => a strict majority (>= ceil(N/2)).
AGREEMENT_RATIO = 0.5


def _read_s3_json(s3_uri):
    import boto3  # provided by the Lambda runtime; imported lazily for testability

    parsed = urlparse(s3_uri)
    bucket, key = parsed.netloc, parsed.path.lstrip("/")
    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


def _parse_worker_content(annotation):
    """Return the entity list from one worker's annotationData content."""
    content = annotation.get("annotationData", {}).get("content", "{}")
    if isinstance(content, str):
        content = json.loads(content)
    if "entities" in content:
        return content.get("entities", [])
    for value in content.values():
        if isinstance(value, dict) and "entities" in value:
            return value.get("entities", [])
    return []


def _overlaps(a, b):
    """True when two spans share at least one character position."""
    return a["startOffset"] < b["endOffset"] and b["startOffset"] < a["endOffset"]


def _cluster_spans(spans):
    """Group spans into clusters of mutually/transitively overlapping ranges."""
    order = sorted(range(len(spans)), key=lambda i: spans[i]["startOffset"])
    clusters = []
    current = []
    current_end = None
    for idx in order:
        span = spans[idx]
        if current and span["startOffset"] < current_end:
            current.append(span)
            current_end = max(current_end, span["endOffset"])
        else:
            if current:
                clusters.append(current)
            current = [span]
            current_end = span["endOffset"]
    if current:
        clusters.append(current)
    return clusters


def _consolidate_cluster(cluster, num_workers):
    """Reduce one overlap cluster to a single agreed entity (or None)."""
    # Each worker contributes at most once toward agreement for this cluster.
    contributing_workers = {s["_worker"] for s in cluster}
    agreement = len(contributing_workers) / num_workers if num_workers else 0.0
    if agreement < AGREEMENT_RATIO:
        return None

    label = Counter(s["label"] for s in cluster).most_common(1)[0][0]
    # Most-agreed exact boundary among spans carrying the winning label.
    boundary = Counter(
        (s["startOffset"], s["endOffset"]) for s in cluster if s["label"] == label
    ).most_common(1)[0][0]
    start, end = boundary

    ofac_ids = [s.get("ofacId") for s in cluster if s.get("ofacId")]
    ofac_id = Counter(ofac_ids).most_common(1)[0][0] if ofac_ids else None

    entity = {
        "label": label,
        "startOffset": start,
        "endOffset": end,
        "confidence": round(agreement, 4),
    }
    if ofac_id is not None:
        entity["ofacId"] = ofac_id
    return entity


def _consolidate_one(dataset_object, label_attribute_name):
    annotations = dataset_object.get("annotations", [])
    num_workers = len(annotations)

    # Flatten all workers' spans, tagging each with its worker id for vote counting.
    all_spans = []
    for ann in annotations:
        worker_id = ann.get("workerId", id(ann))
        for ent in _parse_worker_content(ann):
            all_spans.append({**ent, "_worker": worker_id})

    entities = []
    for cluster in _cluster_spans(all_spans):
        merged = _consolidate_cluster(cluster, num_workers)
        if merged is not None:
            entities.append(merged)
    entities.sort(key=lambda e: e["startOffset"])

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
