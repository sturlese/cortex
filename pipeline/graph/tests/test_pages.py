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
