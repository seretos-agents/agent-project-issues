"""Driving tests for work package #408 (#407 bump to lib-python-projects
v0.4.3, whose loader anchors a relative `local_path` at the config file's
directory): `list_projects` / `search_projects` must return a relative
`local_path` as an absolute path under the directory of the real
`.seretos/projects.yml`, an absolute one byte-for-byte unchanged, and an
omitted one as None.

Uses the real lib loader (no monkeypatched `load_projects`, no fake
provider), pattern of `test_375_board_columns_without_binding.py`.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from project_issues_plugin.tools import projects as proj_tools


def _abs_path(tmp_path: Path) -> str:
    return str(tmp_path / "abs-checkout")


def _write_cfg(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / ".seretos" / "projects.yml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # Single-quoted YAML scalar keeps Windows backslashes literal.
    cfg.write_text(textwrap.dedent(
        f"""
        version: 1
        projects:
          - id: rel-checkout
            provider: github
            path: acme/rel-checkout
            local_path: projects/rel-checkout
          - id: abs-checkout
            provider: github
            path: acme/abs-checkout
            local_path: '{_abs_path(tmp_path)}'
          - id: no-checkout
            provider: github
            path: acme/no-checkout
        """
    ).lstrip())


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _write_cfg(tmp_path)
    for var in (
        "PROJECT_ISSUES_CONFIG", "PROJECT_ISSUES_PLUGIN_ROOT",
        "PROJECT_ISSUES_PLUGIN_CWD", "CLAUDE_PROJECT_DIR",
        "XDG_CONFIG_HOME", "APPDATA", "USERPROFILE",
        "PROJECT_ISSUES_DEBUG",
        "GITHUB_TOKEN", "GITLAB_TOKEN", "AZURE_DEVOPS_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PROJECT_ISSUES_PLUGIN_CWD", str(tmp_path))

    captured: dict = {}

    class _Stub:
        def tool(self):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    proj_tools.register(_Stub())
    return captured


def _listed(configured: dict) -> dict:
    out = configured["list_projects"](fields="full")
    assert out["state"] == "ok", out
    return {p["id"]: p for p in out["projects"]}


def test_relative_local_path_anchored_at_config_dir(
    configured: dict, tmp_path: Path,
) -> None:
    """Driving test. RED on 0.4.1: the relative string passes through
    unchanged, so it is not absolute."""
    v = _listed(configured)["rel-checkout"]["local_path"]
    assert Path(v).is_absolute(), v
    assert Path(v) == (tmp_path / ".seretos" / "projects" / "rel-checkout").resolve(), v


def test_absolute_local_path_unchanged(configured: dict, tmp_path: Path) -> None:
    v = _listed(configured)["abs-checkout"]["local_path"]
    assert v == _abs_path(tmp_path), v


def test_omitted_local_path_is_none(configured: dict) -> None:
    assert _listed(configured)["no-checkout"]["local_path"] is None


def test_search_projects_returns_same_resolved_local_path(
    configured: dict, tmp_path: Path,
) -> None:
    out = configured["search_projects"](query="rel-checkout", fields="full")
    rows = {p["id"]: p for p in out["matches"]}
    v = rows["rel-checkout"]["local_path"]
    assert Path(v).is_absolute(), v
    assert Path(v) == (tmp_path / ".seretos" / "projects" / "rel-checkout").resolve(), v
