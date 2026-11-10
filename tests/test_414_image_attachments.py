"""Driving tests for #414: image attachments on create_ticket /
update_ticket / add_comment plus the read tool get_ticket_images.

Harness: the REAL `GitHubProvider` behind `httpx.MockTransport`
(`github_provider._client` patched, like tests/test_307_ticket_templates.py),
with `_providers.load_projects` stubbed. Unsupported-provider tests use the
real `GitLabProvider`, which raises before any HTTP call.
"""
from __future__ import annotations

import base64
import json
import re
import typing
from pathlib import Path
from typing import Callable

import httpx
import pytest

from lib_python_projects import ProjectConfig, ProjectsLoadResult
from lib_python_projects.providers import attachments as attachments_lib
from lib_python_projects.providers import github as github_provider
from lib_python_projects.providers import gitlab as gitlab_provider
from project_issues_plugin.tools import _providers as providers_mod
from project_issues_plugin.tools import tickets as ticket_tools

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
_BASE = "https://github.com/acme/backend/blob/ticket-attachments/"


def _served_for(path: str) -> bytes:
    """Distinct bytes per image file so a save of the wrong image is visible."""
    return b"PNG-served:" + path.rsplit("/", 1)[-1].encode()


# ---------- scaffolding ------------------------------------------------------


class _StubMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


def _project(provider: str = "github") -> ProjectConfig:
    return ProjectConfig(
        id="acme",
        provider=provider,
        path="acme/backend",
        token_env="TOKEN_ACME",
        permissions={
            "issues": {"create": True, "modify": True},
            "pulls": {"create": False, "modify": False, "merge": False},
        },
    )


def _register(monkeypatch: pytest.MonkeyPatch, project: ProjectConfig) -> dict[str, Callable]:
    def fake_load_projects(*_a, **_k):
        return ProjectsLoadResult(projects=[project], state="ok", search_root="/tmp")

    monkeypatch.setattr(providers_mod, "load_projects", fake_load_projects)
    monkeypatch.setenv(project.token_env, "tok")
    ticket_tools._status_cache_clear()
    stub = _StubMCP()
    ticket_tools.register(stub)
    return stub.tools


def _json(payload, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )


def _issue(number: int = 7, body: str = "old", labels=None) -> dict:
    return {
        "number": number, "title": "t", "body": body, "state": "open",
        "user": {"login": "alice"}, "assignees": [],
        "labels": [{"name": n} for n in (labels if labels is not None else ["ai-generated"])],
        "html_url": f"https://github.com/acme/backend/issues/{number}",
        "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-02T00:00:00Z",
    }


def _comment(cid: int = 99, body: str = "c") -> dict:
    return {
        "id": cid, "body": body, "user": {"login": "alice"},
        "html_url": f"https://github.com/acme/backend/issues/7#issuecomment-{cid}",
        "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-02T00:00:00Z",
    }


_BUG_YML = (
    "name: Bug\ndescription: d\nbody:\n  - type: textarea\n    id: d\n"
    "    attributes:\n      label: Description\n    validations:\n      required: true\n"
)


