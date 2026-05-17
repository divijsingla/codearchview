"""DFS traversal of a TS/TSX project from a single entry file.

For each file we:
    1. Extract imports (tree-sitter) and resolve each to either an
       internal file (queue for recursion) or an external boundary
       (terminate).
    2. Extract top-level symbols (tree-sitter) and, for each symbol,
       enumerate its identifier references and resolve them via the
       LSP.  Internal references add intra-file or inter-file edges;
       external references add edges to the external boundary node.

The traversal terminates when the visited-files queue drains.  All
state (nodes, edges) is held in the `GraphBuilder` we receive.
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Optional

from .builder import GraphBuilder
from .classify import classify_resolved_path, classify_specifier
from .lsp import LspLocation, TypeScriptLsp
from .parser import parse_file
from .paths_resolver import PathsResolver
from .queries import imports_query, run_matches
from .refs import extract_references
from .symbols import extract_symbols, iter_default_export


log = logging.getLogger(__name__)


class Traverser:
    def __init__(
        self,
        project_root: Path,
        builder: GraphBuilder,
        lsp: Optional[TypeScriptLsp],
        paths_resolver: Optional[PathsResolver] = None,
        follow_types: bool = True,
    ) -> None:
        self.project_root = project_root.resolve()
        self.builder = builder
        self.lsp = lsp
        self.paths_resolver = paths_resolver
        self.follow_types = follow_types
        self._visited: set[Path] = set()

    # ---- public ------------------------------------------------------------

    def traverse(self, entry: Path) -> None:
        queue: deque[Path] = deque([entry.resolve()])
        while queue:
            f = queue.popleft()
            if f in self._visited or not f.exists():
                continue
            self._visited.add(f)
            self._visit_file(f, queue)

    # ---- file-level --------------------------------------------------------

    def _visit_file(self, path: Path, queue: deque[Path]) -> None:
        pf = parse_file(path)
        if pf is None:
            return
        rel = self._rel(path)
        self.builder.add_file(rel)

        # Imports (file-level edges)
        self._process_imports(pf, rel, queue)

        # Symbols (and per-symbol references)
        symbols = list(extract_symbols(pf))
        symbols.extend(iter_default_export(pf))
        for sym in symbols:
            self.builder.add_symbol(
                file_rel=rel,
                name=sym.name,
                kind=sym.kind,
                start_line=sym.start_line,
                end_line=sym.end_line,
                exported=sym.exported,
            )
            self._process_symbol_refs(pf, rel, sym, queue)

    # ---- imports -----------------------------------------------------------

    def _process_imports(self, pf, file_rel: str, queue: deque[Path]) -> None:
        q = imports_query("tsx" if pf.path.suffix == ".tsx" else "ts")
        matches = run_matches(q, pf.tree.root_node)
        for _, captures in matches:
            source_nodes = captures.get("source") or []
            if not source_nodes:
                continue
            source_text = pf.text(source_nodes[0])

            ext = classify_specifier(source_text)
            if ext is not None:
                self.builder.add_external(ext.name, ext.source)
                self.builder.add_edge_file_external(file_rel, ext.name, kind="imports")
                continue

            # Need to resolve — try LSP first, then fall back to filesystem.
            target = self._resolve_import_source(pf.path, source_nodes[0])
            if target is None:
                # Unresolved bare specifier -> treat as external
                ext_name = source_text
                self.builder.add_external(ext_name, "unresolved")
                self.builder.add_edge_file_external(file_rel, ext_name, kind="imports")
                continue

            ext_from_path = classify_resolved_path(target)
            if ext_from_path is not None:
                self.builder.add_external(ext_from_path.name, ext_from_path.source)
                self.builder.add_edge_file_external(file_rel, ext_from_path.name, kind="imports")
                continue

            # Internal — enqueue and add file->file edge
            target_rel = self._rel(target)
            self.builder.add_file(target_rel)
            self.builder.add_edge_file_file(file_rel, target_rel, kind="imports")
            queue.append(target.resolve())

    def _resolve_import_source(self, from_file: Path, source_node) -> Optional[Path]:
        """Resolve an import-source string node to a filesystem path."""
        spec = self._read_string_text(source_node) or ""

        # tsconfig path-aliases + relative + baseUrl (handles `@/foo`).
        if self.paths_resolver is not None and spec:
            resolved = self.paths_resolver.resolve(spec, from_file)
            if resolved is not None:
                return resolved

        # Try the LSP next: ask for the definition of the source string
        # (most TS servers respond with the resolved file).
        if self.lsp is not None:
            line, col = source_node.start_point[0], source_node.start_point[1] + 1
            try:
                locs = self.lsp.definition(from_file, line, col)
            except Exception:  # pragma: no cover
                locs = []
            for loc in locs:
                if loc.path.exists():
                    return loc.path

        # Last-ditch: plain relative resolution.
        if spec.startswith(".") or spec.startswith("/"):
            base = (from_file.parent / spec).resolve()
            return _resolve_with_extensions(base)
        return None

    @staticmethod
    def _read_string_text(node) -> Optional[str]:
        # The capture is a string_fragment node; its text is the literal value.
        try:
            return node.text.decode("utf-8")  # type: ignore[attr-defined]
        except Exception:
            return None

    # ---- symbol references -------------------------------------------------

    def _process_symbol_refs(self, pf, file_rel: str, sym, queue: deque[Path]) -> None:
        if self.lsp is None:
            # Without LSP we can't reliably resolve cross-file identifier
            # references, so we skip symbol-level edges.  File-level
            # import edges still capture the high-level architecture.
            return

        refs = extract_references(pf, sym)
        for ref in refs:
            if ref.kind == "uses-type" and not self.follow_types:
                continue
            try:
                locs = self.lsp.definition(pf.path, ref.line, ref.column)
            except Exception:
                continue
            target = _first_internal_definition(locs, self.project_root)
            if target is None:
                # Either external or unresolved — best effort: skip
                # (file-level imports already capture externals).
                continue

            ext = classify_resolved_path(target.path)
            if ext is not None:
                self.builder.add_external(ext.name, ext.source)
                self.builder.add_edge_symbol_external(
                    file_rel=file_rel,
                    sym_name=sym.name,
                    external_name=ext.name,
                    kind=ref.kind,
                )
                continue

            target_rel = self._rel(target.path)
            # Locate the target symbol by name (best-effort).  We don't
            # re-parse — we look up in the builder's known symbols.
            target_sym = self.builder.find_symbol_at(target_rel, target.line + 1)
            if target_sym is None:
                # The target file may not be visited yet; record an
                # unresolved-by-position edge to the file as a fallback.
                self.builder.add_file(target_rel)
                self.builder.add_edge_symbol_file(
                    file_rel=file_rel, sym_name=sym.name,
                    target_file=target_rel, kind=ref.kind,
                )
                queue.append(target.path.resolve())
                continue

            self.builder.add_edge_symbol_symbol(
                src_file=file_rel, src_name=sym.name,
                dst_file=target_rel, dst_name=target_sym,
                kind=ref.kind,
            )
            queue.append(target.path.resolve())

    # ---- helpers -----------------------------------------------------------

    def _rel(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.project_root))
        except ValueError:
            return str(path.resolve())


# ---- module-level helpers ---------------------------------------------------

_CANDIDATE_SUFFIXES = (
    ".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs",
)
_INDEX_NAMES = tuple(f"index{s}" for s in _CANDIDATE_SUFFIXES)


def _resolve_with_extensions(base: Path) -> Optional[Path]:
    if base.is_file():
        return base
    for s in _CANDIDATE_SUFFIXES:
        cand = base.with_suffix(s) if base.suffix else Path(str(base) + s)
        if cand.is_file():
            return cand
    if base.is_dir():
        for name in _INDEX_NAMES:
            cand = base / name
            if cand.is_file():
                return cand
    return None


def _first_internal_definition(
    locs: list[LspLocation], project_root: Path
) -> Optional[LspLocation]:
    for loc in locs:
        try:
            loc.path.resolve().relative_to(project_root)
        except ValueError:
            continue
        return loc
    # If none are inside project_root but some are still meaningful
    # (e.g. node_modules), return the first so the caller can classify.
    return locs[0] if locs else None
