"""Pre-annotation logic (mirror of lambdas/pre_annotation/handler.py).

Transforms a raw manifest record into the ``taskInput`` consumed by the custom
Crowd-HTML template. Kept AWS-free so it can run in the local simulator; the
`source-ref` S3 path is delegated to an injectable reader for testability.
"""

DEFAULT_LABELS = ["PERSON", "ORG", "LOC", "SANCTIONED_ENTITY"]


def build_task_input(data_object, labels=None, source_ref_reader=None):
    """Return the ``taskInput`` dict for one dataset object.

    Args:
        data_object: a manifest record, e.g.
            {"source": "...", "ofacMetadata": [...], "initialValue": [...]}
            or {"source-ref": "s3://...", ...}.
        labels: entity label set (defaults to DEFAULT_LABELS).
        source_ref_reader: optional callable(s3_uri) -> str, used only when the
            record carries a ``source-ref`` instead of inline ``source``.
    """
    labels = labels or list(DEFAULT_LABELS)

    if data_object.get("source") is not None:
        text = data_object["source"]
    elif data_object.get("source-ref"):
        if source_ref_reader is None:
            raise ValueError("source-ref record requires a source_ref_reader")
        text = source_ref_reader(data_object["source-ref"])
    else:
        raise KeyError("dataObject must contain either 'source' or 'source-ref'")

    return {
        "taskObject": text.rstrip(),
        "labels": labels,
        "initialValue": data_object.get("initialValue") or [],
        "ofacMetadata": data_object.get("ofacMetadata") or [],
    }
