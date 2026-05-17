"""Smoke tests for the external classifier (no LSP / tree-sitter needed)."""

from __future__ import annotations

from pathlib import Path

from codearchview.classify import (
    classify_resolved_path,
    classify_specifier,
    is_inside_node_modules,
)


def test_npm_bare_specifier():
    ext = classify_specifier("react")
    assert ext is not None
    assert ext.name == "react"
    assert ext.source == "npm"


def test_scoped_npm_specifier():
    ext = classify_specifier("@radix-ui/react-slot")
    assert ext is not None
    assert ext.name == "@radix-ui/react-slot"
    assert ext.source == "npm"


def test_node_builtin():
    ext = classify_specifier("fs/promises")
    assert ext is not None
    assert ext.source == "node"


def test_node_prefixed_builtin():
    ext = classify_specifier("node:path")
    assert ext is not None
    assert ext.source == "node"


def test_relative_returns_none():
    assert classify_specifier("./Foo") is None
    assert classify_specifier("../bar/baz") is None


def test_alias_returns_none():
    # We always defer alias resolution to the LSP.
    assert classify_specifier("@/components/Navbar") is None


def test_relative_asset_is_external():
    ext = classify_specifier("./index.css")
    assert ext is not None
    assert ext.source == "asset"


def test_node_modules_path():
    p = Path("/repo/node_modules/react/index.js")
    assert is_inside_node_modules(p)
    ext = classify_resolved_path(p)
    assert ext is not None
    assert ext.name == "react"


def test_scoped_node_modules_path():
    p = Path("/repo/node_modules/@radix-ui/react-slot/dist/index.js")
    ext = classify_resolved_path(p)
    assert ext is not None
    assert ext.name == "@radix-ui/react-slot"
