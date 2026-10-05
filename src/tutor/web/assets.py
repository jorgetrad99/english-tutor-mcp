"""Content-hashed static URLs, computed once at startup (no build step)."""

from __future__ import annotations

import hashlib
from pathlib import Path


class AssetManifest:
    def __init__(self, static_dir: Path) -> None:
        self._hashes: dict[str, str] = {}
        combined = hashlib.sha256()
        for path in sorted(p for p in static_dir.rglob("*") if p.is_file()):
            rel = path.relative_to(static_dir).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self._hashes[rel] = digest[:10]
            combined.update(f"{rel}:{digest}".encode())
        self.version = combined.hexdigest()[:12]

    def url(self, rel: str) -> str:
        """`/static/<rel>?v=<hash>`; raises KeyError for a missing file so typos fail tests."""
        return f"/static/{rel}?v={self._hashes[rel]}"

    def files(self) -> tuple[str, ...]:
        return tuple(self._hashes)
