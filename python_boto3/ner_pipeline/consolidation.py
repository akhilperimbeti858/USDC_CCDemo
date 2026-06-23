"""Annotation consolidation logic (mirror of the post-annotation Lambda).

`consolidate_single` -> single-worker pass-through (re-attaches OFAC IDs).

Accepts a list of "dataset objects" in Ground Truth consolidation shape and
returns the list of consolidated label objects. AWS-free; the local simulator
calls it directly.
"""

import json


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
