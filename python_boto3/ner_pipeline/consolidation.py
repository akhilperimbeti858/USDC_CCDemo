"""Annotation consolidation logic (mirrors both post-annotation Lambdas).

`consolidate_single` -> single-worker pass-through (re-attaches OFAC IDs).
`consolidate_merge`  -> multi-worker overlap-cluster + majority vote.

Both accept a list of "dataset objects" in Ground Truth consolidation shape and
return the list of consolidated label objects. They are AWS-free; the local
simulator and the boto3 launcher's local test mode both call them directly.
"""

import json
from collections import Counter

# Fraction of workers that must contribute an overlapping span for it to survive.
AGREEMENT_RATIO = 0.5


def _parse_worker_content(annotation):
    """Extract the entity list from one worker's annotationData.content."""
    content = annotation.get("annotationData", {}).get("content", {})
    if isinstance(content, str):
        content = json.loads(content)
    if "entities" in content:
        return content.get("entities", [])
    for value in content.values():
        if isinstance(value, dict) and "entities" in value:
            return value.get("entities", [])
    return []


def _ofac_by_offset(data_object):
    # Current manifest field is `ofac_metadata`; `ofacMetadata` is legacy.
    meta = data_object.get("ofac_metadata")
    if meta is None:
        meta = data_object.get("ofacMetadata")
    return {m["startOffset"]: m.get("ofacId") for m in (meta or [])}


def _worker_ofac_overrides(annotation):
    """OFAC IDs the annotator typed in the UI modal, keyed by startOffset.

    Submitted in the hidden `ofacOverrides` form field (JSON array). Empty ignored.
    """
    content = annotation.get("annotationData", {}).get("content", "{}")
    if isinstance(content, str):
        content = json.loads(content)
    raw = content.get("ofacOverrides") or []
    if isinstance(raw, str):
        raw = json.loads(raw or "[]")
    return {m["startOffset"]: m.get("ofacId") for m in raw if m.get("ofacId")}


def _wrap(dataset_object_id, label_attribute_name, entities):
    return {
        "datasetObjectId": dataset_object_id,
        "consolidatedAnnotation": {
            "content": {label_attribute_name: {"entities": entities}}
        },
    }


def consolidate_single(dataset_objects, label_attribute_name):
    """Single-worker pass-through consolidation."""
    results = []
    for obj in dataset_objects:
        annotations = obj.get("annotations", [])
        ofac_map = _ofac_by_offset(obj.get("dataObject", {}))
        entities = []
        if annotations:
            # Annotator-entered OFAC IDs win over the manifest seed.
            ofac_map = {**ofac_map, **_worker_ofac_overrides(annotations[0])}
            for ent in _parse_worker_content(annotations[0]):
                if "ofacId" not in ent and ent.get("startOffset") in ofac_map:
                    ent = {**ent, "ofacId": ofac_map[ent["startOffset"]]}
                entities.append(ent)
        results.append(_wrap(obj["datasetObjectId"], label_attribute_name, entities))
    return results


def _cluster_spans(spans):
    """Group spans into clusters of transitively overlapping character ranges."""
    order = sorted(range(len(spans)), key=lambda i: spans[i]["startOffset"])
    clusters, current, current_end = [], [], None
    for idx in order:
        span = spans[idx]
        if current and span["startOffset"] < current_end:
            current.append(span)
            current_end = max(current_end, span["endOffset"])
        else:
            if current:
                clusters.append(current)
            current, current_end = [span], span["endOffset"]
    if current:
        clusters.append(current)
    return clusters


def _consolidate_cluster(cluster, num_workers):
    contributing = {s["_worker"] for s in cluster}
    agreement = len(contributing) / num_workers if num_workers else 0.0
    if agreement < AGREEMENT_RATIO:
        return None
    label = Counter(s["label"] for s in cluster).most_common(1)[0][0]
    start, end = Counter(
        (s["startOffset"], s["endOffset"]) for s in cluster if s["label"] == label
    ).most_common(1)[0][0]
    # No voting on OFAC IDs: keep the first non-empty entered ID in the cluster.
    ofac_id = next((s.get("ofacId") for s in cluster if s.get("ofacId")), None)
    entity = {
        "label": label,
        "startOffset": start,
        "endOffset": end,
        "confidence": round(agreement, 4),
    }
    if ofac_id:
        entity["ofacId"] = ofac_id
    return entity


def consolidate_merge(dataset_objects, label_attribute_name):
    """Multi-worker overlap-cluster + majority-vote consolidation."""
    results = []
    for obj in dataset_objects:
        annotations = obj.get("annotations", [])
        num_workers = len(annotations)
        manifest_map = _ofac_by_offset(obj.get("dataObject", {}))
        spans = []
        for ann in annotations:
            worker = ann.get("workerId", id(ann))
            overrides = _worker_ofac_overrides(ann)
            for ent in _parse_worker_content(ann):
                start = ent.get("startOffset")
                ofac_id = overrides.get(start) or manifest_map.get(start)
                span = {**ent, "_worker": worker}
                if ofac_id and "ofacId" not in span:
                    span["ofacId"] = ofac_id
                spans.append(span)
        entities = []
        for cluster in _cluster_spans(spans):
            merged = _consolidate_cluster(cluster, num_workers)
            if merged is not None:
                entities.append(merged)
        entities.sort(key=lambda e: e["startOffset"])
        results.append(_wrap(obj["datasetObjectId"], label_attribute_name, entities))
    return results