class _Repo:
    """Recording GitHub mock. `push` / `put_status` steer the failure cases;
    `ticket_body` / `comment_body` feed the read tool."""

    def __init__(self, monkeypatch, *, push=True, put_status=201, ticket_body="old",
                 comment_body="c", templated=False):
        self.seen: list[httpx.Request] = []
        self.push = push
        self.put_status = put_status
        self.ticket_body = ticket_body
        self.comment_body = comment_body
        self.templated = templated
        transport = httpx.MockTransport(self._wrapped)

        def fake_client(token):
            return httpx.Client(
                base_url=github_provider.API_BASE, transport=transport,
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )

        monkeypatch.setattr(github_provider, "_client", fake_client)

    def _wrapped(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(req)
        return self.handle(req)

    def handle(self, req: httpx.Request) -> httpx.Response:
        p, m = req.url.path, req.method
        base = "/repos/acme/backend"
        if p == base and m == "GET":
            return _json({"permissions": {"push": self.push}, "default_branch": "main"})
        if p == f"{base}/git/ref/heads/ticket-attachments":
            return _json({"message": "Not Found"}, 404)
        if p == f"{base}/git/ref/heads/main":
            return _json({"object": {"sha": "abc"}})
        if p == f"{base}/git/refs" and m == "POST":
            return _json({"ref": "refs/heads/ticket-attachments"}, 201)
        if "/contents/attachments/" in p and m == "PUT":
            if self.put_status >= 400:
                return _json({"message": "Resource not accessible"}, self.put_status)
            rel = p.split("/contents/", 1)[1]
            return _json({"content": {"html_url": _BASE + rel}}, 201)
        if "/contents/.github/ISSUE_TEMPLATE" in p and m == "GET":
            if not self.templated:
                return _json({"message": "Not Found"}, 404)
            if p.endswith("ISSUE_TEMPLATE"):
                return _json([{"name": "bug.yml", "path": ".github/ISSUE_TEMPLATE/bug.yml", "type": "file"}])
            return _json({"content": base64.b64encode(_BUG_YML.encode()).decode(), "encoding": "base64"})
        if "/contents/" in p and m == "GET":
            return httpx.Response(200, content=_served_for(p))
        if p == f"{base}/labels" and m == "POST":
            return _json({"name": "x"}, 201)
        if p == f"{base}/issues" and m == "POST":
            return _json(_issue(7, json.loads(req.content)["body"]), 201)
        if p == f"{base}/issues/7" and m in ("GET", "PATCH"):
            body = self.ticket_body
            if m == "PATCH" and b'"body"' in req.content:
                body = json.loads(req.content)["body"]
            return _json(_issue(7, body))
        if p == f"{base}/issues/7/comments" and m == "POST":
            return _json(_comment(99, json.loads(req.content)["body"]), 201)
        if p == f"{base}/issues/comments/99" and m == "GET":
            return _json(_comment(99, self.comment_body))
        return _json({"message": "Not Found"}, 404)

    def calls(self, method: str, needle: str) -> list[httpx.Request]:
        return [r for r in self.seen if r.method == method and needle in r.url.path]

    def put_contents(self) -> list[httpx.Request]:
        return self.calls("PUT", "/contents/")

    def issue_posts(self) -> list[httpx.Request]:
        return [r for r in self.calls("POST", "/issues") if r.url.path.endswith("/issues")]

    def issue_or_comment_writes(self) -> list[httpx.Request]:
        out = []
        for r in self.seen:
            if r.method == "POST" and re.search(r"/issues(/\d+/comments)?$", r.url.path):
                out.append(r)
            if r.method == "PATCH" and "/issues/" in r.url.path:
                out.append(r)
        return out


def _png(tmp_path: Path, name: str = "shot.png") -> Path:
    f = tmp_path / name
    f.write_bytes(_PNG)
    return f


def _body_of(req: httpx.Request) -> str:
    return json.loads(req.content)["body"]


def _assert_marker_once(body: str, marker: str = "#ai-generated") -> None:
    assert body.split("\n", 1)[0].strip() == marker, body
    assert body.count(marker) == 1, body


# ---------- R1 create_ticket -------------------------------------------------


def test_create_ticket_uploads_and_rewrites_body(monkeypatch, tmp_path) -> None:
    repo = _Repo(monkeypatch)
    tools = _register(monkeypatch, _project())
    a, b = _png(tmp_path, "a.png"), _png(tmp_path, "b.png")
    result = tools["create_ticket"](
        project_id="acme", title="T",
        body=f"See ![first]({a}) here",
        attachments=[{"path": str(a)}, {"path": str(b), "alt": "second shot"}],
    )
    assert "error" not in result, result
    assert "ticket" in result
    posts = repo.issue_posts()
    assert len(posts) == 1
    body = _body_of(posts[0])
    _assert_marker_once(body)
    assert str(a) not in body
    assert f"![first]({_BASE}attachments/" in body and "a.png?raw=true" in body
    assert "![second shot](" in body and "b.png?raw=true" in body
    puts = repo.put_contents()
    assert len(puts) == 2
    assert all(json.loads(r.content)["branch"] == "ticket-attachments" for r in puts)


def test_create_ticket_without_refs_only_appends_files(monkeypatch, tmp_path) -> None:
    repo = _Repo(monkeypatch)
    tools = _register(monkeypatch, _project())
    a = _png(tmp_path, "a.png")
    result = tools["create_ticket"](
        project_id="acme", title="T", body="plain text", attachments=[{"path": str(a)}],
    )
    assert "error" not in result, result
    body = _body_of(repo.issue_posts()[0])
    _assert_marker_once(body)
    assert "plain text" in body
    assert re.search(
        r"!\[[^\]]*\]\(https://github\.com/acme/backend/blob/ticket-attachments/.*a\.png\?raw=true\)",
        body,
    )


# ---------- R2 update_ticket -------------------------------------------------


def test_update_ticket_with_body_attaches(monkeypatch, tmp_path) -> None:
    repo = _Repo(monkeypatch)
    tools = _register(monkeypatch, _project())
    a = _png(tmp_path, "a.png")
    result = tools["update_ticket"](
        project_id="acme", ticket_id="7", body=f"new ![x]({a})",
        attachments=[{"path": str(a)}],
    )
    assert "error" not in result, result
    patches = repo.calls("PATCH", "/issues/7")
    assert len(patches) == 1
    body = _body_of(patches[0])
    _assert_marker_once(body)
    assert str(a) not in body
    assert "ticket-attachments/attachments/" in body and "a.png?raw=true" in body
    assert len(repo.put_contents()) == 1


def test_update_ticket_attachments_without_body_refused(monkeypatch, tmp_path) -> None:
    repo = _Repo(monkeypatch)
    tools = _register(monkeypatch, _project())
    a = _png(tmp_path, "a.png")
    result = tools["update_ticket"](
        project_id="acme", ticket_id="7", labels_add=["bug"], attachments=[{"path": str(a)}],
    )
    assert "error" in result, result
    assert "body" in result["error"].lower()
    assert repo.seen == []


# ---------- R3 add_comment ---------------------------------------------------


def test_add_comment_with_attachments(monkeypatch, tmp_path) -> None:
    repo = _Repo(monkeypatch)
    tools = _register(monkeypatch, _project())
    a = _png(tmp_path, "a.png")
    result = tools["add_comment"](
        project_id="acme", ticket_id="7", body=f"look ![s]({a})",
        attachments=[{"path": str(a), "alt": "s"}],
    )
    assert "error" not in result, result
    posts = repo.calls("POST", "/comments")
    assert len(posts) == 1
    body = _body_of(posts[0])
    _assert_marker_once(body)
    assert str(a) not in body
    assert "![s](" + _BASE + "attachments/" in body
    assert len(repo.put_contents()) == 1


# ---------- R4 template refusal before upload --------------------------------


def test_template_refusal_uploads_nothing(monkeypatch, tmp_path) -> None:
    repo = _Repo(monkeypatch, templated=True)
    tools = _register(monkeypatch, _project())
    a = _png(tmp_path, "a.png")
    created = tools["create_ticket"](
        project_id="acme", title="T", body="### Wrong\nnope", attachments=[{"path": str(a)}],
    )
    assert created.get("written") is False, created
    updated = tools["update_ticket"](
        project_id="acme", ticket_id="7", body="### Wrong\nnope", template="Bug",
        attachments=[{"path": str(a)}],
    )
    assert updated.get("written") is False, updated
    assert repo.put_contents() == []
    assert repo.calls("POST", "/git/refs") == []
    assert repo.issue_or_comment_writes() == []


# ---------- R5 error cases ---------------------------------------------------


def _call_write(tools, which: str, attachments):
    if which == "create_ticket":
        return tools["create_ticket"](project_id="acme", title="T", body="b", attachments=attachments)
    if which == "update_ticket":
        # labels_add puts update_ticket in label mode (ensure_label write)
        return tools["update_ticket"](
            project_id="acme", ticket_id="7", body="b", labels_add=["bug"], attachments=attachments,
        )
    return tools["add_comment"](project_id="acme", ticket_id="7", body="b", attachments=attachments)


@pytest.mark.parametrize("which", ["create_ticket", "update_ticket", "add_comment"])
@pytest.mark.parametrize("case", ["missing", "bmp", "relative", "no_push", "put_403"])
def test_attachment_errors(monkeypatch, tmp_path, which, case) -> None:
    repo = _Repo(monkeypatch, push=(case != "no_push"), put_status=403 if case == "put_403" else 201)
    tools = _register(monkeypatch, _project())
    if case == "missing":
        path, needle = str(tmp_path / "ghost.png"), "ghost.png"
    elif case == "bmp":
        f = tmp_path / "pic.bmp"
        f.write_bytes(b"BM")
        path, needle = str(f), "pic.bmp"
    elif case == "relative":
        path, needle = "rel/shot.png", "shot.png"
    else:
        path, needle = str(_png(tmp_path, "shot.png")), "shot.png"
    result = _call_write(tools, which, [{"path": path}])
    assert isinstance(result, dict) and "error" in result, result
    assert needle in result["error"]
    if case == "missing":
        assert "not found" in result["error"].lower()  # the reason, not just the name
    if case == "bmp":
        assert ".png" in result["error"]  # the reason lists allowed types
    if case in ("no_push", "put_403"):
        assert "push" in result["error"].lower()
    if case == "relative":
        assert "absolute" in result["error"].lower()
    if which == "update_ticket" and case in ("missing", "bmp", "relative"):
        # local validation must precede the label-mode ensure_label write:
        # no request of any kind (no POST /labels, no pre-flight) was made
        assert repo.seen == [], [(r.method, r.url.path) for r in repo.seen]
    assert repo.issue_or_comment_writes() == []


@pytest.mark.parametrize("which", ["create_ticket", "update_ticket", "add_comment"])
def test_attachment_unsupported_provider_names_files(monkeypatch, tmp_path, which) -> None:
    def no_http(*_a, **_k):
        raise AssertionError("GitLab must not be contacted for attachments")

    monkeypatch.setattr(
        gitlab_provider.GitLabProvider, "list_issue_templates", lambda self, project, token: [],
    )
    monkeypatch.setattr(gitlab_provider, "_client", no_http)
    tools = _register(monkeypatch, _project("gitlab"))
    a = _png(tmp_path, "shot.png")
    result = _call_write(tools, which, [{"path": str(a)}])
    assert "error" in result, result
    assert "does not support image attachments" in result["error"]
    assert "files not attached: shot.png" in result["error"]


# ---------- R6 get_ticket_images ---------------------------------------------

_FOREIGN = "https://example.com/evil.png"
_OWN = _BASE + "attachments/u1/own.png?raw=true"
_OWN2 = _BASE + "attachments/u1/two.png?raw=true"


def _listing_body() -> str:
    return f'![shot]({_OWN}) and <img src="{_OWN2}" alt="second"> and ![x]({_FOREIGN})'


def test_get_ticket_images_list_and_save(monkeypatch, tmp_path) -> None:
    repo = _Repo(monkeypatch, ticket_body=_listing_body())
    tools = _register(monkeypatch, _project())
    listing = tools["get_ticket_images"](project_id="acme", ticket_id="7")
    assert "error" not in listing, listing
    imgs = listing["images"]
    assert [i["url"] for i in imgs] == [_OWN, _OWN2, _FOREIGN]
    assert [i["alt"] for i in imgs][:2] == ["shot", "second"]
    assert len({i["index"] for i in imgs}) == 3
    dest = tmp_path / "out.png"
    saved = tools["get_ticket_images"](
        project_id="acme", ticket_id="7", save_index=imgs[1]["index"], dest_path=str(dest),
    )
    assert "error" not in saved, saved
    expected = _served_for("two.png")
    assert expected != _served_for("own.png")
    assert dest.read_bytes() == expected  # the chosen index's bytes, not another image's
    gets = [r.url.path for r in repo.calls("GET", "/contents/")]
    assert len(gets) == 1 and gets[0].endswith("/attachments/u1/two.png"), gets
    assert saved["saved"]["url"] == _OWN2
    assert saved["saved"]["bytes"] == len(expected)


def test_get_ticket_images_comment(monkeypatch) -> None:
    repo = _Repo(monkeypatch, ticket_body="no images", comment_body=f"c ![k]({_OWN})")
    tools = _register(monkeypatch, _project())
    on_ticket = tools["get_ticket_images"](project_id="acme", ticket_id="7")
    assert on_ticket["images"] == []
    result = tools["get_ticket_images"](project_id="acme", ticket_id="7", comment_id="99")
    assert "error" not in result, result
    assert repo.calls("GET", "/issues/comments/99"), "comment was never fetched"
    assert [i["url"] for i in result["images"]] == [_OWN]


def test_get_ticket_images_refusals(monkeypatch, tmp_path) -> None:
    repo = _Repo(monkeypatch, ticket_body=_listing_body())
    tools = _register(monkeypatch, _project())
    imgs = tools["get_ticket_images"](project_id="acme", ticket_id="7")["images"]
    own_idx = imgs[0]["index"]
    foreign_idx = imgs[2]["index"]
    dest = str(tmp_path / "o.png")

    def call(**kw):
        return tools["get_ticket_images"](project_id="acme", ticket_id="7", **kw)

    rel = call(save_index=own_idx, dest_path="rel/o.png")
    assert "error" in rel, rel
    # refused for being relative, not incidentally for a missing parent directory
    assert "absolute" in rel["error"].lower(), rel
    assert repo.calls("GET", "/contents/") == []  # nothing downloaded
    assert not (Path.cwd() / "rel" / "o.png").exists()

    cases = {
        "foreign": call(save_index=foreign_idx, dest_path=dest),
        "bad_index": call(save_index=99, dest_path=dest),
        "only_index": call(save_index=own_idx),
        "only_dest": call(dest_path=dest),
        "missing_parent": call(save_index=own_idx, dest_path=str(tmp_path / "nope" / "o.png")),
    }
    for name, res in cases.items():
        assert isinstance(res, dict) and "error" in res, (name, res)
    assert not Path(dest).exists()
    assert "nope" in cases["missing_parent"]["error"]


def test_get_ticket_images_unsupported_provider(monkeypatch) -> None:
    def no_http(*_a, **_k):
        raise AssertionError("no HTTP expected")

    monkeypatch.setattr(gitlab_provider, "_client", no_http)
    tools = _register(monkeypatch, _project("gitlab"))
    result = tools["get_ticket_images"](project_id="acme", ticket_id="7")
    assert "error" in result
    assert "does not support image attachments" in result["error"]


# ---------- R7 docs ----------------------------------------------------------


def _attachments_doc(fn) -> str:
    hints = typing.get_type_hints(fn, include_extras=True)
    ann = hints["attachments"]
    descs = [getattr(m, "description", None) for m in getattr(ann, "__metadata__", ())]
    return "\n".join([fn.__doc__ or "", *[d for d in descs if d]])


_NEGATION = re.compile(r"(never|not|no|without|nor|n't)")


def _sentences(text: str) -> list[str]:
    return [x.lower() for x in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if x]


@pytest.mark.parametrize("name", ["create_ticket", "update_ticket", "add_comment"])
def test_attachment_docs(name) -> None:
    stub = _StubMCP()
    ticket_tools.register(stub)
    doc = _attachments_doc(stub.tools[name])
    low = doc.lower()
    for ext in attachments_lib._IMAGE_TYPES:
        assert ext.lstrip(".") in low, ext
    sents = _sentences(doc)
    assert "no per-call limit" in low
    # storage: Contents API and the ticket-attachments branch in one sentence
    storage = [x for x in sents if "contents api" in x and "ticket-attachments" in x and "branch" in x]
    assert storage, sents
    assert all(not _NEGATION.search(x) for x in storage), storage  # an affirmative claim
    # per-provider support: GitHub only, GitLab / Azure DevOps named as unsupported
    assert any(
        "github" in x and "gitlab" in x and "azure devops" in x
        and ("not" in x or "only" in x or "unsupported" in x)
        for x in sents
    ), sents


def test_get_ticket_images_docstring_names_storage() -> None:
    stub = _StubMCP()
    ticket_tools.register(stub)
    sents = _sentences(stub.tools["get_ticket_images"].__doc__ or "")
    storage = [x for x in sents if "ticket-attachments" in x and "branch" in x]
    assert storage, sents
    assert all(not _NEGATION.search(x) for x in storage), storage  # an affirmative claim
