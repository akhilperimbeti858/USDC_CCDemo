"""Convert AWS Comprehend custom-entity output into a Ground Truth manifest.

The real input to this workflow is the direct output of an AWS Comprehend custom
entity recognizer, parsed into a JSON array of per-document objects::

    [
      { "File": "doc1.txt",
        "Entities": [
          { "Score": 0.99, "Type": "OFAC_ORG", "Text": "Acme Corp",
            "BeginOffset": 0, "EndOffset": 9 },
          ...
        ] },
      ...
    ]

This module turns that into the JSON-Lines manifest the rest of the pipeline already
consumes (one record per line), so Ground Truth can present Comprehend's predictions
to a human reviewer who confirms them and adds OFAC IDs.

Each Comprehend document becomes one manifest record with two PARALLEL arrays:

    {
      "source-ref":      "s3://<docs>/doc1.txt",         # text fetched at render time
      "labels":          {"labels": [{"label": "OFAC_ORG"}, ...]},
      "initialEntities": [{"startOffset": 0, "endOffset": 9, "label": "OFAC_ORG"}, ...],
      "metaData":        [{"startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "FILL"}, ...]
    }

Comprehend's `BeginOffset`/`EndOffset`/`Type` map to `startOffset`/`endOffset`/`label`;
each entity's `Score` becomes the parallel `metaData.confidence`, with a placeholder
`ofacID` of "FILL" that the human annotator replaces in the UI. The recognizer is
assumed to emit the OFAC types directly; entities of any other type are dropped.

Pure and AWS-free so it is unit-testable and runnable offline. Mirrors the deployed
``lambdas/comprehend_to_manifest/handler.py``.
"""

import json

# Entity types kept from the Comprehend output and presented to workers.
OFAC_LABELS = ["OFAC_ORG", "OFAC_POI", "FTO"]


def extract_comprehend_docs(tar_bytes):
    """Unzip a Comprehend ``output.tar.gz`` (bytes) into a list of document objects.

    The async job artifact is a gzipped tar holding one or more JSON-Lines members,
    one document per line (``{"File": ..., "Entities": [...]}``). We read every
    regular-file member and parse each non-empty line. Pure/offline; the deployed
    Lambda (``lambdas/comprehend_to_manifest/handler.py``) mirrors this.
    """
    import io
    import tarfile

    docs = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            for line in extracted.read().decode("utf-8").splitlines():
                line = line.strip()
                if line:
                    docs.append(json.loads(line))
    return docs


def _labels_config(labels):
    """The per-record label-set config shape consumed by the pre-annotation step."""
    return {"labels": [{"label": label} for label in labels]}


def _source_ref(s3_docs_base, file_name):
    """Join the S3 docs prefix with a Comprehend `File` value into an S3 URI."""
    return s3_docs_base.rstrip("/") + "/" + str(file_name).lstrip("/")


def comprehend_doc_to_record(doc, s3_docs_base, allowed_types=None,
                             min_score=None, labels=None):
    """Convert one Comprehend document object into a manifest record.

    Args:
        doc: a parsed Comprehend object, e.g. {"File": "doc1.txt", "Entities": [...]}.
        s3_docs_base: S3 prefix where the source documents live (e.g. "s3://b/docs/").
        allowed_types: entity Types to keep (defaults to OFAC_LABELS); others dropped.
        min_score: if set, drop entities whose `Score` is below this threshold.
        labels: label set written into the record's `labels` config (defaults to the
            kept types / OFAC_LABELS).
    """
    allowed = list(allowed_types) if allowed_types is not None else list(OFAC_LABELS)
    label_set = list(labels) if labels is not None else list(allowed)
    allowed_set = set(allowed)

    initial_entities = []
    meta_data = []
    # `or []` handles docs with no detections: "Entities": [], null, or a missing key.
    for ent in (doc.get("Entities") or []):
        if ent.get("Type") not in allowed_set:
            continue
        if min_score is not None and ent.get("Score", 1.0) < min_score:
            continue
        start, end = ent["BeginOffset"], ent["EndOffset"]
        initial_entities.append({
            "startOffset": start,
            "endOffset": end,
            "label": ent["Type"],
        })
        meta_data.append({
            "startOffset": start,
            "endOffset": end,
            "confidence": ent.get("Score"),
            "ofacID": "FILL",
        })

    return {
        "source-ref": _source_ref(s3_docs_base, doc["File"]),
        "labels": _labels_config(label_set),
        "initialEntities": initial_entities,
        "metaData": meta_data,
    }


def comprehend_to_records(docs, s3_docs_base, allowed_types=None,
                          min_score=None, labels=None):
    """Convert a list of Comprehend document objects into manifest records."""
    return [
        comprehend_doc_to_record(
            doc, s3_docs_base, allowed_types=allowed_types,
            min_score=min_score, labels=labels,
        )
        for doc in docs
    ]


def write_manifest(records, path):
    """Write records as a JSON-Lines manifest (one record per line)."""
    with open(path, "w") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    return path
