"""Pure-function NER labeling pipeline (no AWS dependency).

These functions mirror the Lambda handlers under ``/lambdas`` but operate on
plain Python objects so the entire pre-annotation -> consolidation flow can be
unit-tested and simulated locally. The AWS launcher (``aws_launcher.py``) and the
local simulator (``local_simulator.py``) both build on top of these.
"""

from .pre_annotation import build_task_input
from .consolidation import consolidate_single
from .comprehend_to_manifest import (
    comprehend_to_records,
    comprehend_doc_to_record,
    extract_comprehend_docs,
    write_manifest,
    OFAC_LABELS,
)

__all__ = [
    "build_task_input",
    "consolidate_single",
    "comprehend_to_records",
    "comprehend_doc_to_record",
    "extract_comprehend_docs",
    "write_manifest",
    "OFAC_LABELS",
]
