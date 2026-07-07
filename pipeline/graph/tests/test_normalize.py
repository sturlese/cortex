from graph.normalize import is_noise, normalize, slugify


def test_normalize_strips_case_accents_suffixes():
    assert normalize("WAYFARER, S.L.") == "wayfarer"
    assert normalize("Wayfarer SL") == "wayfarer"
    assert normalize("Initech Technologies, S.L.") == "initech technologies"
    assert normalize("Globex S.A.P.I. de C.V.") == "globex"
    assert normalize("Café Ltd.") == "cafe"


def test_normalize_variants_collapse_to_same_key():
    keys = {normalize(n) for n in ["Hooli", "Hooli, S.L.", "HOOLI", "Hooli Inc."]}
    assert keys == {"hooli"}


def test_slugify():
    assert slugify("Wayfarer") == "wayfarer"
    assert slugify("François Müller") == "francois-muller"
    assert slugify("!!!") == "x"


def test_is_noise():
    assert is_noise("a b t")        # initials
    assert is_noise("a b")
    assert is_noise("xy")           # <3 chars
    assert not is_noise("wayfarer")
    assert not is_noise("the example network")
