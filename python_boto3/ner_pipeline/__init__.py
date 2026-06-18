"""Pure-function NER labeling pipeline (no AWS dependency).

These functions mirror the Lambda handlers under ``/lambdas`` but operate on
plain Python objects so the entire pre-annotation -> consolidation flow can be
unit-tested and simulated locally. The AWS launcher (``aws_launcher.py``) and the
local simulator (``local_simulator.py``) both build on top of these.
"""

from .pre_annotation import build_task_input
from .consolidation import consolidate_single, consolidate_merge, AGREEMENT_RATIO

__all__ = [
    "build_task_input",
    "consolidate_single",
    "consolidate_merge",
    "AGREEMENT_RATIO",
]
