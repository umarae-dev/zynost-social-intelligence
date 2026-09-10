"""Secrets hygiene scan (docs/17 TST-SEC, CON-03, SEC-04, DOD-13)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_LITERAL = "test-not-a-secret"

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
    }
)
_SKIP_SUFFIXES = frozenset(
    {".pyc", ".pyo", ".so", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
)
_BEARER_PLACEHOLDERS = frozenset(
    {
        "tokens",
        "token",
        "<token>",
        "…",
        "...",
        "changeme",
        "your_token",
        "your-token",
    }
)

_BEARER_RE = re.compile(r"Bearer\s+(\S+)", re.IGNORECASE)
_SK_LIVE_RE = re.compile(r"sk-(?:ant-|proj-)?[A-Za-z0-9]{16,}")
_REDIS_PASSWORD_RE = re.compile(
    r"rediss?://[^:@\s/]*:[^@\s/]+@",
    re.IGNORECASE,
)


def _iter_scan_files(root: Path) -> Iterator[Path]:
    roots = [
        root / "zynost_social",
        root / "tests",
        root / "examples",
        *root.glob("README*"),
        root / "docs" / "README.md",
    ]
    seen: set[Path] = set()
    for base in roots:
        if not base.exists():
            continue
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        for path in files:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if any(part in _SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
                continue
            if path.suffix.lower() in _SKIP_SUFFIXES:
                continue
            try:
                path.relative_to(root.resolve())
            except ValueError:
                continue
            yield path


def _bearer_is_live(token: str) -> bool:
    cleaned = token.strip().strip("'\"`,")
    if not cleaned:
        return False
    if FAKE_LITERAL in cleaned:
        return False
    if cleaned.startswith("{") or cleaned.startswith("$") or "{" in cleaned:
        return False
    lowered = cleaned.lower().rstrip(".,;:)")
    if lowered in _BEARER_PLACEHOLDERS:
        return False
    return len(lowered) >= 12


def secret_violations(root: Path) -> list[str]:
    hits: list[str] = []
    for path in _iter_scan_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        for match in _BEARER_RE.finditer(text):
            if _bearer_is_live(match.group(1)):
                hits.append(f"{rel}: live-looking Bearer token")
        for match in _SK_LIVE_RE.finditer(text):
            if FAKE_LITERAL not in match.group(0):
                hits.append(f"{rel}: live-looking sk- key")
        for _match in _REDIS_PASSWORD_RE.finditer(text):
            hits.append(f"{rel}: redis URL with password")
    return hits


def test_repo_has_no_live_looking_secrets() -> None:
    assert secret_violations(REPO_ROOT) == []


def test_scan_allows_fake_bearer_and_header_templates(tmp_path: Path) -> None:
    pkg = tmp_path / "zynost_social"
    pkg.mkdir()
    (pkg / "x.py").write_text(
        'headers = {"Authorization": f"Bearer {bearer.strip()}"}\n',
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text(
        f'TOKEN = "{FAKE_LITERAL}"\nassert "Authorization" == "Bearer {FAKE_LITERAL}"\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("REDIS_URL is empty in .env.example.\n", encoding="utf-8")
    assert secret_violations(tmp_path) == []


def test_scan_flags_live_bearer_sk_and_redis_password(tmp_path: Path) -> None:
    pkg = tmp_path / "zynost_social"
    pkg.mkdir()
    bearer = "Bearer " + ("A" * 16)
    sk_key = "sk-" + ("abcd" * 6)
    redis_url = "redis://:" + "not-a-prod-pass" + "@localhost:6379/0"
    (pkg / "leak.py").write_text(
        f"token = '{bearer}'\nkey = '{sk_key}'\nurl = '{redis_url}'\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    hits = secret_violations(tmp_path)
    kinds = " ".join(hits)
    assert "Bearer" in kinds
    assert "sk-" in kinds
    assert "redis URL" in kinds


def test_scan_allows_redis_scheme_without_password(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "zynost_social").mkdir()
    (tests / "test_cache.py").write_text(
        'assert "redis://" not in message\n',
        encoding="utf-8",
    )
    assert secret_violations(tmp_path) == []
