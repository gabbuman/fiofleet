import importlib

from fiofleet import config


def test_env_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("FIOFLEET_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("FOUNDRIES_API_TOKEN", "envtok")
    monkeypatch.setenv("FOUNDRIES_FACTORY", "envfac")
    (tmp_path / "config").write_text("token=filetok\nfactory=filefac\n")
    token, factory, api_base = config.load()
    assert token == "envtok"
    assert factory == "envfac"
    assert api_base == config.DEFAULT_API_BASE


def test_file_used_when_env_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("FIOFLEET_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("FOUNDRIES_API_TOKEN", raising=False)
    monkeypatch.delenv("FOUNDRIES_FACTORY", raising=False)
    monkeypatch.delenv("FOUNDRIES_API_BASE", raising=False)
    (tmp_path / "config").write_text(
        "token=filetok\nfactory=filefac\napi_base=https://example/ota\n"
    )
    token, factory, api_base = config.load()
    assert (token, factory, api_base) == ("filetok", "filefac", "https://example/ota")


def test_save_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("FIOFLEET_CONFIG_DIR", str(tmp_path))
    for var in ("FOUNDRIES_API_TOKEN", "FOUNDRIES_FACTORY", "FOUNDRIES_API_BASE"):
        monkeypatch.delenv(var, raising=False)
    config.save("tok", "fac")
    token, factory, api_base = config.load()
    assert (token, factory) == ("tok", "fac")
    assert api_base == config.DEFAULT_API_BASE
