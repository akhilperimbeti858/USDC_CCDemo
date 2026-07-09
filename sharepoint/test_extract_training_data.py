"""Offline test for the SharePoint parsing step.

Run:  python sharepoint/test_extract_training_data.py

Confirms only humanReviewRequired==false docs are pulled (partial files contribute their
finished docs only), reviewed no-hit docs become zero-entity training examples, and the
same doc across two files is de-duplicated (last wins).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import extract_training_data as X


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    # A partially reviewed export: A1 (hit) + A2 (confirmed clean) reviewed, A3 not.
    partial = {
        "documents": [
            {"file": "training_doc_A1.txt", "text": "Acme Corp wired funds to the Red Brigade.",
             "humanReviewRequired": False,
             "entities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"}]},
            {"file": "training_doc_A2.txt", "text": "No sanctioned entities appear here at all.",
             "humanReviewRequired": False, "entities": []},
            {"file": "training_doc_A3.txt", "text": "John Doe moved USDC to a wallet in Tehran.",
             "humanReviewRequired": True,
             "entities": [{"startOffset": 34, "endOffset": 40, "label": "POI"}]},
        ]
    }
    # A second reviewer's file re-reviews A1 with a different label set (last wins).
    other = {
        "documents": [
            {"file": "training_doc_A1.txt", "text": "Acme Corp wired funds to the Red Brigade.",
             "humanReviewRequired": False,
             "entities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"},
                          {"startOffset": 29, "endOffset": 40, "label": "FTO"}]},
        ]
    }

    # Only-reviewed filter on the partial file
    rev = X.reviewed_docs(partial)
    ids = {d["file"] for d in rev}
    expect(ids == {"training_doc_A1.txt", "training_doc_A2.txt"},
           f"unreviewed A3 must be skipped, got {ids}")
    a2 = next(d for d in rev if d["file"] == "training_doc_A2.txt")
    expect(a2["entities"] == [], "confirmed clean doc trains as zero-entity example")

    # De-dup across two files: last occurrence (other) wins → A1 has 2 entities
    rev2 = X.reviewed_docs(partial, other)
    a1 = next(d for d in rev2 if d["file"] == "training_doc_A1.txt")
    expect(len({d["file"] for d in rev2}) == 2, "A1 de-duplicated across files")
    expect(len(a1["entities"]) == 2, f"last file wins for A1, got {a1['entities']}")

    # Partition to training rows
    files, csv_rows = X.to_training(rev2, max_bytes=5000)
    expect(len(files) == 2, f"2 reviewed docs -> 2 parts, got {len(files)}")
    # CSV holds A1's two spans; A2 contributes none
    body = "\n".join(csv_rows)
    expect(csv_rows[0] == "File,Line,Begin Offset,End Offset,Type", "CSV header present")
    expect("training_doc_A1.txt,0,0,9,ORG" in body, "A1 ORG span in CSV")
    expect("training_doc_A1.txt,0,29,40,FTO" in body, "A1 FTO span in CSV")

    # End-to-end over the bundled sample file
    out = os.path.join(os.path.dirname(__file__), "sample", "_out")
    reviewed, wfiles = X.run(os.path.join(os.path.dirname(__file__), "sample", "Batch_3.json"), out)
    expect({d["file"] for d in reviewed} == {"training_doc_A1.txt", "training_doc_A2.txt"},
           "sample: only A1+A2 reviewed")

    print("OK — parsing step passed")
    print("  reviewed:", sorted(d["file"] for d in reviewed))
    print("  training parts:", [n for n, _ in wfiles])


if __name__ == "__main__":
    main()
