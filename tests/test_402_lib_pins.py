"""Regression test for work package #402: bump `lib-python-projects`
v0.4.0 -> v0.4.1 (`lib-python-config` stays at v0.1.4, guarded by
`test_366_lib_pins.py::test_installed_projects_requires_pinned_config_tag`).

Floor-based convention like #385/#398: the declared tag must be an exact
`vX.Y.Z` tag >= this ticket's floor, and the installed version must be too.
Pin helpers are copied from `tests/test_385_lib_pins.py` so earlier floor
files stay untouched.

R1 (#402) driving-test: `test_projects_pin_meets_402_floor`.
R2 (pin comment) existing-suite: `test_319_lib_pins.py`.
R3 (config pin consistent) existing-suite: `test_366_lib_pins.py`.
"""
from __future__ import annotations

import importlib.metadata
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

_PROJECTS_FLOOR = Version("0.4.1")
_EXACT_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _pyproject_text() -> str:
    return (_repo_root() / "pyproject.toml").read_text(encoding="utf-8")


def _dependencies() -> list[str]:
    return tomllib.loads(_pyproject_text())["project"]["dependencies"]


def _entry(name: str) -> str:
    for entry in _dependencies():
        if Requirement(entry).name == name:
            return entry
    raise AssertionError(f"no {name!r} entry found in project.dependencies")


def _tag_from_url(entry: str) -> str:
    url = Requirement(entry).url or ""
    assert url, f"{entry!r} has no direct-reference URL to pull a tag from"
    return url.rsplit("@", 1)[-1].strip()


def _declared_tag(name: str) -> str:
    return _tag_from_url(_entry(name))


def _exact_version(name: str) -> Version:
    tag = _declared_tag(name)
    assert _EXACT_TAG_RE.match(tag), (
        f"{name} must be pinned to an exact vX.Y.Z tag, found ref {tag!r}"
    )
    return Version(tag.lstrip("v"))


def _installed_version(name: str) -> Version:
    """Wires the floor test directly to the installed environment's actual
    version (not just the declared pyproject.toml text), so a declared-but-
    not-installed bump is caught by the floor test itself -- see
    `test_366_lib_pins.py::_installed_version`'s docstring for the
    originating test-critic finding this mirrors."""
    try:
        return Version(importlib.metadata.version(name))
    except importlib.metadata.PackageNotFoundError:
        raise AssertionError(
            f"{name} is not installed; run `pwsh scripts/test.ps1` "
            f"(or scripts/sync-libs.ps1) to install the pinned tag"
        )


def test_projects_pin_meets_402_floor() -> None:
    """Driving test (R1). RED today: pyproject.toml declares
    lib-python-projects @v0.4.0, which is below this ticket's v0.4.1
    floor, and the installed version is 0.4.0 too."""
    assert _exact_version("lib-python-projects") >= _PROJECTS_FLOOR
    assert _installed_version("lib-python-projects") >= _PROJECTS_FLOOR
