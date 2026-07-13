"""Deterministic faithfulness verification — the trust layer.

The pipeline's core promise is "zero invention": every figure on a page must be quotable from the
source document. This module ENFORCES that promise with pure code: after the LLM writes the body,
each numeric token in it is traced back to the extracted source text. The LLM writes; code verifies.

Two independent checks, both deterministic:

1. PRESENCE (generous matching, high-precision flags): each token expands into a set of plausible
   interpretations — decimal comma vs point, ambiguous thousands grouping ("1.200" is both 1200
   and 1.2), magnitude suffixes ("1.2M" is both 1.2 and 1_200_000), currency symbols and percent
   spacing are ignored. A body token is verified when ANY of its interpretations matches ANY
   interpretation of ANY source token. Bare single-digit integers are skipped (too weak a signal:
   list markers, "the 3 initiatives"). A presence flag means "this figure is very likely not in
   the source" (invented, derived, or the extraction mangled it).

2. PERIOD ANCHORING (high-precision attribution check): a figure that IS in the source can still
   be misattributed — "ARR was 512000 in January" when the source ties 512000 to March. For each
   body figure whose own line asserts a period (year, quarter, month), every source occurrence of
   that value is inspected: if at least one occurrence carries no period signal on its own line,
   or a compatible one, the figure is anchored. Only when EVERY occurrence's line carries a
   contradicting period does the figure land in `numbers_unanchored`. Absence of signal never
   flags; date tokens themselves are exempt (they assert their own period). Flags you can trust.

The ABSENCE of flags is not proof — rephrased or semantic claims are out of scope for
deterministic checking (that is the sampled claim-level judge's job, see ops). Trade-off is
deliberate: every flag this module raises is actionable.

Verdict over problems = unverified ∪ unanchored: verified (0) · partial (1, or <=25% of the
page's figures) · failed (>=2 and >25%). Mentions not found in the source are reported as
advisory only — they never affect the verdict.
"""
import bisect
import re
import unicodedata
from dataclasses import dataclass

from clean.schemas import PageMetadata, Verification

# a number token: grouped ("1,200,000.50") or plain ("42", "3.14"), optional magnitude/percent
_TOKEN = re.compile(
    r"(?<![\w.,])"
    r"(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"\s?(bn|[kKmMbB])?\s?(%)?"
    r"(?!\w)"
)
# space-grouped thousands ("1 200 000") — source side only, adds the combined value to the pool.
# Accept non-breaking / narrow / thin spaces too (explicit escapes, not literals — invisible
# characters do not survive editors): European PDF extractions group with U+00A0/U+202F/U+2009,
# and an ASCII-only pattern flagged faithful figures as invented.
_GROUP_SPACE = "[ \u00a0\u202f\u2009]"
_SPACE_GROUP = re.compile(rf"(?<!\d)(\d{{1,3}}(?:{_GROUP_SPACE}\d{{3}})+)(?!\d)")
_MAGNITUDE = {"k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9}
_MAX_LISTED = 12   # cap the lists persisted to frontmatter/state
_MAX_SPANS = 24    # cap the per-figure span map persisted to state


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


def _candidate_pool(source_text: str, context: str) -> tuple[str, dict[str, list[tuple[int, int]]]]:
    """(hay, pool): hay = source + filename/path context; pool maps each interpretation of every
    number in hay to the spans where it occurs (spans feed the period-anchoring check)."""
    hay = f"{source_text}\n{context}"
    pool: dict[str, list[tuple[int, int]]] = {}
    for m in _TOKEN.finditer(hay):
        for c in _interpretations(m.group(1), m.group(2)):
            pool.setdefault(c, []).append(m.span())
    for m in _SPACE_GROUP.finditer(hay):
        digits = "".join(ch for ch in m.group(1) if ch.isdigit())
        pool.setdefault(_canon(float(digits)), []).append(m.span())
    return hay, pool


# ── period signals (the anchoring check) ─────────────────────────────────────
# Deterministic period *facts* extracted from text: YYYY-MM-DD, YYYY-MM, quarters, capitalized
# month names, bare years. Used only to detect explicit contradictions — never to guess.
@dataclass(frozen=True)
class _Period:
    year: int | None = None
    quarter: int | None = None
    month: int | None = None


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november", "december"])}
_MONTHS |= {m[:3]: v for m, v in _MONTHS.items()} | {"sept": 9}

# Ordered by specificity; earlier patterns suppress overlapping later matches.
_P_YMD = re.compile(r"\b(20\d\d)[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b")
_P_YM = re.compile(r"\b(20\d\d)[-/](0?[1-9]|1[0-2])\b(?![-/]?\d)")
_P_QY = re.compile(r"\bQ([1-4])(?:\s*(?:of\s+)?(20\d\d))?\b")
_P_YQ = re.compile(r"\b(20\d\d)[- ]?Q([1-4])\b")
# capitalized month names only: lowercase "may"/"march" are common English words, and a phantom
# month signal could manufacture a contradiction. Capitalization is the deterministic tiebreak.
_P_MONTH = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?"
    r"|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?(?:\s*,?\s*(20\d\d))?\b")
