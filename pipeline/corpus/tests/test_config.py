from corpus.config import load_config, profile_value


def test_load_config_missing_returns_empty():
    assert load_config(None) == {}
    assert load_config("/nonexistent.toml") == {}


def test_profile_value_precedence(tmp_path):
    p = tmp_path / "corpus_config.toml"
    p.write_text('[defaults]\nworkdir = "/w/default"\n\n[profile.sample]\nworkdir = "/w/sample"\n')
    cfg = load_config(str(p))
    assert profile_value(cfg, "sample", "workdir") == "/w/sample"
    assert profile_value(cfg, "other", "workdir") == "/w/default"
    assert profile_value(cfg, None, "workdir") == "/w/default"
    assert profile_value(cfg, "sample", "missing") is None
