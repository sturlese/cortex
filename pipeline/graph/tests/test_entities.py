from graph.entities import build_entities


def test_dedup_variants_into_one_entity():
    ms = [("Wayfarer", "company", 3), ("WAYFARER, S.L.", "company", 2), ("Wayfarer SL", "company", 1)]
    ents = build_entities(ms, min_mentions=1)
    assert len(ents) == 1
    e = next(iter(ents.values()))
    assert e["title"] == "Wayfarer"          # best_title: non-caps, shortest
    assert e["mentions"] == 6
    assert set(e["aliases"]) == {"Wayfarer", "WAYFARER, S.L.", "Wayfarer SL"}
    assert e["slug"] == "entities/company/wayfarer"


def test_min_mentions_drops_singletons():
    ms = [("Acme", "company", 1), ("Globex", "company", 3)]
    ents = build_entities(ms, min_mentions=2)
    assert {e["title"] for e in ents.values()} == {"Globex"}


def test_noise_dropped():
    ms = [("A.B.T.", "person", 5), ("Real Person", "person", 5)]
    ents = build_entities(ms, min_mentions=1)
    assert {e["title"] for e in ents.values()} == {"Real Person"}


def test_dominant_type_wins():
    ms = [("Initech", "company", 5), ("Initech", "organization", 1)]
    ents = build_entities(ms, min_mentions=1)
    assert next(iter(ents.values()))["type"] == "company"
    # ...and frequency beats the alphabetical tie-break, which only applies to an actual tie. This
    # case is the one that discriminates: here the dominant type sorts LATER than the rare one, so a
    # purely alphabetical rule would pick "company" and pass the assertion above by coincidence.
    ms = [("Initech", "organization", 5), ("Initech", "company", 1)]
    ents = build_entities(ms, min_mentions=1)
    assert next(iter(ents.values()))["type"] == "organization"


def test_output_is_independent_of_mention_order():
    """docs/pipeline/graph.md promises "Output is deterministic for a given input", and the input is
    the pages' CONTENT — not the order os.walk happened to yield them in. Three tie-breaks read a
    Counter, which resolves ties by first-encounter, so build_graph fed them raw directory-entry
    order: adding an unrelated file to brain-md could flip an entity's type, and with it its node
    FILE PATH and every wikilink pointing at it."""
    cases = {
        "type tie":  [("Initech", "company", 1), ("Initech", "organization", 1)],
        "title tie": [("Acme Corp", "company", 1), ("Acme GmbH", "company", 1)],
        "aliases":   [("Acme Corp", "company", 5), ("ACME CORP", "company", 1),
                      ("Acme  Corp", "company", 1), ("acme corp", "company", 1)],
    }
    for label, ms in cases.items():
        assert build_entities(ms, min_mentions=1) == build_entities(ms[::-1], min_mentions=1), label


def test_title_prefers_non_all_caps_over_shortest():
    """The documented title rule is an ORDERED list — non-ALL-CAPS first, then shortest — and this
    is the only case that pins that order: `IBM` is shorter AND more frequent, yet loses to
    `Ibm Global` because it shouts. Without this, reordering the first two keys passes the whole
    suite, and docs/pipeline/graph.md would be promising a rule nothing checks.

    (`test_dedup_variants_into_one_entity` cannot pin it: there the non-caps name is also the
    shortest, so both orderings agree.)"""
    from collections import Counter

    from graph.entities import _best_title
    assert _best_title(Counter({"IBM": 5, "Ibm Global": 1})) == "Ibm Global"
    assert _best_title(Counter({"ACME": 3, "Acme Systems Group": 1})) == "Acme Systems Group"
    # ...and among equally-shouty names the later keys decide, shortest first
    assert _best_title(Counter({"IBM CORP": 1, "IBM": 1})) == "IBM"


def test_tie_breaks_are_alphabetical_not_incidental():
    """The tie-break has to be a stated rule, not just 'stable': most frequent, then alphabetical.
    Pinned so a future change cannot make it deterministic-but-arbitrary."""
    ents = build_entities([("Initech", "organization", 1), ("Initech", "company", 1)], min_mentions=1)
    assert ents["initech"]["type"] == "company"          # tie -> alphabetically first
    assert ents["initech"]["slug"] == "entities/company/initech"

    ents = build_entities([("Acme GmbH", "company", 1), ("Acme Corp", "company", 1)], min_mentions=1)
    assert next(iter(ents.values()))["title"] == "Acme Corp"

    # aliases: most frequent first, so render_node's aliases[:8] keeps the ones that matter. These
    # spellings all normalize to one key, so they are aliases of a single entity rather than three.
    ms = [("Acme  Corp", "company", 1), ("acme corp", "company", 9), ("ACME CORP", "company", 5)]
    ents = build_entities(ms, min_mentions=1)
    assert ents["acme"]["aliases"] == ["acme corp", "ACME CORP", "Acme  Corp"]
    # ...and on a count tie, alphabetically
    ms = [("Bee Corp", "company", 2), ("Ape Corp", "company", 2)]
    ents = build_entities(ms, min_mentions=1)
    assert [e["aliases"] for e in ents.values()] == [["Ape Corp"], ["Bee Corp"]]


def test_slug_collision_disambiguated():
    # "foo bar" and "foo-bar" are distinct keys but slugify identically -> disambiguate
    ms = [("Foo Bar", "company", 2), ("Foo-Bar", "company", 2)]
    ents = build_entities(ms, min_mentions=1)
    slugs = {e["slug"] for e in ents.values()}
    assert len(ents) == 2 and len(slugs) == 2
    assert "entities/company/foo-bar" in slugs
    assert "entities/company/foo-bar-2" in slugs