_P_YEAR = re.compile(r"\b(20\d\d)\b")


def _period_signals(text: str) -> list[tuple[int, int, _Period]]:
    """Every period assertion in `text` as (start, end, period), most specific pattern first;
    a span claimed by a more specific pattern is not re-reported by a weaker one."""
    out: list[tuple[int, int, _Period]] = []
    taken: list[tuple[int, int]] = []

    def _free(s: int, e: int) -> bool:
        return all(e <= ts or s >= te for ts, te in taken)

    def _add(s: int, e: int, p: _Period):
        if _free(s, e):
            out.append((s, e, p))
            taken.append((s, e))

    for m in _P_YMD.finditer(text):
        _add(*m.span(), _Period(year=int(m.group(1)), month=int(m.group(2))))
    for m in _P_YQ.finditer(text):
        _add(*m.span(), _Period(year=int(m.group(1)), quarter=int(m.group(2))))
    for m in _P_QY.finditer(text):
        _add(*m.span(), _Period(year=int(m.group(2)) if m.group(2) else None, quarter=int(m.group(1))))
    for m in _P_YM.finditer(text):
        _add(*m.span(), _Period(year=int(m.group(1)), month=int(m.group(2))))
    for m in _P_MONTH.finditer(text):
        key = m.group(1).lower()
        month = _MONTHS.get(key) or _MONTHS[key[:3]]
        _add(*m.span(), _Period(year=int(m.group(2)) if m.group(2) else None, month=month))
    for m in _P_YEAR.finditer(text):
        _add(*m.span(), _Period(year=int(m.group(1))))
    out.sort(key=lambda t: t[0])
    return out


