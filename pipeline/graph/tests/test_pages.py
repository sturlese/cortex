from graph.pages import page_mentions, render_node, rewrite_doc, split_frontmatter

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


def test_page_mentions_tolerates_non_string_names():
    """A mention name YAML-parsed as a bool/int (unquoted `On`, `1984`) must not crash the graph."""
    doc = ('---\ntype: x\nmentions:\n  - { name: 1984, type: other }\n'
           '  - { name: true, type: company }\n---\nbody\n')
    ms = page_mentions(doc)
    assert ("1984", "other") in ms          # numeric coerced to string, kept
    assert all(not isinstance(n, bool) for n, _ in ms)   # accidental boolean dropped
