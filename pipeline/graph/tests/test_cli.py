import os
import tempfile

from graph import cli


def test_cli_fail_fast_on_missing_in(capsys):
    rc = cli.main(["--in", "/nonexistent/never", "--out", "/tmp/graph-out-x"])
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err


def test_cli_runs_ok():
    with tempfile.TemporaryDirectory() as ind, tempfile.TemporaryDirectory() as outd:
        with open(os.path.join(ind, "a.md"), "w", encoding="utf-8") as f:
            f.write('---\ntype: x\nmentions:\n  - { name: "Globex", type: company }\n---\nbody')
        rc = cli.main(["--in", ind, "--out", outd, "--min-mentions", "1"])
        assert rc == 0
        assert os.path.exists(os.path.join(outd, "entities", "company", "globex.md"))
