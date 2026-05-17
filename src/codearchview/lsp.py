"""Thin wrapper around multilspy's TypeScript language server.

Provides a context-managed session and two operations the traverser
needs:

    * `definition(file, line, col)` -> list of (target_path, target_line, target_col)
    * `resolve_import(file, source_text)` -> target_path | None

multilspy speaks the standard LSP protocol; `request_definition` returns
either a Location or a list of LocationLinks depending on the server.
We normalize both shapes.

If multilspy fails to start (no Node, no typescript-language-server),
construction raises `LspUnavailable` so the CLI can exit cleanly with a
distinct exit code.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import unquote, urlparse


class LspUnavailable(RuntimeError):
    """Raised when we cannot start the TypeScript language server."""


@dataclass(frozen=True)
class LspLocation:
    path: Path
    line: int  # 0-indexed
    column: int  # 0-indexed


def _uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        # Some servers return untitled:// or jdt:// — treat as unresolved.
        raise ValueError(f"non-file URI: {uri}")
    return Path(unquote(parsed.path))


def _normalize_definition(result) -> list[LspLocation]:
    if result is None:
        return []
    items = result if isinstance(result, list) else [result]
    out: list[LspLocation] = []
    for item in items:
        # LocationLink has targetUri/targetSelectionRange; Location has uri/range.
        uri = item.get("targetUri") or item.get("uri")
        rng = item.get("targetSelectionRange") or item.get("targetRange") or item.get("range")
        if not uri or not rng:
            continue
        try:
            path = _uri_to_path(uri)
        except ValueError:
            continue
        start = rng.get("start", {})
        out.append(
            LspLocation(
                path=path,
                line=int(start.get("line", 0)),
                column=int(start.get("character", 0)),
            )
        )
    return out


class TypeScriptLsp:
    """Synchronous wrapper around multilspy's TypeScript server."""

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self._server = None
        self._ctx = None
        try:
            # Imported lazily so import errors surface as LspUnavailable.
            from multilspy import SyncLanguageServer
            from multilspy.multilspy_config import MultilspyConfig
            from multilspy.multilspy_logger import MultilspyLogger
        except Exception as exc:  # pragma: no cover
            raise LspUnavailable(f"multilspy import failed: {exc}") from exc

        config = MultilspyConfig.from_dict({"code_language": "typescript"})
        logger = MultilspyLogger()
        # Quiet down multilspy's own logger unless CAV_DEBUG is set.
        if not os.environ.get("CAV_DEBUG"):
            logging.getLogger("multilspy").setLevel(logging.WARNING)

        try:
            self._server = SyncLanguageServer.create(config, logger, str(self.project_root))
        except Exception as exc:
            raise LspUnavailable(
                "Failed to construct typescript-language-server. "
                "Ensure Node and `typescript-language-server` are installed."
            ) from exc

    # ---- context management ------------------------------------------------

    def __enter__(self) -> "TypeScriptLsp":
        try:
            self._ctx = self._server.start_server()
            self._ctx.__enter__()
        except Exception as exc:
            raise LspUnavailable(f"Failed to start language server: {exc}") from exc
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._ctx is not None:
            try:
                self._ctx.__exit__(exc_type, exc, tb)
            except Exception as cleanup_exc:
                # multilspy's TS server teardown sometimes races on macOS:
                # the Node process exits cleanly on its own, then psutil
                # raises NoSuchProcess when multilspy tries to kill it.
                # Swallow teardown noise unless we were already propagating
                # a real exception (in which case the real exception wins).
                if exc is None:
                    logging.debug("LSP cleanup raised %s (ignored)", cleanup_exc)
                else:
                    logging.debug("LSP cleanup also raised %s", cleanup_exc)
            finally:
                self._ctx = None

    # ---- queries -----------------------------------------------------------

    def _relpath(self, abs_path: Path) -> str:
        try:
            return str(abs_path.resolve().relative_to(self.project_root))
        except ValueError:
            return str(abs_path)

    def definition(self, file: Path, line: int, column: int) -> list[LspLocation]:
        """Resolve `textDocument/definition` at a 0-indexed position."""
        rel = self._relpath(file)
        try:
            result = self._server.request_definition(rel, line, column)
        except Exception:
            return []
        # multilspy normalizes to a list of {"absolutePath", "range", ...}
        # We re-shape to LspLocation.
        out: list[LspLocation] = []
        for item in result or []:
            abs_path = item.get("absolutePath") or item.get("uri")
            if not abs_path:
                continue
            if str(abs_path).startswith("file://"):
                try:
                    abs_path = _uri_to_path(abs_path)
                except ValueError:
                    continue
            else:
                abs_path = Path(abs_path)
            rng = item.get("range") or {}
            start = rng.get("start", {})
            out.append(
                LspLocation(
                    path=abs_path,
                    line=int(start.get("line", 0)),
                    column=int(start.get("character", 0)),
                )
            )
        return out


@contextmanager
def open_lsp(project_root: Path) -> Iterator[Optional[TypeScriptLsp]]:
    """Yield a started LSP or None if it cannot start.

    The traverser uses this so that — if the user wants to run without an
    LSP — we degrade to a pure-tree-sitter mode (imports only).
    """
    try:
        lsp = TypeScriptLsp(project_root)
    except LspUnavailable as exc:
        logging.warning("LSP unavailable: %s", exc)
        yield None
        return

    with lsp:
        yield lsp
