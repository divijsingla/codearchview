"""CLI entry: ``python -m codearchview --entry <file> --out <json>``.

Exit codes:
    0 - success
    1 - generic error / bad arguments
    2 - LSP failed to start
    3 - entry file not found
    4 - parse failure on the entry file
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .builder import GraphBuilder
from .lsp import LspUnavailable, TypeScriptLsp
from .parser import parse_file
from .traverser import Traverser


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codearchview",
        description=(
            "Extract a depth-first architecture graph from a TypeScript / TSX "
            "entry file, stopping at external dependencies."
        ),
    )
    p.add_argument("--entry", required=True, help="Entry .ts / .tsx file (relative to --project-root)")
    p.add_argument("--project-root", default=".", help="Project root (default: current directory)")
    p.add_argument(
        "--tsconfig",
        default=None,
        help="Path to tsconfig (informational; resolution is done by the LSP). Default: auto-detected by the LSP.",
    )
    p.add_argument("--out", required=True, help="Path to write the architecture JSON")
    p.add_argument(
        "--no-lsp",
        action="store_true",
        help="Skip the language server (file-level imports only; no symbol-level cross-file edges).",
    )
    p.add_argument(
        "--no-types",
        action="store_true",
        help="Do not follow type-only references (smaller graph, faster).",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"codearchview {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[codearchview] %(levelname)s %(message)s",
    )

    project_root = Path(args.project_root).resolve()
    entry = (project_root / args.entry).resolve()

    if not entry.exists():
        print(f"entry file not found: {entry}", file=sys.stderr)
        return 3

    # Sanity-parse the entry up front so a broken file fails fast.
    if parse_file(entry) is None:
        print(f"failed to parse entry file: {entry}", file=sys.stderr)
        return 4

    builder = GraphBuilder(project_root=project_root, entry=args.entry)

    if args.no_lsp:
        lsp = None
    else:
        try:
            lsp = TypeScriptLsp(project_root)
        except LspUnavailable as exc:
            print(f"language server unavailable: {exc}", file=sys.stderr)
            return 2

    try:
        if lsp is not None:
            with lsp:
                Traverser(
                    project_root=project_root,
                    builder=builder,
                    lsp=lsp,
                    follow_types=not args.no_types,
                ).traverse(entry)
        else:
            Traverser(
                project_root=project_root,
                builder=builder,
                lsp=None,
                follow_types=not args.no_types,
            ).traverse(entry)
    except Exception:
        logging.exception("traversal failed")
        return 1

    out_path = Path(args.out).resolve()
    builder.write_json(out_path, tool_version=__version__)
    n_nodes = len(builder._nodes)  # noqa: SLF001 — internal but stable
    n_edges = len(builder._edges)  # noqa: SLF001
    print(f"wrote {out_path} ({n_nodes} nodes, {n_edges} edges)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
