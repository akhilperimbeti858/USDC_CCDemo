"""Pre-annotation logic (mirror of lambdas/pre_annotation/handler.py).

Transforms a raw manifest record into the ``taskInput`` consumed by the custom
Crowd-HTML template. Kept AWS-free so it can run in the local simulator; the
`source-ref` S3 path is delegated to an injectable reader for testability.
"""

# OFAC categories emitted by the upstream Comprehend custom recognizer.
DEFAULT_LABELS = ["OFAC_ORG", "OFAC_POI", "FTO"]


def _labels_for(data_object, fallback):
    """Resolve the label set, preferring the per-record manifest config.

    Manifest carries the label set as ``{"labels": [{"label": "PERSON"}, ...]}``;
    a bare array is tolerated. Falls back to ``fallback`` when absent.
    """
    cfg = data_object.get("labels")
    if isinstance(cfg, dict) and isinstance(cfg.get("labels"), list):
        return cfg["labels"]
    if isinstance(cfg, list):
        return cfg
    return fallback


def build_task_input(data_object, labels=None, source_ref_reader=None):
    """Return the ``taskInput`` dict for one dataset object.

    Args:
        data_object: a manifest record, e.g.
            {"source": "...", "labels": {"labels": [...]},
             "initialEntities": [...], "ofac_metadata": [...]}
            or {"source-ref": "s3://...", ...}. The legacy field names
            ``initialValue``/``ofacMetadata`` are still accepted.
        labels: explicit override for the entity label set; when omitted the
            per-record ``labels`` config is used, else DEFAULT_LABELS.
        source_ref_reader: optional callable(s3_uri) -> str, used only when the
            record carries a ``source-ref`` instead of inline ``source``.
    """
    if labels is None:
        labels = _labels_for(data_object, list(DEFAULT_LABELS))

    if data_object.get("source") is not None:
        text = data_object["source"]
    elif data_object.get("source-ref"):
        if source_ref_reader is None:
            raise ValueError("source-ref record requires a source_ref_reader")
        text = source_ref_reader(data_object["source-ref"])
    else:
        raise KeyError("dataObject must contain either 'source' or 'source-ref'")

    initial = data_object.get("initialEntities")
    if initial is None:
        initial = data_object.get("initialValue")

    ofac = data_object.get("ofac_metadata")
    if ofac is None:
        ofac = data_object.get("ofacMetadata")

    return {
        "taskObject": text.rstrip(),
        "labels": labels,
        "initialValue": initial or [],
        "ofacMetadata": ofac or [],
    }
