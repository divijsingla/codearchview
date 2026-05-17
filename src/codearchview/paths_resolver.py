"""tsconfig path-alias resolver.

`typescript-language-server` does not reliably answer
`textDocument/definition` on import-source string positions, so we
implement a fallback that mirrors `tsc`'s baseUrl + paths resolution
ourselves.  This is the same algorithm Vite, webpack, esbuild and tsc
itself use.

Limitations:
    * `extends` chains are followed one level (good enough for nearly
      all real projects).
    * JSON5 (`tsconfig.json` with comments / trailing commas) is
      handled by a small comment-stripping pre-pass; we do not pull in
      a json5 dependency.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional


_CANDIDATE_SUFFIXES = (
    ".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs",
)


def _strip_jsonc(text: str) -> str:
    # Remove // line comments and /* ... */ block comments, plus trailing
    # commas that the TS team kindly tolerates in tsconfig files.
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _load_jsonc(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    return json.loads(_strip_jsonc(raw))


def _load_tsconfig(path: Path) -> dict:
    """Load tsconfig and merge a single level of `extends`."""
    data = _load_jsonc(path)
    extends = data.get("extends")
    if isinstance(extends, str):
        ext_path = (path.parent / extends).resolve()
        if not ext_path.exists() and not ext_path.suffix:
            ext_path = ext_path.with_suffix(".json")
        if ext_path.exists():
            base = _load_tsconfig(ext_path)
            merged_compiler = {**base.get("compilerOptions", {}),
                               **data.get("compilerOptions", {})}
            data = {**base, **data, "compilerOptions": merged_compiler}
    return data


def _resolve_with_extensions(base: Path) -> Optional[Path]:
    if base.is_file():
        return base
    if base.suffix:
        # Already had a suffix that didn't match a file
        for s in _CANDIDATE_SUFFIXES:
            cand = Path(str(base) + s)
            if cand.is_file():
                return cand
    else:
        for s in _CANDIDATE_SUFFIXES:
            cand = base.with_suffix(s)
            if cand.is_file():
                return cand
    if base.is_dir():
        for s in _CANDIDATE_SUFFIXES:
            cand = base / f"index{s}"
            if cand.is_file():
                return cand
    return None


class PathsResolver:
    """Resolves bare-aliased and relative TS import specifiers to paths."""

    def __init__(self, project_root: Path, tsconfig_paths: Iterable[Path] = ()):
        self.project_root = project_root.resolve()
        self.base_url: Path = self.project_root
        # pattern (e.g. "@/*") -> list of substitution templates (e.g. ["./src/*"])
        self.paths: list[tuple[str, list[str]]] = []
        for cfg in tsconfig_paths:
            self._load(cfg)

    # ---- configuration -----------------------------------------------------

    def _load(self, tsconfig_path: Path) -> None:
        if not tsconfig_path.exists():
            return
        try:
            data = _load_tsconfig(tsconfig_path)
        except Exception:
            return
        co = data.get("compilerOptions", {}) or {}
        base_url = co.get("baseUrl")
        if isinstance(base_url, str):
            self.base_url = (tsconfig_path.parent / base_url).resolve()
        paths = co.get("paths")
        if isinstance(paths, dict):
            for pattern, targets in paths.items():
                if isinstance(targets, list) and targets:
                    self.paths.append(
                        (pattern, [t for t in targets if isinstance(t, str)])
                    )

    # ---- resolution --------------------------------------------------------

    def resolve(self, specifier: str, from_file: Optional[Path] = None) -> Optional[Path]:
        spec = specifier.split("?", 1)[0]  # strip ?raw etc.

        # Relative import — anchor to the importing file.
        if spec.startswith("."):
            if from_file is None:
                return None
            return _resolve_with_extensions((from_file.parent / spec).resolve())

        # Path-alias match.  We try the most specific pattern first
        # (longest prefix wins) because tsconfig semantics give priority
        # to the longer match.
        for pattern, targets in sorted(self.paths, key=lambda p: -len(p[0])):
            sub = self._match_pattern(pattern, spec)
            if sub is None:
                continue
            for target in targets:
                resolved_target = target.replace("*", sub) if "*" in target else target
                cand = (self.base_url / resolved_target).resolve()
                res = _resolve_with_extensions(cand)
                if res is not None:
                    return res

        # baseUrl-only resolution (non-aliased bare import that maps
        # under baseUrl, e.g. "components/Foo" when baseUrl = "./src").
        if not spec.startswith("@") and not spec.startswith("/"):
            cand = (self.base_url / spec).resolve()
            res = _resolve_with_extensions(cand)
            if res is not None:
                return res

        return None

    @staticmethod
    def _match_pattern(pattern: str, spec: str) -> Optional[str]:
        """Return the captured `*` substring if `spec` matches `pattern`, else None."""
        if "*" not in pattern:
            return "" if pattern == spec else None
        prefix, suffix = pattern.split("*", 1)
        if not spec.startswith(prefix) or not spec.endswith(suffix):
            return None
        return spec[len(prefix) : len(spec) - len(suffix)] if suffix else spec[len(prefix) :]
