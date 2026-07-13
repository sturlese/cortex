import pytest

from corpus.paths import PathError, require_corpus, require_workdir


def test_require_corpus(tmp_path):
    assert require_corpus(str(tmp_path)) == str(tmp_path)
    with pytest.raises(PathError, match="required"):
        require_corpus(None)
    with pytest.raises(PathError, match="does not exist"):
        require_corpus("/nonexistent-dir")


def test_require_workdir_creates(tmp_path):
    w = tmp_path / "new"
    assert require_workdir(str(w), create=True) == str(w)
    assert w.is_dir()
    with pytest.raises(PathError, match="required"):
        require_workdir(None)
    with pytest.raises(PathError, match="does not exist"):
        require_workdir(str(tmp_path / "missing"), create=False)
