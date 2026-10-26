"""Load and validate config/config.toml.

Relative paths in the file are resolved against the project root, never the
current working directory. The GitHub token is never read from this file; it
comes from the GITHUB_TOKEN environment variable.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

# src/organizador/config.py -> project root (editable install, src layout).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_RELATIVE_PATH = Path("config") / "config.toml"
DEFAULT_LOOKBACK_DAYS = 15

_TOP_LEVEL_KEYS = {"vault_path", "state_path", "github"}
_GITHUB_KEYS = {"owner", "repo", "initial_lookback_days"}
_PLACEHOLDERS = {"owner": "OWNER", "repo": "REPO"}

# GitHub naming rules: owners are alphanumeric or hyphen, cannot start or end
# with a hyphen (max 39 chars); repositories also allow "." and "_" (max 100 chars).
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class ConfigError(Exception):
    """The configuration file is missing, unreadable, or invalid."""


@dataclass(frozen=True)
class Config:
    vault_path: Path
    state_path: Path
    github_owner: str
    github_repo: str
    initial_lookback_days: int


def load_config(project_root: Path = PROJECT_ROOT) -> Config:
    project_root = Path(project_root).resolve()
    path = project_root / CONFIG_RELATIVE_PATH

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(
            f"Config file not found: {path}. "
            "Copy config/config.example.toml to config/config.toml and edit it."
        ) from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Malformed TOML in {path}: {e}") from None
    except OSError as e:
        raise ConfigError(f"Cannot read config file {path}: {e}") from None

    def fail(message: str) -> ConfigError:
        return ConfigError(f"Invalid config {path}: {message}")

    _check_keys(data, _TOP_LEVEL_KEYS, "", fail)

    github = data.get("github")
    if not isinstance(github, dict):
        raise fail("missing [github] section")
    _check_keys(github, _GITHUB_KEYS, "github.", fail)

    vault_path = _resolve_path(data, "vault_path", project_root, fail)
    state_path = _resolve_path(data, "state_path", project_root, fail)
    if vault_path == state_path or vault_path in state_path.parents:
        raise fail("state_path must be outside vault_path")
    if state_path in vault_path.parents:
        raise fail("vault_path must be outside state_path")

    owner = _required_str(github, "owner", "github.owner", fail)
    if not _OWNER_RE.match(owner):
        raise fail(f"github.owner {owner!r} is not a valid GitHub owner name")
    repo = _required_str(github, "repo", "github.repo", fail)
    if repo in {".", ".."} or not _REPO_RE.match(repo):
        raise fail(f"github.repo {repo!r} is not a valid GitHub repository name")
    for key, placeholder in _PLACEHOLDERS.items():
        if github[key] == placeholder:
            raise fail(f"github.{key} is still the placeholder {placeholder!r}")

    lookback = github.get("initial_lookback_days", DEFAULT_LOOKBACK_DAYS)
    # bool is a subclass of int; reject it explicitly.
    if not isinstance(lookback, int) or isinstance(lookback, bool) or lookback < 1:
        raise fail(
            f"github.initial_lookback_days must be a whole number >= 1, got {lookback!r}"
        )

    return Config(
        vault_path=vault_path,
        state_path=state_path,
        github_owner=owner,
        github_repo=repo,
        initial_lookback_days=lookback,
    )


def _check_keys(table, allowed, prefix, fail):
    for key in table:
        if "token" in key.lower():
            raise fail(
                f"{prefix}{key}: tokens must not be stored in the config file; "
                "set the GITHUB_TOKEN environment variable instead"
            )
        if key not in allowed:
            raise fail(f"unknown key {prefix}{key}")


def _required_str(table, key, name, fail):
    value = table.get(key)
    if value is None:
        raise fail(f"missing required key {name}")
    if not isinstance(value, str) or not value.strip():
        raise fail(f"{name} must be a non-empty string")
    return value.strip()


def _resolve_path(table, key, project_root, fail):
    path = Path(_required_str(table, key, key, fail))
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()
