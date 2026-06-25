"""Annotation consolidation logic (mirror of the post-annotation Lambda).

`consolidate_single` -> single-worker pass-through. Emits, per object, the worker's
entity spans plus a parallel ``metaData`` array carrying each span's ``confidence``
and (when the annotator entered one) its ``ofacID``. The seeded "FILL" placeholder
is dropped from the output.

Accepts a list of "dataset objects" in Ground Truth consolidation shape and
returns the list of consolidated label objects. AWS-free; the local simulator
calls it directly.
"""

import json

# Placeholder OFAC ID seeded by the converter / shown in the UI. Dropped from the
# output unless the annotator replaced it with a real ID.
_FILL = "FILL"


def _content(annotation):
    """Parse one worker's annotationData.content (a JSON string or already a dict)."""
    content = annotation.get("annotationData", {}).get("content", {})
    if isinstance(content, str):
        content = json.loads(content)
    return content if isinstance(content, dict) else {}


def _worker_entities(content):
    """Extract the entity list from the crowd-entity-annotation submission."""
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


def _wrap(dataset_object_id, label_attribute_name, entities, meta_data):
    return {
        "datasetObjectId": dataset_object_id,
        "consolidatedAnnotation": {
            "content": {label_attribute_name: {"entities": entities, "metaData": meta_data}}
        },
    }


def consolidate_single(dataset_objects, label_attribute_name):
    """Single-worker pass-through consolidation (entities + parallel metaData)."""
    results = []
    for obj in dataset_objects:
        annotations = obj.get("annotations", [])
        seed_meta = _seed_meta_by_offset(obj.get("dataObject", {}))
        entities, meta_data = [], []
        if annotations:
            content = _content(annotations[0])  # single-worker: first (only) worker
            worker_meta = _worker_meta_by_offset(content)
            for ent in _worker_entities(content):
                start, end = ent.get("startOffset"), ent.get("endOffset")
                entities.append({"startOffset": start, "endOffset": end, "label": ent.get("label")})

                wm = worker_meta.get(start, {})
                confidence = wm.get("confidence", seed_meta.get(start, {}).get("confidence"))
                meta_entry = {"startOffset": start, "endOffset": end, "confidence": confidence}
                ofac = wm.get("ofacID")
                if ofac and ofac != _FILL:
                    meta_entry["ofacID"] = ofac
                meta_data.append(meta_entry)
        results.append(_wrap(obj["datasetObjectId"], label_attribute_name, entities, meta_data))
    return results
