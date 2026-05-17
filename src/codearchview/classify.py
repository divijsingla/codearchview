"""External-boundary classification.

A target is considered "external" (and therefore terminates DFS
recursion) if it falls into any of:

    * resolved path lives under any `node_modules/` directory
    * resolved path lives under the bundled TypeScript lib (lib.*.d.ts)
    * import source cannot be resolved at all (unresolved bare specifier)
    * import source is a Node built-in (`fs`, `path`, ...)
    * import source is a non-code asset (`.css`, `.md?raw`, `.svg`, image, ...)

When external, we also classify the *source* (npm / node / browser /
asset) so the UI can color them differently.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


NODE_BUILTINS = frozenset(
    {
        "assert", "buffer", "child_process", "cluster", "console", "constants",
        "crypto", "dgram", "dns", "domain", "events", "fs", "http", "https",
        "module", "net", "os", "path", "perf_hooks", "process", "punycode",
        "querystring", "readline", "repl", "stream", "string_decoder", "sys",
        "timers", "tls", "tty", "url", "util", "v8", "vm", "worker_threads",
        "zlib",
    }
)


ASSET_SUFFIXES = frozenset(
    {".css", ".scss", ".sass", ".less", ".svg", ".png", ".jpg", ".jpeg",
     ".gif", ".webp", ".avif", ".ico", ".json", ".md", ".mdx", ".txt",
     ".woff", ".woff2", ".ttf", ".otf"}
)


@dataclass(frozen=True)
class External:
    name: str            # package name as written in `import ... from "<name>"`
    source: str          # "npm" | "node" | "browser" | "asset" | "unresolved"


def _strip_query(spec: str) -> str:
    return spec.split("?", 1)[0]


def classify_specifier(spec: str) -> Optional[External]:
    """Classify an import specifier *without* path resolution.

    Returns None when the specifier looks internal (relative or alias),
    meaning the caller should attempt path resolution / LSP follow.
    """
    bare = _strip_query(spec)

    # Relative or absolute path -> internal candidate
    if bare.startswith(".") or bare.startswith("/"):
        # ...unless suffix is an asset
        suffix = Path(bare).suffix.lower()
        if suffix in ASSET_SUFFIXES:
            return External(name=bare, source="asset")
        return None

    # Path-alias-looking imports (start with @/ etc.) — caller resolves them.
    # Note: scoped npm packages also start with @, but they contain a slash
    # after the scope ("@radix-ui/react-slot"). Pure alias forms like "@/foo"
    # also have a slash; distinguishing requires tsconfig path matching, which
    # is what the LSP gives us. So we always return None here and let the
    # caller try LSP first.
    if bare.startswith("@/"):
        return None

    # Node built-in (with or without the `node:` prefix)
    if bare.startswith("node:"):
        return External(name=bare, source="node")
    head = bare.split("/", 1)[0]
    if head in NODE_BUILTINS:
        return External(name=bare, source="node")

    # Otherwise it's an npm bare specifier
    package = bare if not bare.startswith("@") else "/".join(bare.split("/", 2)[:2])
    return External(name=package, source="npm")


def is_inside_node_modules(path: Path) -> bool:
    parts = path.resolve().parts
    return "node_modules" in parts


def is_ts_lib(path: Path) -> bool:
    """Heuristic for the bundled TypeScript lib (`lib.es2020.d.ts` etc.)."""
    name = path.name
    if not name.startswith("lib.") or not name.endswith(".d.ts"):
        return False
    return any(p == "typescript" or p == "lib" for p in path.resolve().parts)


def classify_resolved_path(path: Path) -> Optional[External]:
    """If `path` represents an external target, return an External record."""
    if is_inside_node_modules(path):
        # Try to extract the package name from the path
        parts = path.resolve().parts
        idx = parts.index("node_modules")
        rest = parts[idx + 1 :]
        if not rest:
            return External(name="<node_modules>", source="npm")
        if rest[0].startswith("@") and len(rest) >= 2:
            pkg = f"{rest[0]}/{rest[1]}"
        else:
            pkg = rest[0]
        return External(name=pkg, source="npm")
    if is_ts_lib(path):
        return External(name=path.name, source="browser")
    return None
