"""Tree-sitter parser wrapper for TypeScript / TSX.

Caches parsed trees per file path (keyed on mtime) so the rest of the
pipeline (symbol extraction, reference finding) can re-query the tree
without re-parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser, Tree


TS_LANGUAGE = Language(tsts.language_typescript())
TSX_LANGUAGE = Language(tsts.language_tsx())


def _language_for(path: Path) -> Language:
    return TSX_LANGUAGE if path.suffix == ".tsx" else TS_LANGUAGE


@lru_cache(maxsize=8)
def _parser_for(suffix: str) -> Parser:
    lang = TSX_LANGUAGE if suffix == ".tsx" else TS_LANGUAGE
    return Parser(lang)


@dataclass(frozen=True)
class ParsedFile:
    path: Path
    source: bytes
    tree: Tree
    language: Language

    def text(self, node) -> str:
        return self.source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    @property
    def line_count(self) -> int:
        return self.source.count(b"\n") + 1


_CACHE: dict[Path, tuple[float, ParsedFile]] = {}


def parse_file(path: Path) -> Optional[ParsedFile]:
    """Parse a TS/TSX file, returning None for unsupported suffixes."""
    if path.suffix not in (".ts", ".tsx"):
        return None
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return None

    cached = _CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]

    source = path.read_bytes()
    parser = _parser_for(path.suffix)
    tree = parser.parse(source)
    pf = ParsedFile(path=path, source=source, tree=tree, language=_language_for(path))
    _CACHE[path] = (mtime, pf)
    return pf


def clear_cache() -> None:
    _CACHE.clear()
