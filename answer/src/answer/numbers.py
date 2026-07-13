"""Numeric token matching for the answer verifier.

Deliberately mirrors the interpretation logic of the pipeline's trust layer
(pipeline/clean/src/clean/verify.py) — the packages share no code by design (ADR 001: they talk
through files, never imports), so the ~50 lines are duplicated rather than coupled. Any change
here must be mirrored there and vice versa.
"""
import re

_TOKEN = re.compile(
    r"(?<![\w.,])"
    r"(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"\s?(bn|[kKmMbB])?\s?(%)?"
    r"(?!\w)"
)
_MAGNITUDE = {"k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9}


def _canon(v: float) -> str:
    return f"v:{v:.6g}"


def interpretations(num: str, suffix: str | None) -> set[str]:
    """All plausible values of one numeric token, canonicalized (decimal comma vs point,
    ambiguous grouping, magnitude suffixes)."""
    values: set[float] = set()
    if "." in num and "," in num:
        dec = "." if num.rfind(".") > num.rfind(",") else ","
        thou = "," if dec == "." else "."
        try:
            values.add(float(num.replace(thou, "").replace(dec, ".")))
        except ValueError:
            pass
    elif "." in num or "," in num:
        sep = "." if "." in num else ","
        parts = num.split(sep)
        if len(parts) > 2:
            values.add(float("".join(parts)))
        else:
            head, tail = parts
            if len(tail) == 3:
                values.add(float(head + tail))
            values.add(float(head + "." + tail))
    else:
        values.add(float(num))
    out: set[str] = set()
    for v in values:
        out.add(_canon(v))
        if suffix:
            out.add(_canon(v * _MAGNITUDE[suffix.lower()]))
    return out


def number_pool(text: str) -> set[str]:
    """Every interpretation of every number in `text`."""
    pool: set[str] = set()
    for m in _TOKEN.finditer(text or ""):
        pool |= interpretations(m.group(1), m.group(2))
    return pool


def unverified_figures(answer_text: str, evidence_text: str) -> list[str]:
    """Figures in the answer that no evidence interpretation backs. Bare single digits are
    skipped (list markers); repeated tokens count once. Same generosity as the page verifier:
    a flag means 'this figure did not come from the evidence'."""
    pool = number_pool(evidence_text)
    seen: set[str] = set()
    missing: list[str] = []
    for m in _TOKEN.finditer(answer_text or ""):
        num, suffix, pct = m.group(1), m.group(2), m.group(3)
        if len(re.sub(r"\D", "", num)) == 1 and not suffix and not pct:
            continue
        display = m.group(0).strip()
        if display in seen:
            continue
        seen.add(display)
        if not (interpretations(num, suffix) & pool):
            missing.append(display)
    return missing
