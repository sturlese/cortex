import os
import tempfile

from graph.build import build_graph

DOC = """---
type: contract
title: Loan agreement
mentions:
  - { name: "Initech", type: company }
  - { name: "Initech, S.L.", type: company }
  - { name: "A.B.", type: person }
---

Body.
"""


def test_build_graph_end_to_end():
    with tempfile.TemporaryDirectory() as ind, tempfile.TemporaryDirectory() as outd:
        os.makedirs(os.path.join(ind, "entities"))
        with open(os.path.join(ind, "entities", "doc.md"), "w", encoding="utf-8") as f:
            f.write(DOC)

        stats = build_graph(ind, outd, min_mentions=1)

        assert stats["docs"] == 1
        assert stats["entities"] == 1            # Initech variants merged; A.B. dropped as noise
        out_doc = open(os.path.join(outd, "entities", "doc.md"), encoding="utf-8").read()
        assert "[[entities/company/initech|Initech]]" in out_doc
        assert "A.B." not in out_doc.split("Related entities")[1]
        assert os.path.exists(os.path.join(outd, "entities", "company", "initech.md"))


def test_min_mentions_filters_across_docs():
    with tempfile.TemporaryDirectory() as ind, tempfile.TemporaryDirectory() as outd:
        # "Globex" in 2 docs, "Acme" in 1 -> with min_mentions=2 only Globex survives
        for i, body in enumerate([
            '---\ntype: x\nmentions:\n  - { name: "Globex", type: company }\n'
            '  - { name: "Acme", type: company }\n---\na',
            '---\ntype: x\nmentions:\n  - { name: "Globex", type: company }\n---\nb',
        ]):
            with open(os.path.join(ind, f"d{i}.md"), "w", encoding="utf-8") as f:
                f.write(body)
        stats = build_graph(ind, outd, min_mentions=2)
        assert stats["entities"] == 1
        assert os.path.exists(os.path.join(outd, "entities", "company", "globex.md"))
        assert not os.path.exists(os.path.join(outd, "entities", "company", "acme.md"))
