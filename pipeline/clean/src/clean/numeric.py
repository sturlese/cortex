"""Numeric interpretation — the package's single notion of "what number does this string carry".

A dependency-free leaf: the facts validator (facts.py) compares a claimed value with its cell
through it, the store (factstore.py) derives the queryable value_num column from it, and the
offline fake backend (fake_llm.py) uses it to decide which cells look numeric. Keeping it here
means the storage layer never imports the agent layer just to parse a number.
"""
import re


def parse_num(s: str) -> float | None:
    """Best-effort numeric value of a cell/value string (for equality + the value_num column)."""
    t = re.sub(r"[\u00a0\u202f\u2009]", " ", str(s)).strip()
    t = re.sub(r"[€$£%]|(usd|eur|gbp)\b", "", t, flags=re.I).strip()
    m = re.fullmatch(r"[-+]?[\d.,\s]+([kKmMbB]|bn)?", t)
    if not m or not re.search(r"\d", t):
        return None
    suffix = m.group(1)
    digits = t[: len(t) - len(suffix)] if suffix else t
    digits = digits.replace(" ", "")
    try:
        if "," in digits and "." in digits:
            dec = "." if digits.rfind(".") > digits.rfind(",") else ","
            thou = "," if dec == "." else "."
            v = float(digits.replace(thou, "").replace(dec, "."))
        elif digits.count(".") > 1:                                   # dot-grouped: 1.200.000
            v = float(digits.replace(".", ""))
        elif digits.count(",") == 1 and len(digits.split(",")[1]) != 3:
            v = float(digits.replace(",", "."))                       # decimal comma: 1,5
        else:
            v = float(digits.replace(",", ""))
    except ValueError:
        return None
    return v * {"k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9}.get((suffix or "").lower(), 1)
