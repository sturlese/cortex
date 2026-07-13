from graph.pages import _y, page_mentions, render_node, rewrite_doc, split_frontmatter

DOC = """---
type: contract
title: Test
mentions:
  - { name: "Initech", type: company }
  - { name: "Jane Doe", type: person }
---

Contract body.
"""


def test_split_frontmatter():
    fm, body = split_frontmatter(DOC)
    assert fm["type"] == "contract"
    assert "Contract body." in body


def test_split_no_frontmatter_and_bad_yaml():
    assert split_frontmatter("# body only") == ({}, "# body only")
    fm, _ = split_frontmatter("---\n: : bad : yaml\n---\nx")
    assert fm == {}


def test_page_mentions():
    ms = page_mentions(DOC)
    assert ("Initech", "company") in ms
    assert ("Jane Doe", "person") in ms


def test_rewrite_doc_links_only_kept_entities():
    ents = {"initech": {"slug": "entities/company/initech", "title": "Initech",
                        "type": "company", "aliases": ["Initech"]}}
    out = rewrite_doc(DOC, ents)
    assert "## Related entities" in out
    assert "[[entities/company/initech|Initech]]" in out
    section = out.split("Related entities")[1]
    assert "Jane Doe" not in section          # filtered -> not linked
    assert "Contract body." in out            # body untouched


def test_render_node():
    s = render_node({"type": "company", "title": "Initech",
                     "aliases": ["Initech", "Initech, S.L."], "mentions": 3})
    assert "type: company" in s
    assert "title: Initech" in s
    assert "aliases:" in s and "Initech, S.L." in s


def test_render_node_hostile_names_stay_parseable():
    """Entity names starting with a YAML indicator (@, &) or containing quotes must not break
    the node page's frontmatter."""
    import yaml
    s = render_node({"type": "company", "title": "@AcmeCorp",
                     "aliases": ["@AcmeCorp", 'Joe "Bo" Smith'], "mentions": 2})
    fm = yaml.safe_load(s.split("\n---\n", 1)[0].removeprefix("---\n"))
    assert fm["title"] == "@AcmeCorp"
    assert 'Joe "Bo" Smith' in fm["aliases"]


def test_render_node_yaml_implicit_typed_names_round_trip():
    """Entity names that look like YAML 1.1 implicit scalars -- ISO dates, hex/binary/underscored
    ints -- must survive the frontmatter round-trip as strings. Otherwise yaml.safe_load re-types
    the title/alias (2001-12-14 -> datetime.date, 0x1F -> 31) and the entity key changes on read."""
    import yaml
    # Incl. invalid dates that match YAML's timestamp regex but raise a bare ValueError in
    # datetime.date() -- these must quote (and round-trip), not crash render_node.
    for name in ["2001-12-14", "0x1F", "0b101", "1_000", "0000-00-00", "2026-02-30", "2026-13-01"]:
        # title path: the implicit-typed name is the title
        s = render_node({"type": "company", "title": name,
                         "aliases": ["Globex"], "mentions": 2})
        fm = yaml.safe_load(s.split("\n---\n", 1)[0].removeprefix("---\n"))
        assert fm["title"] == name, (name, fm["title"])
        # alias path: distinct title so the implicit-typed alias is not filtered as == title
        s2 = render_node({"type": "company", "title": "Acme Holdings",
                          "aliases": [name], "mentions": 2})
        fm2 = yaml.safe_load(s2.split("\n---\n", 1)[0].removeprefix("---\n"))
        assert name in fm2["aliases"], (name, fm2["aliases"])


def test_yaml_scalar_emit_parse_round_trips_as_a_pair():
    """Contract guard binding the emitter to the parser: what _y writes, split_frontmatter must
    read back as the identical STRING. clean/page._yaml (emit) and answer/index.split_frontmatter
    (parse) are hand-mirrored copies of these two in other packages that share no code, so this
    pins the invariant they all depend on — an unquoted YAML 1.1 implicit scalar (date, bool, int,
    null-word) must never be re-typed on read. Exercises the emit->parse path as one unit, unlike
    the render_node tests above that parse with yaml.safe_load directly."""
    tricky = [
        "2001-12-14", "0000-00-00", "2026-02-30",     # ISO dates, incl. invalid-but-timestamp-shaped
        "on", "Off", "yes", "No", "true", "FALSE",     # YAML 1.1 booleans
        "null", "Null", "~", "none",                   # null-ish (lowercase 'none' is a plain string)
        "1984", "0x1F", "0b101", "1_000", "3.14",      # decimal/hex/binary/underscored ints, float
        "a: b", "value #hash", "- dash", "* star",     # YAML indicators -> must quote
        "Acme Holdings", "café-AG",                    # genuinely plain (ascii + unicode)
    ]
    for v in tricky:
        fm, body = split_frontmatter(f"---\ntitle: {_y(v)}\n---\nbody\n")
        assert fm["title"] == v, (v, fm.get("title"))
        assert body.strip() == "body"


def test_page_mentions_tolerates_non_string_names():
    """A mention name YAML-parsed as a bool/int (unquoted `On`, `1984`) must not crash the graph."""
    doc = ('---\ntype: x\nmentions:\n  - { name: 1984, type: other }\n'
           '  - { name: true, type: company }\n---\nbody\n')
    ms = page_mentions(doc)
    assert ("1984", "other") in ms          # numeric coerced to string, kept
    assert all(not isinstance(n, bool) for n, _ in ms)   # accidental boolean dropped
