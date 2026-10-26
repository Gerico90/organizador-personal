import shutil
from pathlib import Path

import pytest

from organizador.config import PROJECT_ROOT, ConfigError, load_config

VALID = """\
vault_path = "vault"
state_path = "state"

[github]
owner = "octo-cat"
repo = "my.repo_1"
initial_lookback_days = 15
"""


def write_config(root: Path, text: str) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "config.toml").write_text(text, encoding="utf-8")


def load_text(tmp_path: Path, text: str):
    write_config(tmp_path, text)
    return load_config(tmp_path)


def test_valid_config(tmp_path):
    cfg = load_text(tmp_path, VALID)
    root = tmp_path.resolve()
    assert cfg.vault_path == root / "vault"
    assert cfg.state_path == root / "state"
    assert cfg.github_owner == "octo-cat"
    assert cfg.github_repo == "my.repo_1"
    assert cfg.initial_lookback_days == 15


def test_lookback_defaults_to_15(tmp_path):
    cfg = load_text(tmp_path, VALID.replace("initial_lookback_days = 15\n", ""))
    assert cfg.initial_lookback_days == 15


def test_absolute_paths_kept(tmp_path):
    vault = (tmp_path / "elsewhere" / "vault").resolve()
    text = VALID.replace('vault_path = "vault"', f"vault_path = '{vault}'")
    assert load_text(tmp_path, text).vault_path == vault


def test_paths_independent_of_working_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    write_config(project, VALID)
    other = tmp_path / "other"
    other.mkdir()

    monkeypatch.chdir(project)
    from_root = load_config(project)
    monkeypatch.chdir(other)
    from_other = load_config(project)

    assert from_root == from_other
    assert from_other.vault_path == project.resolve() / "vault"
    assert from_other.state_path == project.resolve() / "state"


def test_default_project_root_is_this_project():
    assert (PROJECT_ROOT / "pyproject.toml").is_file()
    assert (PROJECT_ROOT / "src" / "organizador" / "config.py").is_file()


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="Config file not found.*config.example.toml"):
        load_config(tmp_path)


def test_malformed_toml(tmp_path):
    with pytest.raises(ConfigError, match="Malformed TOML"):
        load_text(tmp_path, 'vault_path = "vault\n[github\n')


def test_example_config_is_rejected_as_placeholder(tmp_path):
    (tmp_path / "config").mkdir()
    shutil.copy(PROJECT_ROOT / "config" / "config.example.toml", tmp_path / "config" / "config.toml")
    with pytest.raises(ConfigError, match="placeholder"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ('vault_path = "vault"\n', "", "missing required key vault_path"),
        ('state_path = "state"\n', "", "missing required key state_path"),
        ('owner = "octo-cat"\n', "", "missing required key github.owner"),
        ('repo = "my.repo_1"\n', "", "missing required key github.repo"),
        ('vault_path = "vault"', 'vault_path = ""', "vault_path must be a non-empty string"),
        ('state_path = "state"', "state_path = 5", "state_path must be a non-empty string"),
        ('owner = "octo-cat"', 'owner = "bad owner"', "not a valid GitHub owner"),
        ('owner = "octo-cat"', 'owner = "-lead"', "not a valid GitHub owner"),
        ('repo = "my.repo_1"', 'repo = "a/b"', "not a valid GitHub repository"),
        ('repo = "my.repo_1"', 'repo = ".."', "not a valid GitHub repository"),
        ("initial_lookback_days = 15", "initial_lookback_days = 0", "initial_lookback_days"),
        ("initial_lookback_days = 15", 'initial_lookback_days = "15"', "initial_lookback_days"),
        ("initial_lookback_days = 15", "initial_lookback_days = 1.5", "initial_lookback_days"),
        ("initial_lookback_days = 15", "initial_lookback_days = true", "initial_lookback_days"),
        ('state_path = "state"', 'state_path = "vault"', "state_path must be outside vault_path"),
        ('state_path = "state"', 'state_path = "vault/state"', "state_path must be outside vault_path"),
        ('vault_path = "vault"', 'vault_path = "state/vault"', "vault_path must be outside state_path"),
        ("[github]\n", "[other]\n", "unknown key other"),
        ('repo = "my.repo_1"\n', 'repo = "my.repo_1"\nbranch = "main"\n', "unknown key github.branch"),
    ],
)
def test_invalid_values(tmp_path, old, new, message):
    assert old in VALID
    with pytest.raises(ConfigError, match=message):
        load_text(tmp_path, VALID.replace(old, new))


def test_owner_ending_in_hyphen_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="not a valid GitHub owner"):
        load_text(tmp_path, VALID.replace('owner = "octo-cat"', 'owner = "octocat-"'))


def test_missing_github_section(tmp_path):
    with pytest.raises(ConfigError, match=r"missing \[github\] section"):
        load_text(tmp_path, 'vault_path = "vault"\nstate_path = "state"\n')


@pytest.mark.parametrize("key", ["token", "github_token", "GITHUB_TOKEN"])
def test_token_in_config_is_rejected(tmp_path, key):
    text = VALID.replace('repo = "my.repo_1"\n', f'repo = "my.repo_1"\n{key} = "x"\n')
    with pytest.raises(ConfigError, match="GITHUB_TOKEN environment variable"):
        load_text(tmp_path, text)
