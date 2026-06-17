# USDC_CCDemo

Test repository created via Claude Code on 2026-06-17.

# Entity-Sentence Semantic Matcher

> Match a list of entities to the most semantically similar sentences in a
> corpus using SentenceTransformers embeddings and a FAISS index.

A minimal, two-step demo project: one script builds a vector index from a
sentence corpus, and a second script searches that index to find the
sentences each entity is most semantically related to. Matching is based on
meaning, not keywords, so "machine learning infrastructure" can match a
sentence about a GPU training cluster even with no words in common.

---

## Table of contents

- [Features](#features)
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Data format](#data-format)
- [Example output](#example-output)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Roadmap](#roadmap)
- [License](#license)
- [References](#references)

---

## Features

- Semantic matching by meaning, not exact keywords.
- Two clearly separated steps: build the index once, then search many times.
- Cosine similarity via a normalized FAISS inner-product index.
- A shared embedding module so both scripts stay consistent.
- Small sample data included, so it runs end to end out of the box.

## How it works

1. **Embed.** Each sentence in the corpus is converted into a fixed-length
   vector by a SentenceTransformer model. Similar meanings produce nearby
   vectors.
2. **Index.** Vectors are normalized and stored in a FAISS `IndexFlatIP`.
   With normalized vectors, inner-product search equals cosine similarity.
3. **Search.** Each entity is embedded with the same model, then FAISS
   returns the top-k closest sentence vectors and their similarity scores.

## Project structure

```
entity-matcher/
├── README.md              This file
├── requirements.txt       Python dependencies
├── config.py              Model name, top-k, and file paths
├── data/
│   ├── sentences.txt      The corpus (one sentence per line)
│   └── entities.txt       The queries (one entity per line)
├── src/
│   ├── __init__.py
│   ├── embeddings.py      Shared model loading and encoding
│   ├── build_index.py     Step 1: build and save the FAISS index
│   └── search.py          Step 2: match entities to sentences
└── artifacts/             Generated index and metadata (created on first run)
```

## Requirements

- Python 3.9 or newer
- Roughly 500 MB of disk for the default model on first download
- No GPU required; runs on CPU

## Installation

```bash
git clone <your-repo-url>
cd entity-matcher

python -m venv .venv
source .venv/bin/activate        # On Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

The model downloads automatically the first time you run a script.

## Usage

Run the two scripts in order from the project root.

```bash
# Step 1: build the index from data/sentences.txt
python -m src.build_index

# Step 2: match the entities in data/entities.txt against the index
python -m src.search
```

Rebuild the index whenever the corpus changes. The search step can be run
as many times as you like without rebuilding.

## Configuration

All tunables live in `config.py`.

| Setting          | Default              | Description                                  |
|------------------|----------------------|----------------------------------------------|
| `MODEL_NAME`     | `all-MiniLM-L6-v2`   | SentenceTransformer model used for embedding |
| `TOP_K`          | `3`                  | Number of matches returned per entity        |
| `SENTENCES_PATH` | `data/sentences.txt` | Corpus file, one sentence per line           |
| `ENTITIES_PATH`  | `data/entities.txt`  | Query file, one entity per line              |
| `INDEX_PATH`     | `artifacts/faiss.index` | Where the FAISS index is saved            |

## Data format

Both input files are plain text, one item per line. Blank lines are ignored.

`data/sentences.txt`
```
The quarterly revenue report showed strong growth in the cloud division.
Our new GPU cluster cut model training time by nearly half.
```

`data/entities.txt`
```
machine learning infrastructure
interest rate policy
```

## Example output

```
Entity: machine learning infrastructure
  1. (0.612) Our new GPU cluster cut model training time by nearly half.
  2. (0.341) The data pipeline now ingests events in near real time.
  3. (0.297) The recommendation engine boosted average order value by twelve percent.

Entity: interest rate policy
  1. (0.658) The central bank signaled it may raise interest rates next month.
  ...
```

Scores are cosine similarities in the range -1 to 1; higher is more similar.

## Troubleshooting

- **`faiss.index not found`**: run `python -m src.build_index` before searching.
- **Model download is slow or fails**: the first run pulls the model from the
  Hugging Face hub; ensure network access, then rerun.
- **`ModuleNotFoundError: src`**: run the commands from the project root using
  the `-m` flag, for example `python -m src.search`, not `python src/search.py`.

## Limitations

- Uses a flat (exhaustive) index, which is exact but does not scale to very
  large corpora. Swap in an approximate index such as `IndexIVFFlat` or HNSW
  for large datasets.
- Match quality depends on the embedding model; the small default favors
  speed over accuracy.

## Roadmap

- Add an approximate-search index option for larger corpora.
- Accept a similarity threshold to filter weak matches.
- Add a simple test suite and a CLI for arbitrary input files.

## License

MIT. See `LICENSE` for details.

## References

- SentenceTransformers: https://www.sbert.net
- FAISS: https://github.com/facebookresearch/faiss
