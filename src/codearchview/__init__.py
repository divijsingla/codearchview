"""codearchview — DFS architecture graph extractor for TypeScript projects.

Combines tree-sitter (for fast, lossless syntax extraction) with the
TypeScript Language Server (for accurate cross-file symbol resolution)
to produce a semantic graph of files and symbols, stopping at the
external-dependency boundary (node_modules / unresolved bare specifiers).
"""

__version__ = "0.1.0"
