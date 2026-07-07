"""Deterministic faithfulness verification — the trust layer.

The pipeline's core promise is "zero invention": every figure on a page must be quotable from the
source document. This module ENFORCES that promise with pure code: after the LLM writes the body,
each numeric token in it is traced back to the extracted source text. The LLM writes; code verifies.

Design (high-precision flags, best-effort recall):
- Matching is GENEROUS, so reformatting never triggers a flag: each token expands into a set of
  plausible interpretations — decimal comma vs point, ambiguous thousands grouping ("1.200" is
  both 1200 and 1.2), magnitude suffixes ("1.2M" is both 1.2 and 1_200_000), currency symbols and
  percent spacing are ignored. A body token is verified when ANY of its interpretations matches
  ANY interpretation of ANY source token.
- Bare single-digit integers are skipped (too weak a signal: list markers, "the 3 initiatives").
- A flag therefore means "this figure is very likely not in the source" (invented, derived, or the
  extraction mangled it). The ABSENCE of flags is not proof — rephrased or semantic claims are out
  of scope. That trade-off is deliberate: flags you can trust beat noisy ones.

Verdict: verified (0 unverified) · partial (1 unverified, or <=25% of the page's figures)
· failed (>=2 unverified and >25%). Mentions not found in the source are reported as advisory
only — they never affect the verdict.
"""
import re
import unicodedata

from clean.schemas import PageMetadata, Verification

# a number token: grouped ("1,200,000.50") or plain ("42", "3.14"), optional magnitude/percent
_TOKEN = re.compile(
    r"(?<![\w.,])"
    r"(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"\s?(bn|[kKmMbB])?\s?(%)?"
    r"(?!\w)"
)
# space-grouped thousands ("1 200 000") — source side only, adds the combined value to the pool
_SPACE_GROUP = re.compile(r"(?<!\d)(\d{1,3}(?: \d{3})+)(?!\d)")
_MAGNITUDE = {"k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9}
_MAX_LISTED = 12   # cap the lists persisted to frontmatter/state


def _canon(v: float) -> str:
    return f"v:{v:.6g}"


def _interpretations(num: str, suffix: str | None) -> set[str]:
    """All plausible values of one numeric token, canonicalized. Ambiguity adds candidates —
    generosity here is what keeps flags high-precision."""
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
        if len(parts) > 2:                       # "1.200.000" -> grouping
            values.add(float("".join(parts)))
        else:
            head, tail = parts
            if len(tail) == 3:                   # "1.200" -> 1200 OR 1.2 (both plausible)
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


def _candidate_pool(source_text: str, context: str) -> set[str]:
    """Union of interpretations of every number in the source (+ filename/path context)."""
    hay = f"{source_text}\n{context}"
    pool: set[str] = set()
    for m in _TOKEN.finditer(hay):
        pool |= _interpretations(m.group(1), m.group(2))
    for m in _SPACE_GROUP.finditer(hay):
        pool.add(_canon(float(m.group(1).replace(" ", ""))))
    return pool


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s)


def verify_page(body: str, metadata: PageMetadata | None, source_text: str, context: str = "") -> Verification:
    """Cross-checks the generated body (and metadata mentions) against the source text.
    `context` carries filename/path — legitimate places a date or a number can come from."""
    pool = _candidate_pool(source_text, context)

    seen: set[str] = set()
    unverified: list[str] = []
    for m in _TOKEN.finditer(body or ""):
        num, suffix, pct = m.group(1), m.group(2), m.group(3)
        if len(re.sub(r"\D", "", num)) == 1 and not suffix and not pct:
            continue                             # bare single digit: too weak a signal
        display = m.group(0).strip()
        if display in seen:
            continue
        seen.add(display)
        if not (_interpretations(num, suffix) & pool):
            unverified.append(display)

    total = len(seen)
    if not unverified:
        verdict = "verified"
    elif len(unverified) == 1 or len(unverified) / total <= 0.25:
        verdict = "partial"
    else:
        verdict = "failed"

    mentions_missing: list[str] = []
    if metadata and metadata.mentions:
        hay = _norm(f"{source_text} {context}")
        mentions_missing = [mn.name for mn in metadata.mentions if _norm(mn.name) not in hay]

    return Verification(
        verdict=verdict,
        numbers_total=total,
        numbers_unverified=unverified[:_MAX_LISTED],
        mentions_unverified=mentions_missing[:_MAX_LISTED],
    )
