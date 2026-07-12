"""Entity resolution from folder paths: conventions, catalog pass, status/period parsing."""
import json

from clean.entity import DEFAULT_CONVENTIONS, build_catalog, load_conventions, resolve_entity, slugify


def test_tracked_entity_numbered_under_anchor():
    e = resolve_entity("/Acme Drive/Portfolio/3. Initech/reports/q1.pdf")
    assert e["slug"] == "initech"
    assert e["name"] == "Initech"
    assert e["kind"] == "tracked"
    assert e["seq"] == 3


def test_tracked_entity_with_year_intermediate_and_status():
    e = resolve_entity("/X/Clients/2024/7. Globex - archived/contract.pdf")
    assert e["slug"] == "globex"
    assert e["status"] == "archived"
    assert e["seq"] == 7
    assert e["date"] == "2024"


def test_prospect_stage_and_name():
    e = resolve_entity("/X/Pipeline/Evaluating/Hooli/deck.pdf")
    assert e["slug"] == "hooli"
    assert e["kind"] == "prospect"
    assert e["stage"] == "Evaluating"


def test_prospect_lone_file_is_not_the_entity():
    e = resolve_entity("/X/Pipeline/Evaluating/deck.pdf")
    assert e["slug"] is None


def test_non_entity_numbered_folder_is_skipped():
    e = resolve_entity("/X/Portfolio/1. Reporting/summary.pdf")
    assert e["slug"] is None


def test_numbered_file_is_not_an_entity():
    e = resolve_entity("/X/Portfolio/2. notes.pdf")
    assert e["slug"] is None


def test_period_year_and_quarter():
    e = resolve_entity("/X/Portfolio/3. Initech/2023/Q2 report/kpis.xlsx")
    assert e["date"] == "2023"
    assert e["period"] == "2023-Q2"


def test_unit_hint_passthrough():
    e = resolve_entity("/X/random/doc.pdf", unit_hint="EU")
    assert e["unit"] == "EU"
    assert e["slug"] is None


def test_catalog_second_pass_recovers_nonstandard_anchor():
    paths = [
        ("/X/Portfolio/3. Initech/a.pdf", None),
        ("/X/Audits/Initech/audit-2023.pdf", None),
    ]
    catalog = build_catalog(paths)
    assert catalog == {"initech": "Initech"}
    e = resolve_entity("/X/Audits/Initech/audit-2023.pdf", catalog=catalog)
    assert e["slug"] == "initech"
    assert e["kind"] == "tracked"


def test_catalog_short_slugs_are_ignored():
    catalog = {"abc": "ABC"}   # < 4 chars: too collision-prone in pass 2
    e = resolve_entity("/X/Misc/ABC/doc.pdf", catalog=catalog)
    assert e["slug"] is None


def test_load_conventions_override(tmp_path):
    p = tmp_path / "conv.json"
    p.write_text(json.dumps({"entity_anchors": ["cases"]}))
    conv = load_conventions(str(p))
    assert conv["entity_anchors"] == ["cases"]
    assert conv["status_markers"] == DEFAULT_CONVENTIONS["status_markers"]
    e = resolve_entity("/X/Cases/1. Wayne Corp/doc.pdf", conventions=conv)
    assert e["slug"] == "wayne-corp"


def test_load_conventions_missing_file_falls_back():
    assert load_conventions("/nonexistent.json") == DEFAULT_CONVENTIONS


def test_status_marker_requires_whole_word_not_substring():
    """A status marker must be the whole trailing segment, not a substring of the name."""
    e = resolve_entity("/X/Clients/1. Acme - Wonderland/doc.pdf")
    assert e["slug"] == "acme-wonderland"     # "won" not read out of "Wonderland"
    assert e["status"] is None
    won = resolve_entity("/X/Clients/2. Globex - won/doc.pdf")
    assert won["slug"] == "globex" and won["status"] == "won"


def test_hyphenated_status_marker_on_hold_is_recognized():
    """A status marker that carries its own hyphen ('on-hold', a shipped default) must still be
    stripped. Otherwise the same company fragments into two entities (acme/ vs acme-on-hold/) and
    its lifecycle status is silently dropped."""
    e = resolve_entity("/X/Portfolio/3. Acme - on-hold/board/minutes.pdf")
    assert e["slug"] == "acme"
    assert e["name"] == "Acme"
    assert e["status"] == "on-hold"
    # the same entity filed without the status suffix must resolve to the SAME slug
    plain = resolve_entity("/X/Portfolio/3. Acme/board/minutes.pdf")
    assert plain["slug"] == e["slug"] == "acme"


def test_whitespace_only_segment_does_not_crash():
    """A blank folder name anywhere in a path must not raise — build_catalog runs over the whole
    inventory outside any per-doc guard, so one such path would otherwise abort the entire pass."""
    # blank segment before the anchor: must not crash and still resolve the entity
    e = resolve_entity("/X/ /Clients/3. Acme/doc.pdf")
    assert e["slug"] == "acme"
    # blank segment between anchor and entity: must not crash (resolution may fall through)
    build_catalog([("/X/Clients/ /3. Acme/doc.pdf", None),
                   ("/X/ /Pipeline/Evaluating/Hooli/deck.pdf", None)])


def test_slugify_strips_accents_and_symbols():
    assert slugify("Café & Co. (2024)") == "cafe-co-2024"
