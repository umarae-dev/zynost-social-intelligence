"""LLM-free repository scan (docs/17 TST-LLM, CON-01, DOD-04)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Split so this file is not an import of those packages (TST-LLM scans tests for imports).
_LLM_PACKAGES = (
    "anthropic",
    "open" + "ai",
    "google.generativeai",
    "google.genai",
    "vertexai",
)
_LLM_REQUIREMENTS = (
    *_LLM_PACKAGES,
    "google-generativeai",
    "google-genai",
)

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".egg-info",
    }
)

_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(" + "|".join(re.escape(name) for name in _LLM_PACKAGES) + r")\b",
    re.MULTILINE | re.IGNORECASE,
)
_DYNAMIC_IMPORT_RE = re.compile(
    r"""(?:import_module|__import__)\(\s*['"]("""
    + "|".join(re.escape(name) for name in _LLM_PACKAGES)
    + r""")['"]""",
    re.IGNORECASE,
)
_REQUIREMENT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])("
    + "|".join(re.escape(name) for name in _LLM_REQUIREMENTS)
    + r")(?![A-Za-z0-9._-])",
    re.IGNORECASE,
)


def _iter_files(root: Path, relative: str, *, suffix: str | None = None) -> Iterator[Path]:
    base = root / relative
    if base.is_file():
        if suffix is None or base.suffix == suffix or base.name.endswith(suffix or ""):
            yield base
        return
    if not base.is_dir():
        return
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
            continue
        if suffix is None or path.suffix == suffix or path.name.endswith(suffix):
            yield path


def llm_violations(root: Path) -> list[str]:
    """Imports in package/tests; tokens in manifests. Docs are not scanned (TST-LLM)."""
    hits: list[str] = []
    for path in (
        *_iter_files(root, "zynost_social", suffix=".py"),
        *_iter_files(root, "tests", suffix=".py"),
    ):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        for match in _IMPORT_RE.finditer(text):
            hits.append(f"{rel}: import {match.group(1)}")
        for match in _DYNAMIC_IMPORT_RE.finditer(text):
            hits.append(f"{rel}: dynamic import {match.group(1)}")
        if path.parts[-2] == "zynost_social" or "zynost_social" in path.parts:
            for match in _REQUIREMENT_RE.finditer(text):
                hits.append(f"{rel}: token {match.group(1)}")

    manifests = [root / "pyproject.toml"]
    manifests.extend(root.glob("requirements*"))
    manifests.extend(root.glob("*lock*"))
    manifests.extend(root.glob("Pipfile*"))
    for path in manifests:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        for match in _REQUIREMENT_RE.finditer(text):
            hits.append(f"{rel}: dependency {match.group(1)}")
    return hits


def test_package_and_manifests_have_no_llm_sdks() -> None:
    assert llm_violations(REPO_ROOT) == []


def test_scan_flags_openai_import(tmp_path: Path) -> None:
    pkg = tmp_path / "zynost_social"
    pkg.mkdir()
    (pkg / "bad.py").write_text("import openai\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    hits = llm_violations(tmp_path)
    assert any("openai" in hit for hit in hits)


def test_scan_flags_manifest_dependency(tmp_path: Path) -> None:
    (tmp_path / "zynost_social").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["anthropic>=0.1"]\n',
        encoding="utf-8",
    )
    hits = llm_violations(tmp_path)
    assert any("anthropic" in hit for hit in hits)


def test_scan_ignores_docs_mentions(tmp_path: Path) -> None:
    (tmp_path / "zynost_social").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("Do not import openai or anthropic.\n", encoding="utf-8")
    assert llm_violations(tmp_path) == []
