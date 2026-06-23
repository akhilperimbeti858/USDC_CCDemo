#!/usr/bin/env python3
"""Run the NER labeling pipeline end-to-end locally (no AWS).

Example:
    python run_local_simulation.py

It reads the shared manifest (../manifests/input.manifest.example) and a set of
simulated worker answers from sample_data/, runs pre-annotation + single-worker
consolidation, and prints the resulting taskInputs and consolidated output
manifest entries.
"""

import argparse
import json
import os

from ner_pipeline.local_simulator import load_manifest, simulate

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.join(HERE, "..", "manifests", "input.manifest.example")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--label-attribute-name", default="ner-labels")
    ap.add_argument("--answers", help="Path to worker answers JSON.",
                    default=os.path.join(HERE, "sample_data", "worker_answers.single.json"))
    args = ap.parse_args()

    records = load_manifest(args.manifest)
    with open(args.answers) as fh:
        worker_answers = json.load(fh)

    report = simulate(records, worker_answers,
                      label_attribute_name=args.label_attribute_name)

    print("=== Pre-annotation taskInputs (single-worker consolidation) ===")
    for i, ti in enumerate(report["task_inputs"]):
        print(f"[{i}] text={ti['taskObject'][:50]!r} ofac={len(ti['ofacMetadata'])} labels={ti['labels']}")

    print("\n=== Consolidated output manifest entries ===")
    print(json.dumps(report["consolidated"], indent=2))


if __name__ == "__main__":
    main()
