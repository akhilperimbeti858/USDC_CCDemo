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

Each Comprehend document becomes one manifest record:

    {
      "source-ref":      "s3://<docs>/doc1.txt",         # text fetched at render time
      "labels":          {"labels": [{"label": "OFAC_ORG"}, ...]},
      "initialEntities": [{"label": "OFAC_ORG", "startOffset": 0, "endOffset": 9}, ...],
      "ofac_metadata":   []                               # analysis job; humans add IDs
    }

Comprehend's `BeginOffset`/`EndOffset`/`Type` map to the manifest's
`startOffset`/`endOffset`/`label`. The recognizer is assumed to emit the OFAC types
directly; entities of any other type are dropped. `ofac_metadata` is always empty
(an incoming analysis job), which keeps the manifest invariant intact.

Pure and AWS-free so it is unit-testable and runnable offline.
"""

import json

# Entity types kept from the Comprehend output and presented to workers.
OFAC_LABELS = ["OFAC_ORG", "OFAC_POI", "FTO"]


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
    # `or []` handles docs with no detections: "Entities": [], null, or a missing key.
    for ent in (doc.get("Entities") or []):
        if ent.get("Type") not in allowed_set:
            continue
        if min_score is not None and ent.get("Score", 1.0) < min_score:
            continue
        initial_entities.append({
            "label": ent["Type"],
            "startOffset": ent["BeginOffset"],
            "endOffset": ent["EndOffset"],
        })

    return {
        "source-ref": _source_ref(s3_docs_base, doc["File"]),
        "labels": _labels_config(label_set),
        "initialEntities": initial_entities,
        "ofac_metadata": [],
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
