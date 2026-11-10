"""Live GitHub check for #414 (manual): skipped unless both env vars are set.

    PROJECT_ISSUES_LIVE_GITHUB_TOKEN   token with push access
    PROJECT_ISSUES_LIVE_GITHUB_REPO    owner/repo of a scratch repository

Run: python -m pytest -m live_github tests/test_414_live_github_attachments.py
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from lib_python_projects import ProjectConfig, ProjectsLoadResult
from project_issues_plugin.tools import _providers as providers_mod
from project_issues_plugin.tools import tickets as ticket_tools

_TOKEN = os.environ.get("PROJECT_ISSUES_LIVE_GITHUB_TOKEN")
_REPO = os.environ.get("PROJECT_ISSUES_LIVE_GITHUB_REPO")

pytestmark = [
    pytest.mark.live_github,
    pytest.mark.skipif(
        not (_TOKEN and _REPO),
        reason="set PROJECT_ISSUES_LIVE_GITHUB_TOKEN and PROJECT_ISSUES_LIVE_GITHUB_REPO (owner/repo)",
    ),
]

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class _StubMCP:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def test_live_attach_and_readback(monkeypatch, tmp_path: Path) -> None:
    project = ProjectConfig(
        id="live", provider="github", path=_REPO or "o/r", token_env="LIVE_GH_TOKEN",
        permissions={"issues": {"create": True, "modify": True},
                     "pulls": {"create": False, "modify": False, "merge": False}},
    )
    monkeypatch.setenv("LIVE_GH_TOKEN", _TOKEN or "")
    monkeypatch.setattr(
        providers_mod, "load_projects",
        lambda *a, **k: ProjectsLoadResult(projects=[project], state="ok", search_root="/tmp"),
    )
    stub = _StubMCP()
    ticket_tools.register(stub)
    tools = stub.tools

    png = tmp_path / "live.png"
    png.write_bytes(_PNG)
    created = tools["create_ticket"](
        project_id="live", title="#414 live attachment check", body="live",
        attachments=[{"path": str(png), "alt": "live"}],
    )
    assert "error" not in created, created
    tid = str(created["ticket"]["id"])
    try:
        full = tools["get_ticket"](project_id="live", ticket_id=tid, response="full")
        assert "ticket-attachments" in str(full)
        out = tmp_path / "back.png"
        imgs = tools["get_ticket_images"](project_id="live", ticket_id=tid)["images"]
        assert imgs
        saved = tools["get_ticket_images"](
            project_id="live", ticket_id=tid, save_index=imgs[0]["index"], dest_path=str(out),
        )
        assert "error" not in saved, saved
        assert out.read_bytes() == _PNG
    finally:
        tools["update_ticket"](project_id="live", ticket_id=tid, status="closed")