def _compatible(a: _Period, b: _Period) -> bool:
    """True unless the two assertions explicitly contradict on a shared granularity. A coarser
    signal never contradicts a finer one within it ("2026" vs "2026-03"; "Q1 2026" vs "2026-02")."""
    if a.year and b.year and a.year != b.year:
        return False
    if a.month and b.month and a.month != b.month:
        return False
    aq = a.quarter or (((a.month - 1) // 3) + 1 if a.month else None)
    bq = b.quarter or (((b.month - 1) // 3) + 1 if b.month else None)
    return not (aq and bq and aq != bq)


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _line_window(pos: int, starts: list[int], text_len: int, radius: int = 0) -> tuple[int, int]:
    """Char range of the line containing `pos`, extended `radius` lines each way."""
    i = bisect.bisect_right(starts, pos) - 1
    lo = starts[max(0, i - radius)]
    hi_line = i + radius + 1
    hi = starts[hi_line] - 1 if hi_line < len(starts) else text_len
    return lo, hi


def _signals_in(window: tuple[int, int], signals: list[tuple[int, int, _Period]]) -> list[_Period]:
    lo, hi = window
    return [p for s, e, p in signals if s < hi and e > lo]


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int, _Period]]) -> bool:
    s, e = span
    return any(s < pe and e > ps for ps, pe, _ in spans)


def provable_as_of(date_str: str | None, hay: str) -> str | None:
    """The as-of the evidence supports, at the finest PROVABLE granularity. The LLM proposes a
    content date; this returns it only as far as the source (extraction + filename + path)
    backs it: full date when the date literally appears, year-month when a compatible signal
    exists, bare year when only the year does, None when nothing does. as_of is a trust field —
    it must never say more than the document can prove."""
    if not date_str:
        return None
    m = re.fullmatch(r"(20\d\d)-(\d\d)(?:-(\d\d))?", date_str.strip())
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if m.group(3) and re.search(rf"\b{re.escape(date_str.strip())}\b", hay):
        return date_str.strip()
    signals = [p for _s, _e, p in _period_signals(hay)]
    if any(p.year == year and p.month == month for p in signals):
        return f"{year}-{month:02d}"
    if any(p.year == year and p.month is None and p.quarter == ((month - 1) // 3) + 1 for p in signals):
        return f"{year}-Q{((month - 1) // 3) + 1}"
    if any(p.year == year for p in signals):
        return str(year)
    return None


def parse_period(text: str) -> str | None:
    """Normalize a period expression to 'YYYY', 'YYYY-MM' or 'YYYY-QN' — the shared period
    vocabulary of the trust layer (page anchoring) and the facts layer. None when `text` carries
    no period signal or an ambiguous one (several distinct signals)."""
    signals = _period_signals(text)
    periods = {(p.year, p.quarter, p.month) for _s, _e, p in signals}
    if len(periods) != 1:
        return None
    p = signals[0][2]
    if p.year and p.month:
        return f"{p.year}-{p.month:02d}"
    if p.year and p.quarter:
        return f"{p.year}-Q{p.quarter}"
    if p.year:
        return str(p.year)
    return None                     # month/quarter without a year: not addressable as a period


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s)


def verify_page(body: str, metadata: PageMetadata | None, source_text: str, context: str = "") -> Verification:
    """Cross-checks the generated body (and metadata mentions) against the source text.
    `context` carries filename/path — legitimate places a date or a number can come from."""
    body = body or ""
    hay, pool = _candidate_pool(source_text, context)
    hay_starts = _line_starts(hay)
    hay_signals = _period_signals(hay)
    body_starts = _line_starts(body)
    body_signals = _period_signals(body)

    seen: set[str] = set()
    present: dict[str, bool] = {}
    matched_by_display: dict[str, set[str]] = {}
    unverified: list[str] = []
    unanchored: list[str] = []
    spans: dict[str, list[int]] = {}

    for m in _TOKEN.finditer(body):
        num, suffix, pct = m.group(1), m.group(2), m.group(3)
        if len(re.sub(r"\D", "", num)) == 1 and not suffix and not pct:
            continue                             # bare single digit: too weak a signal
        display = m.group(0).strip()
        if display not in seen:
            seen.add(display)
            matched = _interpretations(num, suffix) & pool.keys()
            matched_by_display[display] = matched
            present[display] = bool(matched)
            if not matched:
                unverified.append(display)
            elif len(spans) < _MAX_SPANS:
                first = min(sp for c in matched for sp in pool[c])
                spans[display] = [first[0], first[1]]
        if not present[display] or display in unanchored:
            continue
        # ── period anchoring: does the page tie this figure to a period the source contradicts?
        if _overlaps(m.span(), body_signals):
            continue                             # the figure IS a date/period token — self-asserting
        asserted = _signals_in(_line_window(m.start(), body_starts, len(body)), body_signals)
        if not asserted:
            continue                             # the page asserts no period for this figure
        anchored = False
        for c in matched_by_display[display]:
            for s, _e in pool[c]:
                # window = the occurrence's OWN line: in tables, adjacent rows carry adjacent
                # periods, so any wider window would anchor a wrong-row figure to its neighbor.
                # Layouts that keep the period elsewhere (headers, filename) fall under the
                # "no signal -> no contradiction" rule and never flag.
                near = _signals_in(_line_window(s, hay_starts, len(hay)), hay_signals)
                if not near or any(_compatible(a, b) for a in asserted for b in near):
                    anchored = True
                    break
            if anchored:
                break
        if not anchored:
            unanchored.append(display)

    total = len(seen)
    problems = len(unverified) + len(unanchored)
    if problems == 0:
        verdict = "verified"
    elif problems == 1 or problems / total <= 0.25:
        verdict = "partial"
    else:
        verdict = "failed"

    mentions_missing: list[str] = []
    if metadata and metadata.mentions:
        mention_hay = _norm(f"{source_text} {context}")
        mentions_missing = [mn.name for mn in metadata.mentions if _norm(mn.name) not in mention_hay]

    return Verification(
        verdict=verdict,
        numbers_total=total,
        numbers_unverified=unverified[:_MAX_LISTED],
        numbers_unanchored=unanchored[:_MAX_LISTED],
        numbers_spans=spans,
        mentions_unverified=mentions_missing[:_MAX_LISTED],
    )
