import os
import stat

from finagent import keystore
from finagent.cli import main
from finagent.config import load_config


def test_prompt_saves_private_file_and_loads(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cfg" / "env"
    monkeypatch.setenv("FINAGENT_ENV_FILE", str(path))
    answers = iter(["client-123"])
    hidden = iter(["prod-secret", "sandbox-secret", "", "", ""])
    keystore.prompt_and_save(ask=lambda p: next(answers), ask_hidden=lambda p: next(hidden), say=lambda s: None)
    assert keystore.read_env_file(path) == {"PLAID_CLIENT_ID": "client-123", "PLAID_SECRET": "prod-secret",
                                            "PLAID_SANDBOX_SECRET": "sandbox-secret"}
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # Pressing Enter keeps what's saved
    keystore.prompt_and_save(ask=lambda p: "", ask_hidden=lambda p: "", say=lambda s: None)
    assert keystore.read_env_file(path)["PLAID_SECRET"] == "prod-secret"

    assert keystore.load_env_file(path) == ["PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_SANDBOX_SECRET"]
    cfg = load_config(overrides={"source": {"type": "plaid"}})
    assert cfg.plaid_keys() == ("client-123", "prod-secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    assert cfg.plaid_keys() == ("client-123", "sandbox-secret")  # sandbox uses its own secret


def test_real_environment_wins_and_quotes_are_stripped(tmp_path, monkeypatch):
    path = tmp_path / "env"
    path.write_text('# comment\nexport PLAID_CLIENT_ID="from-file"\nPLAID_SECRET=\'s3cret\'\nJUNK\n')
    monkeypatch.setenv("PLAID_CLIENT_ID", "from-shell")
    assert keystore.load_env_file(path) == ["PLAID_SECRET"]
    assert os.environ["PLAID_CLIENT_ID"] == "from-shell" and os.environ["PLAID_SECRET"] == "s3cret"


def test_keys_show_never_prints_values(tmp_path, monkeypatch, capsys):
    path = tmp_path / "env"
    monkeypatch.setenv("FINAGENT_ENV_FILE", str(path))
    keystore.write_env_file({"PLAID_CLIENT_ID": "client-123", "PLAID_SECRET": "super-secret-value"}, path)
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    assert main(["--config", str(cfg), "keys", "--show"]) == 0
    out = capsys.readouterr().out
    assert "PLAID_SECRET" in out and "saved" in out
    assert "super-secret-value" not in out and "client-123" not in out
