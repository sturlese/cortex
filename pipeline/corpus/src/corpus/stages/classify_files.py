"""Stage classify-files: files.jsonl (+ taxonomy.json) -> classification.jsonl + matrix.

A generic, ORDERED rules engine driven entirely by taxonomy.json: first matching rule wins; each
rule assigns a doc type and a verdict (IN/MAYBE/OUT). Matching is case- and accent-insensitive.
No company-specific logic lives in code — tune the taxonomy, not this module.
"""
from __future__ import annotations

import collections
import json
import os
import re
import unicodedata

from corpus.artifacts import read_jsonl, write_json, write_jsonl, write_provenance
from corpus.schemas import ClassRecord, FileRecord

VALID_VERDICTS = ("IN", "MAYBE", "OUT")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm(p: str) -> str:
    return strip_accents(p.lower())


def topdir(path: str) -> str:
    parts = path.split("/")
    return parts[1] if len(parts) >= 2 and parts[0] == "." else parts[0]


def ext_of(path: str) -> str:
    b = os.path.basename(path)
    return "." + b.rsplit(".", 1)[1].lower() if "." in b else ""


def default_taxonomy_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "taxonomy.json")


def load_taxonomy(taxonomy_path: str | None = None) -> dict:
    """Loads and validates the taxonomy; pre-normalizes matchers and compiles regexes."""
    with open(taxonomy_path or default_taxonomy_path(), encoding="utf-8") as f:
        tax = json.load(f)
    rules = tax.get("rules") or []
    fallback = tax.get("fallback") or {"type": "other", "verdict": "MAYBE"}
    for rule in rules + [fallback]:
        if not rule.get("type"):
            raise ValueError(f"taxonomy rule without 'type': {rule}")
        if rule.get("verdict") not in VALID_VERDICTS:
            raise ValueError(f"taxonomy rule {rule['type']!r}: verdict must be one of {VALID_VERDICTS}")
    compiled = []
    for rule in rules:
        compiled.append({
            "type": rule["type"],
            "verdict": rule["verdict"],
            "ext": {e.lower() for e in rule.get("ext", [])},
            "basename_any": [norm(s) for s in rule.get("basename_any", [])],
            "path_any": [norm(s) for s in rule.get("path_any", [])],
            "path_regex": [re.compile(rx, re.I) for rx in rule.get("path_regex", [])],
        })
    return {
        "rules": compiled,
        "fallback": fallback,
        "org_units": tax.get("org_units") or {},
        "demoted_types": tax.get("demoted_types") or [],
    }


def classify(path: str, taxonomy: dict) -> tuple[str, str]:
    """path -> (type, verdict). First matching rule wins; ANY matcher within a rule triggers it."""
    p = norm(path)
    b = norm(os.path.basename(path))
    e = ext_of(path)
    for rule in taxonomy["rules"]:
        if (e and e in rule["ext"]) \
                or any(s in b for s in rule["basename_any"]) \
                or any(s in p for s in rule["path_any"]) \
                or any(rx.search(p) for rx in rule["path_regex"]):
            return rule["type"], rule["verdict"]
    fb = taxonomy["fallback"]
    return fb["type"], fb["verdict"]


def unit_of(path: str, taxonomy: dict) -> str:
    """Org-unit column: the top-level folder, optionally mapped to a short label."""
    top = topdir(path)
    return taxonomy["org_units"].get(top, top)


def classify_records(files, taxonomy: dict):
    """FileRecords -> (ClassRecord list, type x unit matrix). Files at the corpus root are skipped."""
    cells = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    examples = collections.defaultdict(list)
    units: set[str] = set()
    rows: list[ClassRecord] = []
    for fr in files:
        rel = re.sub(r"^\./", "", fr.path)
        if "/" not in rel:          # file at the corpus root, no unit folder
            continue
        unit = unit_of(fr.path, taxonomy)
        t, verdict = classify(fr.path, taxonomy)
        rows.append(ClassRecord(path=fr.path, type=t, verdict=verdict, unit=unit, size=fr.size))
        units.add(unit)
        cells[t][unit][0] += 1
        cells[t][unit][1] += fr.size
        if len(examples[t]) < 8:
            examples[t].append(fr.path)
    unit_cols = sorted(units)
    types = sorted(cells)
    matrix = {"cells": {t: {u: cells[t][u] for u in unit_cols} for t in types},
              "units": unit_cols,
              "examples": {t: examples[t] for t in types}}
    return rows, matrix


def run_stage(workdir: str, taxonomy_path: str | None = None) -> int:
    files_path = os.path.join(workdir, "files.jsonl")
    files = read_jsonl(files_path, FileRecord)
    taxonomy = load_taxonomy(taxonomy_path)
    rows, matrix = classify_records(files, taxonomy)
    out = os.path.join(workdir, "classification.jsonl")
    write_jsonl(out, rows)
    write_json(os.path.join(workdir, "classification_matrix.json"), matrix)
    write_provenance(out, "classify-files@2", [files_path, taxonomy_path or default_taxonomy_path()], len(rows))
    return len(rows)
