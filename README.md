# codearchview

DFS code architecture extractor for TypeScript / TSX projects.

`codearchview` takes a single entry file (e.g. `src/main.tsx`), traverses
the import / call / render / type-reference graph depth-first using
**tree-sitter** for syntax and the **TypeScript Language Server** for
cross-file symbol resolution, and emits a JSON graph of files and
symbols. Traversal stops at the external dependency boundary: anything
inside `node_modules`, Node built-ins, and unresolved bare specifiers.

The output JSON is designed to be visualised by a separate UI (e.g. a
React Flow tab in your own app) and supports multiple granularities
(file, component, hook, function, type, all symbols).

## Install

```bash
# from git (pin to a tag once published):
pip install "git+https://github.com/dsingla/codearchview@main"

# or local checkout, editable:
pip install -e .
```

`codearchview` shells out to `typescript-language-server`, which must be
installed via npm:

```bash
npm i -g typescript typescript-language-server
```

Python 3.10+ and Node 18+ are required.

## Usage

```bash
python -m codearchview \
  --entry src/main.tsx \
  --project-root . \
  --out src/data/architecture.json
```

Useful flags:

| flag | purpose |
| --- | --- |
| `--no-lsp` | Skip the language server. File-level imports only (still useful for very fast architecture sketches). |
| `--no-types` | Do not follow type-only references. Produces a smaller graph. |
| `-v` / `--verbose` | Debug logging. |

### Exit codes

| code | meaning |
| --- | --- |
| 0 | Success |
| 1 | Generic error / argument problem |
| 2 | Language server failed to start |
| 3 | Entry file not found |
| 4 | Parse failure on the entry file |

Consumers (e.g. a `prebuild` npm hook) can branch on these to either
hard-fail the build or fall back to a committed JSON.

## Output schema

```json
{
  "meta": {
    "entry": "src/main.tsx",
    "commit": "abc1234",
    "generatedAt": "2026-05-17T10:00:00+00:00",
    "tool": "codearchview",
    "toolVersion": "0.1.0"
  },
  "nodes": [
    { "id": "file::src/App.tsx", "kind": "file", "name": "App.tsx", "path": "src/App.tsx" },
    {
      "id": "sym::src/App.tsx::App",
      "kind": "component",
      "name": "App",
      "file": "src/App.tsx",
      "loc": { "startLine": 18, "endLine": 38 },
      "exported": true
    },
    { "id": "ext::react", "kind": "external", "name": "react", "source": "npm" }
  ],
  "edges": [
    { "source": "file::src/App.tsx", "target": "sym::src/App.tsx::App", "kind": "contains" },
    { "source": "sym::src/App.tsx::App", "target": "sym::src/components/Navbar.tsx::Navbar", "kind": "renders" },
    { "source": "file::src/App.tsx", "target": "ext::react", "kind": "imports" }
  ]
}
```

### Node kinds

`file`, `component`, `hook`, `function`, `const`, `class`, `interface`,
`type`, `enum`, `default-export`, `external`.

### Edge kinds

`contains` (file → symbol), `imports` (file → file or file →
external), `calls`, `renders` (JSX usage), `uses-type`.

### External `source`

`npm`, `node`, `browser` (TS lib), `asset` (css / images / `.md?raw`),
`unresolved` (bare specifier the LSP could not resolve).

## How it works

```
entry file -> tree-sitter (imports, top-level decls, refs)
            \
             -> LSP (textDocument/definition) -> resolved file/symbol
                                                    |
                                                    +-> inside project? -> recurse
                                                    +-> in node_modules / unresolved? -> external node, stop
```

* **tree-sitter** gives fast, lossless syntax: import statements,
  top-level declarations, identifier references inside each symbol's
  body, JSX detection for component classification.
* **TypeScript Language Server** (via `multilspy`) resolves every
  reference to a definition file + position, handling tsconfig path
  aliases, barrel re-exports, generics, and ambient declarations
  without us reimplementing TS module resolution.

Components are detected by combining naming convention (PascalCase) with
JSX-body detection. Hooks are detected by `^use[A-Z]` naming.

## Status

v0.1: single-entry traversal, TypeScript / TSX only, multilspy backend.
No incremental mode, no PyPI release yet.
