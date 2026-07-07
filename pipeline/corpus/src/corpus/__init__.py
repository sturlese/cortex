"""corpus — local, reproducible curation of a document corpus.

A PURE box: reads the corpus (read-only), emits JSON artifacts to the workdir. It does not touch
Drive, does not launch clean, does not reimplement the fetcher. The seam with the pipeline is
inventory.json. Everything runs in a container.
"""

__version__ = "0.1.0"
