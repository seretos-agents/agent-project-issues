"""Ticket #400: an abbreviated sha works for wait-pipeline and list_pipeline_runs.

The fix lives in lib-python-projects (>= v0.4.1): GitHub and Azure DevOps
expand an abbreviated sha to the full commit sha before filtering runs.
These tests drive the plugin's two entry points (the real CLI ``main`` in
process, and the ``list_pipeline_runs`` tool) against the real
``GitHubProvider`` / ``AzureDevOpsProvider`` with mocked HTTP. The mocked
runs listing answers only to the FULL 40-char sha, like the real API.
"""
from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from lib_python_projects import ProjectConfig, ProjectsLoadResult
from lib_python_projects.providers import azuredevops as ado_provider
from lib_python_projects.providers import github as github_provider
from project_issues_plugin import cli
from project_issues_plugin.tools import _providers as providers_mod
from project_issues_plugin.tools import pipelines as pipeline_tools

from tests.test_pipelines import _install_mock, _json, _StubMCP  # noqa: F401

FULL = "c847b8a92afd46fe7914917cefd015b8a5e1eec1"
SHORT = FULL[:7]
TOKEN_ENV = "TEST_400_TOKEN"


def _github_project() -> ProjectConfig:
    return ProjectConfig(
        id="acme", provider="github", path="acme/backend", token_env=TOKEN_ENV
    )


def _ado_project() -> ProjectConfig:
    return ProjectConfig(
        id="ado", provider="azuredevops", path="org/proj/repo", token_env=TOKEN_ENV
    )


def _github_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path in (
        f"/repos/acme/backend/commits/{SHORT}",
        f"/repos/acme/backend/commits/{FULL}",
    ):
        return _json({"sha": FULL})
    if path == "/repos/acme/backend/actions/runs":
        if request.url.params.get("head_sha") == FULL:
            return _json(
                {
                    "total_count": 1,
                    "workflow_runs": [
                        {
                            "id": 101,
                            "name": "CI",
                            "head_branch": "main",
                            "head_sha": FULL,
                            "event": "push",
                            "status": "completed",
                            "conclusion": "success",
                            "html_url": "https://github.com/acme/backend/actions/runs/101",
                            "created_at": "2024-01-01T00:00:00Z",
                            "updated_at": "2024-01-01T00:05:00Z",
                            "run_attempt": 1,
                        }
                    ],
                }
            )
        return _json({"total_count": 0, "workflow_runs": []})
    if path == "/repos/acme/backend/actions/workflows":
        return _json(
            {"total_count": 1, "workflows": [{"id": 1, "name": "CI", "path": ".github/workflows/ci.yml"}]}
        )
    raise AssertionError(f"unexpected GitHub request: {request.method} {request.url}")


def _ado_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/org/proj/_apis/build/builds":
        return _json(
            {
                "value": [
                    {
                        "id": 202,
                        "buildNumber": "202",
                        "status": "completed",
                        "result": "succeeded",
                        "sourceVersion": FULL,
                        "sourceBranch": "refs/heads/main",
                        "definition": {"id": 7, "name": "CI"},
                        "reason": "manual",
                        "queueTime": "2024-01-01T00:00:00Z",
                        "finishTime": "2024-01-01T00:05:00Z",
                        "_links": {"web": {"href": "https://dev.azure.com/org/proj/_build/results?buildId=202"}},
                    }
                ]
            }
        )
    if path == "/org/proj/_apis/git/repositories":
        return _json({"value": [{"name": "repo", "id": "repo-guid"}]})
    if path in (
        f"/org/proj/_apis/git/repositories/repo-guid/commits/{SHORT}",
        f"/org/proj/_apis/git/repositories/repo-guid/commits/{FULL}",
    ):
        return _json({"commitId": FULL})
    return _json({"message": "not found"}, status_code=404)


def _install_ado_mock(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(wrapped)

    def fake_client(project, token, **_kw) -> httpx.Client:
        return httpx.Client(
            base_url="https://dev.azure.com",
            headers={"Accept": "application/json"},
            transport=transport,
        )

    monkeypatch.setattr(ado_provider, "_client", fake_client)
    return seen


def _setup(monkeypatch: pytest.MonkeyPatch, provider: str):
    """Install the mocked HTTP layer + project resolution; return (project, seen)."""
    if provider == "github":
        project = _github_project()
        seen = _install_mock(monkeypatch, _github_handler)
    else:
        project = _ado_project()
        # Reset the module-level repo-id cache so test order cannot matter.
        for name in dir(ado_provider):
            obj = getattr(ado_provider, name)
            if name.lower().endswith("repo_id_cache") and isinstance(obj, dict):
                obj.clear()
        seen = _install_ado_mock(monkeypatch, _ado_handler)

    def fake_load_projects(*_a, **_kw):
        return ProjectsLoadResult(projects=[project], state="ok", search_root="/tmp")

    monkeypatch.setattr(providers_mod, "load_projects", fake_load_projects)
    monkeypatch.setenv(TOKEN_ENV, "tok")
    return project, seen


PROVIDERS = ["github", "azuredevops"]


@pytest.mark.parametrize("sha", [SHORT, FULL], ids=["short", "full"])
@pytest.mark.parametrize("provider", PROVIDERS)
def test_wait_pipeline_short_sha_green_first_poll(monkeypatch, capsys, provider, sha):
    project, seen = _setup(monkeypatch, provider)

    code = cli.main(
        ["wait-pipeline", "--project", project.id, "--sha", sha,
         "--timeout", "2", "--interval", "1"]
    )

    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0, out
    assert out["state"] == "success"
    assert len(out["runs"]) == 1
    assert out["waited_s"] < 1
    if provider == "github":
        run_reqs = [r for r in seen if r.url.path.endswith("/actions/runs")]
        assert len(run_reqs) == 1
        assert run_reqs[0].url.params["head_sha"] == FULL


@pytest.mark.parametrize("provider", PROVIDERS)
def test_list_pipeline_runs_short_sha_matches_full(monkeypatch, provider):
    project, _seen = _setup(monkeypatch, provider)
    stub = _StubMCP()
    pipeline_tools.register(stub)
    list_runs = stub.tools["list_pipeline_runs"]

    short = list_runs(project_id=project.id, commit_sha=SHORT)
    full = list_runs(project_id=project.id, commit_sha=FULL)

    assert "error" not in short, short
    assert short["addressed_by"] == "commit"
    assert len(short["runs"]) == 1
    assert short["runs"] == full["runs"]
