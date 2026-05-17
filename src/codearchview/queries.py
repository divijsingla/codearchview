"""S-expression tree-sitter queries for TS/TSX.

Queries are compiled lazily against either the TS or TSX language.
The same query source works for both grammars (tree-sitter-typescript
ships parallel grammars that agree on the relevant node types).
"""

from __future__ import annotations

from functools import lru_cache

from tree_sitter import Language, Query

from .parser import TS_LANGUAGE, TSX_LANGUAGE


# Top-level import statements:
#   import X from "foo"
#   import { a, b as c } from "foo"
#   import * as ns from "foo"
#   import "foo"
#   import type { T } from "foo"
IMPORTS_QUERY = """
(import_statement
  (import_clause)? @clause
  source: (string (string_fragment) @source)) @import
"""

# Top-level declarations we care about.  Captures one named declaration
# per match plus the surrounding statement so we can locate exports.
TOP_LEVEL_QUERY = """
(program
  (export_statement
    declaration: (lexical_declaration
      (variable_declarator name: (identifier) @name) @decl)) @export)

(program
  (export_statement
    declaration: (function_declaration name: (identifier) @name) @decl) @export)

(program
  (export_statement
    declaration: (class_declaration name: (type_identifier) @name) @decl) @export)

(program
  (export_statement
    declaration: (interface_declaration name: (type_identifier) @name) @decl) @export)

(program
  (export_statement
    declaration: (type_alias_declaration name: (type_identifier) @name) @decl) @export)

(program
  (export_statement
    declaration: (enum_declaration name: (identifier) @name) @decl) @export)

(program
  (lexical_declaration
    (variable_declarator name: (identifier) @name) @decl))

(program
  (function_declaration name: (identifier) @name) @decl)

(program
  (class_declaration name: (type_identifier) @name) @decl)

(program
  (interface_declaration name: (type_identifier) @name) @decl)

(program
  (type_alias_declaration name: (type_identifier) @name) @decl)
"""

# Identifier references inside any subtree.  We classify by parent node
# at extraction time (call_expression -> "calls", jsx_opening_element ->
# "renders", type_identifier -> "uses-type").
REFERENCES_QUERY = """
(call_expression function: (identifier) @call_ref)
(call_expression function: (member_expression object: (identifier) @call_ref))
(jsx_opening_element name: (identifier) @jsx_ref)
(jsx_self_closing_element name: (identifier) @jsx_ref)
(jsx_opening_element name: (member_expression object: (identifier) @jsx_ref))
(jsx_self_closing_element name: (member_expression object: (identifier) @jsx_ref))
(type_identifier) @type_ref
(new_expression constructor: (identifier) @call_ref)
"""

# Detect whether a function/arrow body returns JSX (used to classify
# PascalCase symbols as React components).
JSX_BODY_QUERY = """
(jsx_element) @jsx
(jsx_self_closing_element) @jsx
(jsx_fragment) @jsx
"""


@lru_cache(maxsize=32)
def _compile(language_name: str, source: str) -> Query:
    lang: Language = TSX_LANGUAGE if language_name == "tsx" else TS_LANGUAGE
    return Query(lang, source)


def imports_query(language_name: str) -> Query:
    return _compile(language_name, IMPORTS_QUERY)


def top_level_query(language_name: str) -> Query:
    return _compile(language_name, TOP_LEVEL_QUERY)


def references_query(language_name: str) -> Query:
    return _compile(language_name, REFERENCES_QUERY)


def jsx_body_query(language_name: str) -> Query:
    return _compile(language_name, JSX_BODY_QUERY)
