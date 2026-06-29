"""Lambda: split labeled documents into training-size parts (with offset rebasing).

WHY
---
Amazon Comprehend custom entity recognizer training has a **per-document byte limit**
(default 5000 bytes here). A labeled document larger than that must be split into
smaller parts before training. We split into **balanced** parts (a 9,000-byte document
becomes two ~4,500-byte parts, not 5,000 + 4,000), choosing split points that land on
**whitespace** and **never cut through an entity span**. Each part becomes its own
document, so every entity's offsets are **rebased** to be relative to its part.

INPUT
-----
A JSON-Lines "labeled manifest" (e.g. the Ground Truth output), one record per
document. Per record we read:
  * text       — inline ``text`` or ``source``; else fetched from ``source-ref`` (S3).
  * file name  — ``file`` / ``File`` / ``id``; else the basename of ``source-ref``.
  * entities   — ``<LABEL_ATTRIBUTE_NAME>.entities`` if present, else ``entities``.
                 Each entity is ``{startOffset, endOffset, label}`` (character offsets).

OUTPUT
------
For each input document:
  * one ``.txt`` object per part under ``OUTPUT_S3_PREFIX``. A document that already
    fits keeps its name ``<stem>.txt``; a split document becomes
    ``<stem>_part_1.txt``, ``<stem>_part_2.txt``, … (e.g.
    ``training_doc_CECGHHTE.txt`` -> ``training_doc_CECGHHTE_part_1.txt``).
  * one line per part appended to ``OUTPUT_S3_PREFIX<ANNOTATIONS_KEY>`` (JSON-Lines):
    ``{"file": "<part filename>", "entities": [{startOffset, endOffset, label}, ...]}``
    with offsets **rebased** to the part.

CONFIG (environment variables)
------------------------------
  OUTPUT_S3_BUCKET   (required)  Bucket to write parts + annotations into.
  OUTPUT_S3_PREFIX   (optional)  Key prefix for outputs. Default ``training/``.
  ANNOTATIONS_KEY    (optional)  Annotations filename. Default ``annotations.manifest``.
  MAX_DOC_BYTES      (optional)  Per-part byte limit. Default ``5000``.
  LABEL_ATTRIBUTE_NAME (optional) GT label attribute to read entities from. Default ``ner-labels``.

Pure logic (``partition_document`` etc.) is AWS-free and unit-tested; ``_read_s3*`` /
``_write_s3`` are module-level so tests can monkeypatch them.
"""

import json
import math
import os
from bisect import bisect_left
from urllib.parse import urlparse, unquote_plus


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _max_doc_bytes():
    try:
        return int(os.environ.get("MAX_DOC_BYTES", "5000"))
    except ValueError:
        return 5000


# ---------------------------------------------------------------------------
# Pure splitting logic (no AWS) -- unit tested
# ---------------------------------------------------------------------------
def _byte_len(s):
    """UTF-8 byte length of a string (the unit the doc limit is measured in)."""
    return len(s.encode("utf-8"))


def _byte_prefix(text):
    """prefix[i] = UTF-8 byte length of text[:i], for i in 0..len(text)."""
    prefix = [0] * (len(text) + 1)
    for i, ch in enumerate(text):
        prefix[i + 1] = prefix[i] + len(ch.encode("utf-8"))
    return prefix


def _is_safe_cut(entities, p):
    """A cut between text[p-1] and text[p] is safe if it splits no entity span."""
    return not any(e["startOffset"] < p < e["endOffset"] for e in entities)


def _is_word_boundary(text, p):
    """Prefer cutting at whitespace so words aren't split across parts."""
    return text[p - 1].isspace() or text[p].isspace()


def _find_boundary(text, entities, target, lo, hi):
    """Nearest char index to ``target`` in (lo, hi) that is a safe cut.

    First pass requires a whitespace word boundary; second pass relaxes to any safe
    (non-entity-splitting) cut. Returns None if nothing in range qualifies.
    """
    span = max(target - lo, hi - target) + 1
    for word_boundary in (True, False):
        for radius in range(span):
            for p in (target - radius, target + radius):
                if lo < p < hi and _is_safe_cut(entities, p) \
                        and (not word_boundary or _is_word_boundary(text, p)):
                    return p
    return None


def _split_positions(text, entities, n):
    """Return char boundaries [0, p1, …, len] for ``n`` balanced-by-bytes parts."""
    prefix = _byte_prefix(text)
    total = prefix[-1]
    length = len(text)
    bounds = [0]
    for k in range(1, n):
        target_byte = round(total * k / n)
        target_char = bisect_left(prefix, target_byte)          # char index ~ that byte
        p = _find_boundary(text, entities, target_char, bounds[-1], length)
        if p is None or p <= bounds[-1]:
            p = max(bounds[-1] + 1, min(target_char, length - 1))  # last-resort cut
        bounds.append(p)
    bounds.append(length)
    return bounds


