# corpus — offline curation

`pipeline/corpus/src/corpus/`. A reproducible curation CLI for bootstrapping the brain from a
*local copy* of a corpus (e.g. a bulk Drive export) instead of a live mirror. Pure box: reads the
corpus read-only, emits JSON artifacts to a workdir. Each JSONL/inventory artifact gets a
`.meta.json` provenance sidecar (sha256 of inputs → idempotency).

## Stages

```
enumerate-files   corpus/ -> files.jsonl            (path, size, mtime, md5; deterministic walk)
classify-files    files.jsonl -> classification.jsonl + matrix   (taxonomy rules engine)
curate-manifest   classification -> manifest_full.jsonl          (IN+MAYBE, md5 dedup, canonical pick; taxonomy for trim-survival)
trim-manifest     manifest_full -> manifest.jsonl                (drop non-documents + demoted types)
build-inventory   manifest -> inventory.json                     (what clean consumes as _state.json)
```

`build-manifest` chains the first four.

## The taxonomy (the interesting bit)

Classification is 100% config: `taxonomy.json` is an **ordered** list of rules — first match wins —
each assigning a `type` and a verdict (`IN` index / `MAYBE` / `OUT` discard). Matchers: `ext`,
`basename_any`, `path_any`, `path_regex` (any hit triggers the rule). Matching is case- and
accent-insensitive. The packaged file is a generic example for a company drive — **tune it to your
corpus**; the engine (`stages/classify_files.py`) never changes.

`org_units` maps top-level folders to short labels for the classification matrix; `demoted_types`
lists types that `trim-manifest` drops. `curate-manifest` also reads `demoted_types`: when it dedups
byte-identical copies it picks a canonical copy that will **survive** `trim` (document extension, not
a demoted type), so a duplicate can never delete a document that had a keep-worthy sibling.

## Run

```bash
cd pipeline/corpus
docker build -t cortex-corpus:local .
docker run --rm -v /path/to/corpus:/data/corpus:ro -v /path/to/work:/data/work \
  cortex-corpus:local build-manifest --corpus /data/corpus --workdir /data/work

docker run --rm -v /path/to/work:/data/work \
  cortex-corpus:local build-inventory --workdir /data/work
# copy work/inventory.json into the raw dir as _state.json and clean will process it
```

`--config corpus_config.toml --profile <name>` can hold path defaults (CLI flags always win).
