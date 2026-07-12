"""corpus CLI. Every command says what it does; no `run`/`run2`. Critical paths (`--corpus`,
`--workdir`) are required and validated -> fail fast, no silent defaults.

corpus does NOT touch Drive and does NOT launch clean (that's the pipeline's job). Local curation only.
"""
from __future__ import annotations

import argparse
import sys

from corpus.config import load_config, profile_value
from corpus.paths import PathError, require_corpus, require_workdir
from corpus.stages import (
    build_inventory,
    classify_files,
    curate_manifest,
    enumerate_files,
    trim_manifest,
)


def _resolve(args, key: str, cli_value):
    """CLI wins; else the config's profile/defaults; else None."""
    if cli_value is not None:
        return cli_value
    cfg = load_config(getattr(args, "config", None))
    return profile_value(cfg, getattr(args, "profile", None), key)


def _corpus(args) -> str:
    return require_corpus(_resolve(args, "corpus", args.corpus))


def _workdir(args, create: bool) -> str:
    return require_workdir(_resolve(args, "workdir", args.workdir), create=create)


def cmd_enumerate(args):
    n = enumerate_files.run_stage(_corpus(args), _workdir(args, create=True))
    print(f"enumerate-files: {n} files -> files.jsonl")


def cmd_classify(args):
    n = classify_files.run_stage(_workdir(args, create=False), _resolve(args, "taxonomy", args.taxonomy))
    print(f"classify-files: {n} -> classification.jsonl")


def cmd_curate(args):
    n = curate_manifest.run_stage(_workdir(args, create=False), _resolve(args, "taxonomy", args.taxonomy))
    print(f"curate-manifest: {n} -> manifest_full.jsonl")


def cmd_trim(args):
    n = trim_manifest.run_stage(_workdir(args, create=False), _resolve(args, "taxonomy", args.taxonomy))
    print(f"trim-manifest: {n} -> manifest.jsonl (allowlist)")


def cmd_build_manifest(args):
    corpus, workdir = _corpus(args), _workdir(args, create=True)
    taxonomy = _resolve(args, "taxonomy", args.taxonomy)
    a = enumerate_files.run_stage(corpus, workdir)
    b = classify_files.run_stage(workdir, taxonomy)
    c = curate_manifest.run_stage(workdir, taxonomy)
    d = trim_manifest.run_stage(workdir, taxonomy)
    print(f"build-manifest: enumerate={a} classify={b} curate={c} -> allowlist={d}")


def cmd_build_inventory(args):
    n = build_inventory.run_stage(_workdir(args, create=False), _resolve(args, "drive_ids", args.drive_ids))
    print(f"build-inventory: {n} entries -> inventory.json")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="corpus_config.toml (optional)")
    common.add_argument("--profile", help="config profile (sample/full/...)")

    p = argparse.ArgumentParser(prog="corpus", description="Reproducible corpus curation (local, containerized).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, func, *, corpus=False, workdir=True, extra=None):
        sp = sub.add_parser(name, parents=[common])
        if corpus:
            sp.add_argument("--corpus", help="corpus root (read-only). Required.")
        if workdir:
            sp.add_argument("--workdir", help="artifacts directory. Required.")
        for a, kw in (extra or []):
            sp.add_argument(a, **kw)
        sp.set_defaults(func=func)
        return sp

    add("enumerate-files", cmd_enumerate, corpus=True)
    add("classify-files", cmd_classify, extra=[("--taxonomy", {"help": "taxonomy.json (default: the packaged one)"})])
    add("curate-manifest", cmd_curate, extra=[("--taxonomy", {"help": "taxonomy.json (default: the packaged one)"})])
    add("trim-manifest", cmd_trim, extra=[("--taxonomy", {"help": "taxonomy.json (default: the packaged one)"})])
    add("build-manifest", cmd_build_manifest, corpus=True,
        extra=[("--taxonomy", {"help": "taxonomy.json (default: the packaged one)"})])
    add("build-inventory", cmd_build_inventory,
        extra=[("--drive-ids", {"help": "drive_ids.json {path: fileId} (optional)"})])
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except PathError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
