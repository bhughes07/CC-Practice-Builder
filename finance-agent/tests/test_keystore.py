import os
import stat

from finagent import keystore
from finagent.cli import main
from finagent.config import load_config

CID = "5f1a2b3c4d5e6f7a8b9c0d1e"            # 24 hex, shaped like a Plaid client ID (not real)
PROD = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5"     # 30 hex, shaped like a Plaid secret (not real)
SBX = "0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d"


def test_prompt_saves_private_file_and_loads(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cfg" / "env"
    monkeypatch.setenv("FINAGENT_ENV_FILE", str(path))
    answers = iter([CID])
    hidden = iter([PROD, SBX, "", "", ""])
    keystore.prompt_and_save(ask=lambda p: next(answers), ask_hidden=lambda p: next(hidden), say=lambda s: None)
    assert keystore.read_env_file(path) == {"PLAID_CLIENT_ID": CID, "PLAID_SECRET": PROD, "PLAID_SANDBOX_SECRET": SBX}
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # Pressing Enter keeps what's saved
    keystore.prompt_and_save(ask=lambda p: "", ask_hidden=lambda p: "", say=lambda s: None)
    assert keystore.read_env_file(path)["PLAID_SECRET"] == PROD

    assert keystore.load_env_file(path) == ["PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_SANDBOX_SECRET"]
    cfg = load_config(overrides={"source": {"type": "plaid"}})
    assert cfg.plaid_keys() == (CID, PROD)
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    assert cfg.plaid_keys() == (CID, SBX)  # sandbox uses its own secret


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


def test_doubled_masked_and_word_pastes():
    assert keystore.clean("PLAID_SECRET", PROD + PROD) == PROD             # pasted twice
    assert keystore.clean("PLAID_SECRET", f"\u201c{PROD}\u201d\u200b ") == PROD  # Word smart quotes + zero-width
    assert keystore.clean("PLAID_CLIENT_ID", f" {CID[:12]} {CID[12:]}\n") == CID
    assert keystore.diagnose("PLAID_SECRET", PROD) == []
    assert "masked" in keystore.diagnose("PLAID_SECRET", "••••••••••••••••••••••••••••••")[0]
    assert "pasted twice" in keystore.diagnose("PLAID_SECRET", PROD + PROD)[0]
    assert "30" in keystore.diagnose("PLAID_SECRET", PROD[:20])[0]
    assert "0–9 and a–f" in keystore.diagnose("PLAID_SECRET", "your-production-secret")[0]


def test_prompt_rejects_bad_paste_then_accepts(tmp_path, monkeypatch):
    path = tmp_path / "env"
    monkeypatch.setenv("FINAGENT_ENV_FILE", str(path))
    said = []
    hidden = iter(["••••", PROD, "", "", "", ""])
    keystore.prompt_and_save(ask=lambda p: CID, ask_hidden=lambda p: next(hidden), say=said.append,
                             only=["PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_SANDBOX_SECRET"])
    assert keystore.read_env_file(path) == {"PLAID_CLIENT_ID": CID, "PLAID_SECRET": PROD}
    assert any("masked" in s for s in said)


def test_existing_doubled_secret_heals_on_load_and_check(tmp_path, monkeypatch, capsys):
    path = tmp_path / "env"
    monkeypatch.setenv("FINAGENT_ENV_FILE", str(path))
    path.write_text(f"PLAID_CLIENT_ID={CID}\nPLAID_SECRET={PROD}{PROD}\nPLAID_SANDBOX_SECRET={SBX}\n")
    assert keystore.read_env_file(path)["PLAID_SECRET"] == PROD
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    assert main(["--config", str(cfg), "keys", "--check"]) == 0
    out = capsys.readouterr().out
    assert "All Plaid keys look right" in out and PROD not in out and CID not in out


def test_check_flags_shell_override_and_swapped_secrets(tmp_path, monkeypatch, capsys):
    path = tmp_path / "env"
    monkeypatch.setenv("FINAGENT_ENV_FILE", str(path))
    keystore.write_env_file({"PLAID_CLIENT_ID": CID, "PLAID_SECRET": PROD, "PLAID_SANDBOX_SECRET": PROD}, path)
    monkeypatch.setenv("PLAID_SECRET", "your-production-secret")  # old README placeholder in ~/.zshrc
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    assert main(["--config", str(cfg), "keys", "--check"]) == 1
    out = capsys.readouterr().out
    assert "overriding the saved key" in out and "unset PLAID_SECRET" in out
    assert "identical" in out and "your-production-secret" not in out


def test_friendly_plaid_errors():
    from finagent.sources.plaid import PlaidError, friendly
    msg = friendly(PlaidError("INVALID_FIELD", "secret must be a properly formatted, non-empty string"))
    assert "Production secret" in msg and "keys --check" in msg
    assert "Sandbox" in friendly(PlaidError("INVALID_API_KEYS", "invalid client_id or secret provided"), "sandbox")
