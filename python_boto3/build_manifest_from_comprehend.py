#!/usr/bin/env python3
"""Build a Ground Truth input manifest from AWS Comprehend custom-entity output.

The Comprehend recognizer's output, parsed to a JSON array of per-document objects
(`[{"File": "doc1.txt", "Entities": [...]}, ...]`), is converted into the JSON-Lines
manifest the labeling pipeline consumes. Each document's detected OFAC entities seed
the human-review UI (`initialEntities`); the document text is referenced via
`source-ref` and fetched from S3 at render time.

Example:
    python build_manifest_from_comprehend.py \
        --comprehend parsed_comprehend.json \
        --s3-docs-base s3://my-bucket/docs/ \
        --out ../manifests/input.manifest

Optional: --min-score 0.5 drops low-confidence entities; --labels overrides the
kept entity types / label set (default: OFAC_ORG OFAC_POI FTO).
"""

import argparse
import json

from ner_pipeline.comprehend_to_manifest import (
    OFAC_LABELS,
    comprehend_to_records,
    write_manifest,
)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--comprehend", required=True,
                    help="Path to the parsed Comprehend output JSON (array of {File, Entities}).")
    ap.add_argument("--s3-docs-base", required=True,
                    help="S3 prefix where the source documents live, e.g. s3://bucket/docs/.")
    ap.add_argument("--out", required=True, help="Output manifest path (JSON Lines).")
    ap.add_argument("--min-score", type=float, default=None,
                    help="Drop entities whose Comprehend Score is below this threshold.")
    ap.add_argument("--labels", nargs="+", default=list(OFAC_LABELS),
                    help="Entity types to keep / label set (default: OFAC_ORG OFAC_POI FTO).")
    args = ap.parse_args()

    with open(args.comprehend) as fh:
        docs = json.load(fh)

    records = comprehend_to_records(
        docs, args.s3_docs_base, allowed_types=args.labels,
        min_score=args.min_score, labels=args.labels,
    )
    write_manifest(records, args.out)

    seeded = sum(len(r["initialEntities"]) for r in records)
    print(f"Wrote {len(records)} record(s) ({seeded} seed entit{'y' if seeded == 1 else 'ies'}) "
          f"to {args.out}")


if __name__ == "__main__":
    main()
