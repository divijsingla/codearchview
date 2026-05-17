"""Graph builder — accumulates nodes and edges and serializes to JSON.

Node ID conventions:
    file       -> f"file::{relative_posix_path}"
    symbol     -> f"sym::{relative_posix_path}::{symbol_name}"
    external   -> f"ext::{package_name}"

Edges are deduplicated by (source, target, kind).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class _Node:
    id: str
    kind: str
    name: str
    # Optional / kind-specific fields
    path: Optional[str] = None
    file: Optional[str] = None
    loc: Optional[dict] = None
    exported: Optional[bool] = None
    source: Optional[str] = None  # external source classification

    def to_dict(self) -> dict:
        d = {"id": self.id, "kind": self.kind, "name": self.name}
        if self.path is not None:
            d["path"] = self.path
        if self.file is not None:
            d["file"] = self.file
        if self.loc is not None:
            d["loc"] = self.loc
        if self.exported is not None:
            d["exported"] = self.exported
        if self.source is not None:
            d["source"] = self.source
        return d


@dataclass
class _Edge:
    source: str
    target: str
    kind: str

    def to_dict(self) -> dict:
        return asdict(self)


def _to_posix(p: str) -> str:
    return p.replace("\\", "/")


def _file_id(rel: str) -> str:
    return f"file::{_to_posix(rel)}"


def _sym_id(rel: str, name: str) -> str:
    return f"sym::{_to_posix(rel)}::{name}"


def _ext_id(name: str) -> str:
    return f"ext::{name}"


class GraphBuilder:
    def __init__(self, project_root: Path, entry: str) -> None:
        self.project_root = project_root.resolve()
        self.entry = entry
        self._nodes: dict[str, _Node] = {}
        self._edges: dict[tuple[str, str, str], _Edge] = {}
        # rel_path -> { (start_line, end_line): symbol_name }
        self._file_symbols: dict[str, list[tuple[int, int, str]]] = {}

    # ---- node helpers ------------------------------------------------------

    def add_file(self, rel: str) -> str:
        nid = _file_id(rel)
        if nid not in self._nodes:
            name = Path(rel).name
            self._nodes[nid] = _Node(id=nid, kind="file", name=name, path=_to_posix(rel))
        return nid

    def add_symbol(
        self,
        file_rel: str,
        name: str,
        kind: str,
        start_line: int,
        end_line: int,
        exported: bool,
    ) -> str:
        nid = _sym_id(file_rel, name)
        if nid not in self._nodes:
            self._nodes[nid] = _Node(
                id=nid,
                kind=kind,
                name=name,
                file=_to_posix(file_rel),
                loc={"startLine": start_line, "endLine": end_line},
                exported=exported,
            )
        self._file_symbols.setdefault(file_rel, []).append((start_line, end_line, name))
        # `contains` edge from file -> symbol
        self.add_edge(_file_id(file_rel), nid, "contains")
        return nid

    def add_external(self, name: str, source: str) -> str:
        nid = _ext_id(name)
        if nid not in self._nodes:
            self._nodes[nid] = _Node(id=nid, kind="external", name=name, source=source)
        return nid

    # ---- edge helpers ------------------------------------------------------

    def add_edge(self, src: str, dst: str, kind: str) -> None:
        if src == dst and kind == "contains":
            return
        key = (src, dst, kind)
        if key not in self._edges:
            self._edges[key] = _Edge(source=src, target=dst, kind=kind)

    def add_edge_file_file(self, src: str, dst: str, kind: str) -> None:
        self.add_edge(_file_id(src), _file_id(dst), kind)

    def add_edge_file_external(self, src: str, ext_name: str, kind: str) -> None:
        self.add_edge(_file_id(src), _ext_id(ext_name), kind)

    def add_edge_symbol_external(
        self, file_rel: str, sym_name: str, external_name: str, kind: str
    ) -> None:
        self.add_edge(_sym_id(file_rel, sym_name), _ext_id(external_name), kind)

    def add_edge_symbol_file(
        self, file_rel: str, sym_name: str, target_file: str, kind: str
    ) -> None:
        self.add_edge(_sym_id(file_rel, sym_name), _file_id(target_file), kind)

    def add_edge_symbol_symbol(
        self, src_file: str, src_name: str, dst_file: str, dst_name: str, kind: str,
    ) -> None:
        self.add_edge(_sym_id(src_file, src_name), _sym_id(dst_file, dst_name), kind)

    # ---- queries -----------------------------------------------------------

    def find_symbol_at(self, file_rel: str, line_1based: int) -> Optional[str]:
        """Return the symbol name whose body contains the given 1-indexed line."""
        candidates = self._file_symbols.get(file_rel, [])
        # Prefer the smallest (innermost) enclosing range.
        best: Optional[tuple[int, str]] = None
        for start, end, name in candidates:
            if start <= line_1based <= end:
                span = end - start
                if best is None or span < best[0]:
                    best = (span, name)
        return best[1] if best else None

    # ---- serialization -----------------------------------------------------

    def to_dict(self, tool_version: str) -> dict:
        commit = _git_commit_short(self.project_root)
        return {
            "meta": {
                "entry": self.entry,
                "commit": commit,
                "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "tool": "codearchview",
                "toolVersion": tool_version,
            },
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges.values()],
        }

    # ---- write-if-changed --------------------------------------------------

    @staticmethod
    def _hash_graph(nodes: list[dict], edges: list[dict]) -> str:
        """Order-independent hash of (nodes, edges).

        The traverser may legitimately enumerate nodes/edges in a
        slightly different order across runs (LSP race conditions,
        etc.) even when the set of nodes/edges is identical, so we
        sort both lists before hashing.
        """
        sorted_nodes = sorted(nodes, key=lambda n: n.get("id", ""))
        sorted_edges = sorted(
            edges,
            key=lambda e: (e.get("source", ""), e.get("target", ""), e.get("kind", "")),
        )
        body = json.dumps(
            {"nodes": sorted_nodes, "edges": sorted_edges},
            sort_keys=True,
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def _structural_hash(self) -> str:
        return self._hash_graph(
            [n.to_dict() for n in self._nodes.values()],
            [e.to_dict() for e in self._edges.values()],
        )

    @staticmethod
    def _existing_structural_hash(path: Path) -> Optional[str]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        try:
            return GraphBuilder._hash_graph(
                data.get("nodes", []), data.get("edges", [])
            )
        except Exception:
            return None

    def write_json(self, out_path: Path, tool_version: str, *, force: bool = False) -> bool:
        """Write the graph JSON to `out_path`.

        Returns True if the file was (re)written, False if the on-disk
        graph already matches what we just computed and we deliberately
        skipped the write to avoid spurious git churn from timestamps.

        Pass `force=True` to always rewrite.
        """
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not force and out_path.exists():
            existing = self._existing_structural_hash(out_path)
            if existing is not None and existing == self._structural_hash():
                return False
        out_path.write_text(
            json.dumps(self.to_dict(tool_version), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        return True


def _git_commit_short(repo_root: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        return out.stdout.strip() or None
    except Exception:
        return None
