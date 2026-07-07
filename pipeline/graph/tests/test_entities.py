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


def test_slug_collision_disambiguated():
    # "foo bar" and "foo-bar" are distinct keys but slugify identically -> disambiguate
    ms = [("Foo Bar", "company", 2), ("Foo-Bar", "company", 2)]
    ents = build_entities(ms, min_mentions=1)
    slugs = {e["slug"] for e in ents.values()}
    assert len(ents) == 2 and len(slugs) == 2
    assert "entities/company/foo-bar" in slugs
    assert "entities/company/foo-bar-2" in slugs
