#!/usr/bin/env python3
"""Run the NER labeling pipeline end-to-end locally (no AWS).

Examples:
    python run_local_simulation.py --mode single
    python run_local_simulation.py --mode merge

It reads the shared manifest (../manifests/input.manifest.example) and a set of
simulated worker answers from sample_data/, runs pre-annotation + consolidation,
and prints the resulting taskInputs and consolidated output manifest entries.
"""

import argparse
import json
import os

from ner_pipeline.local_simulator import load_manifest, simulate

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.join(HERE, "..", "manifests", "input.manifest.example")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["single", "merge"], default="single")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--label-attribute-name", default="ner-labels")
    ap.add_argument("--answers", help="Path to worker answers JSON (defaults by mode).")
    args = ap.parse_args()

    answers_path = args.answers or os.path.join(
        HERE, "sample_data", f"worker_answers.{args.mode}.json"
    )

    records = load_manifest(args.manifest)
    with open(answers_path) as fh:
        worker_answers = json.load(fh)

    report = simulate(records, worker_answers, mode=args.mode,
                      label_attribute_name=args.label_attribute_name)

    print(f"=== Pre-annotation taskInputs (mode={report['mode']}) ===")
    for i, ti in enumerate(report["task_inputs"]):
        print(f"[{i}] text={ti['taskObject'][:50]!r} ofac={len(ti['ofacMetadata'])} labels={ti['labels']}")

    print("\n=== Consolidated output manifest entries ===")
    print(json.dumps(report["consolidated"], indent=2))


if __name__ == "__main__":
    main()
