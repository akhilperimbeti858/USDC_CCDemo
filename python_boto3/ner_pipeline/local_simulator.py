"""Offline end-to-end simulator for the NER labeling workflow.

Runs the exact pre-annotation -> (provided worker answers) -> consolidation flow
that Ground Truth would run, but entirely on the local machine with no AWS calls.
Use it to validate the manifest, the taskInput contract, and the consolidation
behavior before spending money on a real labeling job.
"""

import json

from .pre_annotation import build_task_input
from .consolidation import consolidate_single


def load_manifest(path):
    """Read a JSON-Lines Ground Truth manifest into a list of records."""
    records = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def build_consolidation_input(records, worker_answers):
    """Assemble the Ground Truth consolidation payload from records + answers.

    Args:
        records: list of manifest records (the dataObjects).
        worker_answers: list (parallel to records) of lists of worker entity
            sets, i.e. worker_answers[i] = [entities_from_worker_1, ...].
    """
    dataset_objects = []
    for i, (record, answers) in enumerate(zip(records, worker_answers)):
        # The UI submits the entity spans AND a hidden `metaData` field (seeded from
        # the record's metaData, then edited). We seed it from the record so the
        # confidence flows through; ofacID stays the "FILL" placeholder.
        record_meta = record.get("metaData", [])
        annotations = [
            {
                "workerId": f"worker-{w}",
                "annotationData": {"content": json.dumps({
                    "annotatedResult": {"entities": ents},
                    "metaData": json.dumps(record_meta),
                })},
            }
            for w, ents in enumerate(answers)
        ]
        dataset_objects.append(
            {"datasetObjectId": str(i), "dataObject": record, "annotations": annotations}
        )
    return dataset_objects


def simulate(records, worker_answers, label_attribute_name="ner-labels"):
    """Run pre-annotation + single-worker consolidation locally and return a report.

    Returns a dict with the rendered taskInputs and the consolidated output
    manifest entries, so callers can assert on the full pipeline.
    """
    task_inputs = [build_task_input(r) for r in records]

    dataset_objects = build_consolidation_input(records, worker_answers)
    consolidated = consolidate_single(dataset_objects, label_attribute_name)

    return {
        "label_attribute_name": label_attribute_name,
        "task_inputs": task_inputs,
        "consolidated": consolidated,
    }
