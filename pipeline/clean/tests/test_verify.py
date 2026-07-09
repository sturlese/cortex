"""Trust layer: generous matching must absorb every reformatting; flags must mean something.
Fixtures deliberately use European-locale text (French/German words, decimal commas, dot
grouping, spaced percents) — verification must be locale-proof, not English-only."""
from clean.schemas import Mention, PageMetadata
from clean.verify import verify_page


def _meta(mentions=()):
    return PageMetadata(title="T", type="report",
                        mentions=[Mention(name=n, type="organization") for n in mentions])


# ── faithful pages never get flagged ─────────────────────────────────────────
def test_verbatim_numbers_verify():
    src = "Revenue was $1.2M in Q1 2026, up 40% QoQ across 12 accounts."
    body = "Revenue reached $1.2M (up 40% QoQ) across 12 accounts in 2026."
    v = verify_page(body, None, src)
    assert v.verdict == "verified"
    assert v.numbers_total == 4          # 1.2M, 40%, 12, 2026
    assert v.numbers_unverified == []


def test_magnitude_suffix_matches_expanded_form():
    assert verify_page("we spent 1.2M", None, "total cost 1,200,000 dollars").verdict == "verified"
    assert verify_page("total 1,200,000", None, "cost was 1.2M").verdict == "verified"
    assert verify_page("headcount 25k", None, "25,000 employees").verdict == "verified"


def test_decimal_comma_and_ambiguous_grouping():
    assert verify_page("growth of 1.5", None, "Wachstum von 1,5 Punkten").verdict == "verified"
    assert verify_page("1200 units", None, "shipped 1.200 units").verdict == "verified"
    assert verify_page("1.2 ratio", None, "ratio de 1,200").verdict == "verified"
    assert verify_page("total 1200000", None, "Betrag 1.200.000 EUR").verdict == "verified"


def test_mixed_separators_both_conventions():
    assert verify_page("total 1,234,567.89", None, "Betrag 1.234.567,89 EUR").verdict == "verified"
    assert verify_page("total 1.234.567,89", None, "amount 1,234,567.89 USD").verdict == "verified"


def test_space_grouped_thousands_in_source():
    assert verify_page("about 1.2M", None, "budget: 1 200 000").verdict == "verified"


def test_nbsp_grouped_thousands_in_source():
    """European PDF extractions group thousands with a non-breaking / narrow space; a faithful
    page rewriting them with commas must still verify (no false 'invented figure' flag)."""
    assert verify_page("Revenue was 1,200,000 EUR.", None,
                       "Chiffre d'affaires: 1 200 000 EUR").verdict == "verified"
    assert verify_page("total 1,234,567", None, "Total 1 234 567").verdict == "verified"


def test_percent_spacing_and_currency():
    assert verify_page("margin 40%", None, "marge de 40 %").verdict == "verified"
    assert verify_page("price €3.4M", None, "Preis: 3.400.000").verdict == "verified"


def test_dates_match_componentwise():
    v = verify_page("Signed on 2026-03-14.", None, "signé le 14/03/2026")
    assert v.verdict == "verified"


def test_number_from_filename_context_verifies():
    v = verify_page("Q1 2026 summary", None, "no dates in the text", context="Report Q1 2026.pdf")
    assert v.verdict == "verified"


def test_no_numbers_is_verified():
    v = verify_page("Prose only, no figures.", None, "source")
    assert v.verdict == "verified"
    assert v.numbers_total == 0


def test_bare_single_digits_skipped():
    v = verify_page("the 3 initiatives\n1. first\n2. second", None, "no digits here at all")
    assert v.numbers_total == 0
    assert v.verdict == "verified"


# ── invention gets flagged ───────────────────────────────────────────────────
def test_hallucinated_figure_flagged():
    v = verify_page("ARR of $9.9M and 77% margin", None, "revenue was $1.2M at 40% margin")
    assert v.verdict == "failed"
    assert set(v.numbers_unverified) == {"9.9M", "77%"}
    assert v.numbers_total == 2


def test_single_miss_is_partial_not_failed():
    src = "Revenue $1.2M, 40% QoQ, 12 accounts, year 2026."
    body = "Revenue $1.2M, 40% QoQ, 12 accounts, 2026 — and a derived 33% share."
    v = verify_page(body, None, src)
    assert v.verdict == "partial"
    assert v.numbers_unverified == ["33%"]


def test_mostly_unverified_is_failed():
    v = verify_page("Numbers: 111, 222, 333, 444", None, "only 111 appears here")
    assert v.verdict == "failed"
    assert len(v.numbers_unverified) == 3


def test_repeated_token_counted_once():
    v = verify_page("55% then 55% again and 55%", None, "no such figure")
    assert v.numbers_total == 1
    assert v.numbers_unverified == ["55%"]


# ── mentions are advisory ────────────────────────────────────────────────────
def test_mentions_found_case_and_accent_insensitive():
    v = verify_page("body", _meta(["Café Rio", "INITECH"]), "about cafe rio and Initech, S.L.")
    assert v.mentions_unverified == []


def test_missing_mention_is_advisory_only():
    v = verify_page("clean body, no numbers", _meta(["Ghost Corp"]), "nothing relevant")
    assert v.mentions_unverified == ["Ghost Corp"]
    assert v.verdict == "verified"          # mentions never affect the verdict


def test_unverified_list_is_capped():
    body = " ".join(f"{i}77%" for i in range(10, 40))     # 30 distinct invented figures
    v = verify_page(body, None, "empty")
    assert v.verdict == "failed"
    assert len(v.numbers_unverified) == 12  # capped for frontmatter/state size
