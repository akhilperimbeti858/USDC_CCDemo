"""Offline dry-run for the consolidation merge (no AWS, no Databricks).

Run:  python consolidation/test_consolidate.py

Covers the four buckets, both user-described transitions (no-hit→hit, false-positive→
no-hit), multi-reviewer latest-mtime-wins + conflict reporting, partial saves never
overriding a finished doc, and that reviewed docs (incl. confirmed no-hits) partition.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "training_prep"))

import consolidate as C
from partition import partition_document, part_filenames


# --- The Comprehend input batch (what every reviewer starts from) -----------
INPUT_BATCH = {
    "labels": ["FTO", "POI", "ORG"],
    "documents": [
        # comprehend_hit=True  — a real hit the reviewer will confirm
        {"id": "d_hit_confirmed.txt", "text": "Acme Corp wired funds to the Red Brigade.",
         "initialEntities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"}]},
        # comprehend_hit=True  — a FALSE POSITIVE the reviewer will clear
        {"id": "d_false_positive.txt", "text": "The weather in Reading was mild today.",
         "initialEntities": [{"startOffset": 15, "endOffset": 22, "label": "ORG"}]},
        # comprehend_hit=False — Comprehend missed it; reviewer will ADD a span
        {"id": "d_new_hit.txt", "text": "John Doe moved USDC to a wallet in Tehran.",
         "initialEntities": []},
        # comprehend_hit=False — genuinely clean; reviewer confirms no entities
        {"id": "d_clean.txt", "text": "No sanctioned entities appear here at all.",
         "initialEntities": []},
        # never reviewed by anyone — stays unreviewed (comprehend_hit=True)
        {"id": "d_untouched.txt", "text": "Volkov Industries appears once.",
         "initialEntities": [{"startOffset": 0, "endOffset": 17, "label": "ORG"}]},
    ],
}

# --- Reviewer ALICE (older file): reviews 4 docs; leaves d_untouched alone ---
ALICE = {
    "source": "batch_7.alice.json", "mtime": 100, "annotatorID": "Alice",
    "documents": [
        {"file": "d_hit_confirmed.txt", "humanReviewRequired": False, "annotatorID": "Alice",
         "entities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"}]},
        {"file": "d_false_positive.txt", "humanReviewRequired": False, "annotatorID": "Alice",
         "entities": []},                                   # cleared the FP  -> no_hits_reviewed
        {"file": "d_new_hit.txt", "humanReviewRequired": False, "annotatorID": "Alice",
         "entities": [{"startOffset": 34, "endOffset": 40, "label": "POI"}]},  # added -> hits_reviewed
        {"file": "d_clean.txt", "humanReviewRequired": False, "annotatorID": "Alice",
         "entities": []},                                   # confirmed clean -> no_hits_reviewed
        # a PARTIAL save of d_untouched — still needs review, must NOT override
        {"file": "d_untouched.txt", "humanReviewRequired": True, "annotatorID": "Alice",
         "entities": []},
    ],
}

# --- Reviewer BOB (newer file): re-reviews d_hit_confirmed with a DIFFERENT span
BOB = {
    "source": "batch_7.bob.json", "mtime": 200, "annotatorID": "Bob",
    "documents": [
        {"file": "d_hit_confirmed.txt", "humanReviewRequired": False, "annotatorID": "Bob",
         "entities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"},
                      {"startOffset": 29, "endOffset": 39, "label": "FTO"}]},
    ],
}


def main():
    result = C.consolidate(INPUT_BATCH, [BOB, ALICE])   # unordered on purpose; mtime sorts them
    by_id = {r["id"]: r for r in result["consolidated"]}

    def expect(cond, msg):
        if not cond:
            raise AssertionError(msg)

    # --- categories ---------------------------------------------------------
    expect(by_id["d_hit_confirmed.txt"]["category"] == C.HITS_REVIEWED, "confirmed hit")
    expect(by_id["d_false_positive.txt"]["category"] == C.NO_HITS_REVIEWED, "false positive -> no_hits_reviewed")
    expect(by_id["d_new_hit.txt"]["category"] == C.HITS_REVIEWED, "no-hit + human span -> hits_reviewed")
    expect(by_id["d_clean.txt"]["category"] == C.NO_HITS_REVIEWED, "confirmed clean")
    expect(by_id["d_untouched.txt"]["category"] == C.HITS_UNREVIEWED, "untouched w/ seed -> hits_unreviewed")

    # --- comprehend_hit is immutable regardless of the human outcome --------
    expect(by_id["d_new_hit.txt"]["comprehend_hit"] is False, "new_hit was a comprehend miss")
    expect(by_id["d_new_hit.txt"]["final_hit"] is True, "new_hit is a final hit")
    expect(by_id["d_false_positive.txt"]["comprehend_hit"] is True, "FP was a comprehend hit")
    expect(by_id["d_false_positive.txt"]["final_hit"] is False, "FP is not a final hit")

    # --- latest mtime wins + conflict recorded ------------------------------
    expect(by_id["d_hit_confirmed.txt"]["annotatorID"] == "Bob", "newest reviewer wins")
    expect(len(by_id["d_hit_confirmed.txt"]["entities"]) == 2, "Bob's 2-span review won")
    conflicts = result["summary"]["conflicts"]
    expect(len(conflicts) == 1 and conflicts[0]["id"] == "d_hit_confirmed.txt", "one conflict recorded")
    expect(conflicts[0]["resolved"] == "Bob", "conflict resolved to Bob")

    # --- partial save did NOT override d_untouched --------------------------
    expect(by_id["d_untouched.txt"]["reviewed"] is False, "partial save must not mark reviewed")

    # --- summary counts -----------------------------------------------------
    counts = result["summary"]["counts"]
    expect(counts[C.HITS_REVIEWED] == 2, f"hits_reviewed==2, got {counts}")
    expect(counts[C.NO_HITS_REVIEWED] == 2, f"no_hits_reviewed==2, got {counts}")
    expect(counts[C.HITS_UNREVIEWED] == 1, f"hits_unreviewed==1, got {counts}")
    expect(counts[C.NO_HITS_UNREVIEWED] == 0, f"no_hits_unreviewed==0, got {counts}")
    expect(result["summary"]["reviewed"] == 4 and result["summary"]["total"] == 5, "4/5 reviewed")

    # --- training set = all reviewed docs (hits AND confirmed no-hits) ------
    training = result["training"]
    ids = {t["file"] for t in training}
    expect(ids == {"d_hit_confirmed.txt", "d_false_positive.txt", "d_new_hit.txt", "d_clean.txt"},
           f"training = reviewed docs only, got {ids}")
    # confirmed no-hits are valid zero-entity examples
    clean = next(t for t in training if t["file"] == "d_clean.txt")
    expect(clean["entities"] == [], "clean doc trains as a zero-entity example")

    # --- reviewed docs partition without error (reuses training_prep) -------
    for t in training:
        parts = partition_document(t["text"], t["entities"], 5000)
        names = part_filenames(t["file"], len(parts))
        expect(len(names) == len(parts) >= 1, "partition returns >=1 named part")

    print("OK — consolidation dry-run passed")
    print("  buckets:", counts)
    print("  conflicts:", conflicts)
    print("  training docs:", sorted(ids))


if __name__ == "__main__":
    main()