def partition_document(text, entities, max_bytes):
    """Split one document into <= max_bytes byte parts with rebased entities.

    Returns a list of ``{"text": <part text>, "entities": [...]}`` (offsets relative to
    the part). A document already within the limit is returned unchanged as one part.
    Balanced: the part count is ``ceil(total_bytes / max_bytes)``, raised only if a
    balanced split can't fit (e.g. boundary adjustment around entities). A single entity
    longer than ``max_bytes`` cannot be split and yields an over-limit part (logged).
    """
    entities = sorted((dict(e) for e in entities), key=lambda e: e["startOffset"])
    if _byte_len(text) <= max_bytes or len(text) < 2:
        return [{"text": text, "entities": entities}]

    total = _byte_len(text)
    prefix = _byte_prefix(text)
    base_n = math.ceil(total / max_bytes)
    bounds = _split_positions(text, entities, base_n)
    # If a balanced split overflows (boundary nudging), add parts until it fits or we
    # hit a hard wall (an unsplittable entity bigger than the limit).
    n = base_n
    while n < len(text) and any(
        prefix[b] - prefix[a] > max_bytes for a, b in zip(bounds, bounds[1:])
    ):
        n += 1
        new_bounds = _split_positions(text, entities, n)
        if new_bounds == bounds:        # no further progress possible
            break
        bounds = new_bounds

    parts = []
    for a, b in zip(bounds, bounds[1:]):
        seg_ents = [
            {**e, "startOffset": e["startOffset"] - a, "endOffset": e["endOffset"] - a}
            for e in entities
            if a <= e["startOffset"] and e["endOffset"] <= b
        ]
        parts.append({"text": text[a:b], "entities": seg_ents})
    return parts


def part_filenames(file_name, n):
    """``base.txt`` -> ``[base.txt]`` when n==1, else ``base_part_1.txt`` … ``base_part_n.txt``."""
    stem = file_name[:-4] if file_name.lower().endswith(".txt") else file_name
    if n == 1:
        return [f"{stem}.txt"]
    return [f"{stem}_part_{i}.txt" for i in range(1, n + 1)]


# ---------------------------------------------------------------------------
# Record parsing
# ---------------------------------------------------------------------------
def _entities_of(record, label_attribute_name):
    attr = record.get(label_attribute_name)
    if isinstance(attr, dict) and isinstance(attr.get("entities"), list):
        return attr["entities"]
    if isinstance(record.get("entities"), list):
        return record["entities"]
    return []


def _file_base(record):
    for key in ("file", "File", "id"):
        if record.get(key):
            return str(record[key]).rsplit("/", 1)[-1]
    ref = record.get("source-ref")
    if ref:
        return ref.rsplit("/", 1)[-1]
    return None


# ---------------------------------------------------------------------------
# S3 I/O -- module-level so unit tests can monkeypatch (no boto3 needed in tests)
# ---------------------------------------------------------------------------
def _s3():
    import boto3  # provided by the Lambda runtime

    return boto3.client("s3")


def _read_s3_text(bucket, key):
    return _s3().get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")


def _write_s3(bucket, key, body, content_type="text/plain"):
    if isinstance(body, str):
        body = body.encode("utf-8")
    _s3().put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)


def _resolve_input_location(event):
    """(bucket, key) of the labeled manifest from manual / S3 / EventBridge events."""
    if event.get("output_s3_uri"):
        parsed = urlparse(event["output_s3_uri"])
        return parsed.netloc, parsed.path.lstrip("/")
    if event.get("bucket") and event.get("key"):
        return event["bucket"], event["key"]
    records = event.get("Records")
    if records:
        s3 = records[0].get("s3", {})
        bucket = s3.get("bucket", {}).get("name")
        key = unquote_plus(s3.get("object", {}).get("key", ""))
        if bucket and key:
            return bucket, key
    detail = event.get("detail")
    if detail:
        bucket = detail.get("bucket", {}).get("name")
        key = detail.get("object", {}).get("key")
        if bucket and key:
            return bucket, key
    raise ValueError("Provide {'bucket','key'} or {'output_s3_uri'}, or invoke via S3/EventBridge.")


def _text_of(record):
    """Inline text (``text``/``source``) or fetched from ``source-ref``."""
    for key in ("text", "source"):
        if record.get(key) is not None:
            return record[key]
    ref = record.get("source-ref")
    if ref:
        parsed = urlparse(ref)
        return _read_s3_text(parsed.netloc, parsed.path.lstrip("/"))
    raise KeyError("record must contain 'text', 'source', or 'source-ref'")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def lambda_handler(event, context):
    out_bucket = os.environ["OUTPUT_S3_BUCKET"]
    out_prefix = os.environ.get("OUTPUT_S3_PREFIX", "training/")
    annotations_key = os.environ.get("ANNOTATIONS_KEY", "annotations.manifest")
    label_attribute_name = os.environ.get("LABEL_ATTRIBUTE_NAME", "ner-labels")
    max_bytes = _max_doc_bytes()

    src_bucket, src_key = _resolve_input_location(event)
    manifest = _read_s3_text(src_bucket, src_key)

    annotations_lines = []
    in_docs = out_parts = split_docs = 0

    for line in manifest.splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        in_docs += 1

        text = _text_of(record)
        entities = _entities_of(record, label_attribute_name)
        base = _file_base(record) or f"training_doc_{in_docs:06d}.txt"

        parts = partition_document(text, entities, max_bytes)
        names = part_filenames(base, len(parts))
        if len(parts) > 1:
            split_docs += 1

        for name, part in zip(names, parts):
            _write_s3(out_bucket, out_prefix + name, part["text"])
            annotations_lines.append(json.dumps({"file": name, "entities": part["entities"]}))
            out_parts += 1

    _write_s3(out_bucket, out_prefix + annotations_key,
              "\n".join(annotations_lines) + ("\n" if annotations_lines else ""),
              content_type="application/json")

    return {
        "input_documents": in_docs,
        "output_parts": out_parts,
        "documents_split": split_docs,
        "annotations_s3_uri": f"s3://{out_bucket}/{out_prefix}{annotations_key}",
        "max_doc_bytes": max_bytes,
    }
