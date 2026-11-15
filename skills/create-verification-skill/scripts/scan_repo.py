#!/usr/bin/env python3
"""Inventory a repo before writing its verification skill.

Reports: languages, marker files, build and test commands, launch candidates
(servers with ports, CLIs), HTTP routes, CLI subcommands, env vars, existing
skill dirs, and a proposed feature grouping (routes and CLI verbs that touch
the same resource become one user-facing feature).

Heuristics (stdlib only, no execution of repo code):
  Python  AST: Flask/FastAPI-style decorators, Django path(), http.server
          handlers (do_GET/do_POST... with ==, startswith/endswith, re.match,
          parts[i] == "x", and route tables like ("GET", r"^/todos$")),
          "GET /path" strings, argparse add_parser/add_argument, click/typer
          commands, ports (--port defaults, HTTPServer((host, port)), run(port=)),
          os.environ / os.getenv, __main__.py packages, [project.scripts].
  Node    package.json scripts/bin, app.get('/x') style routes, .command('x'),
          .listen(N), process.env.X.
  Go      HandleFunc("GET /x/{id}"), r.GET("/x"), ListenAndServe(":N"), cobra Use:.
  Make    targets (test/check, build/all, run/serve/start/dev).
Test files and agent dirs (.claude, .cursor, ...) are not scanned for routes or commands.
A path under a filesystem root (/home, /tmp, /Users, ...) is a route only where the source declares it: a router
call, decorator, explicitly named route table, "GET /x" string, or comparison with a recognized request path.
Bare names need a preceding request-path assignment in the same function; path segments need a split of that path.
Assignments that may not have executed on exceptional paths or after optional suites do not prove a path source.
Unconfirmed containers/segments retain filesystem-prefix filtering. Go receiver names are restricted to common
router conventions (r, router, mux, app, engine, group, api, server; http for Handle/HandleFunc). Each CLI option records takes_value: false for argparse's store_true,
store_false, store_const, append_const, count, help, version, and BooleanOptionalAction, and for click flags.
Preceding register("action", alias, argparse_action) calls on the same parser preserve those action semantics.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from bisect import bisect_right
from collections import Counter
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".nox", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".nuxt", "target", "vendor", "coverage",
             "artifacts", ".claude", ".cursor", ".agents", ".codex", ".idea", ".vscode", "site-packages"}
SKILL_ROOTS = [".claude/skills", ".cursor/skills", ".agents/skills", ".codex/skills", "skills"]
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|e2e|testdata|fixtures)(/|$)|(^|/)(test_[^/]*|[^/]*_test\.[a-z]+|"
                       r"[^/]*\.(test|spec)\.[a-z]+|conftest\.py)$")
LANG_EXT = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
            ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust", ".rb": "ruby", ".java": "java",
            ".kt": "kotlin", ".swift": "swift", ".c": "c", ".cc": "cpp", ".cpp": "cpp", ".cs": "csharp",
            ".php": "php", ".sh": "shell"}
MARKERS = ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile", "tox.ini", "package.json",
           "pnpm-lock.yaml", "yarn.lock", "package-lock.json", "go.mod", "Cargo.toml", "Makefile", "justfile",
           "Procfile", "Dockerfile", "docker-compose.yml", "compose.yaml", "README.md", "AGENTS.md", "CLAUDE.md"]
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
HEALTH_NAMES = {"health", "healthz", "healthcheck", "health-check", "ready", "readyz", "readiness", "live", "livez",
                "liveness", "ping", "_health", "__health", "status"}
SERVE_NAMES = {"serve", "server", "runserver", "start", "web", "api", "dev", "up", "http", "run"}
PORT_FLAGS = ("--port", "-p", "--http-port", "--listen-port")
DATA_FLAGS = ("--data", "--data-dir", "--datadir", "--db", "--database", "--db-path", "--storage", "--store",
              "--state", "--state-dir", "--dir", "--root", "--path", "--file", "--home", "--data-path")
FS_PREFIX = re.compile(r"^/(tmp|etc|usr|var|dev|proc|bin|sbin|home|opt|root|sys|Users|private|mnt|Volumes|lib)(/|$)")
ROUTE_DECOS = {"route", "get", "post", "put", "patch", "delete", "head", "options", "api_route", "websocket"}
NO_VALUE_ACTIONS = {"store_true", "store_false", "store_const", "append_const", "count", "help", "version",
                    "BooleanOptionalAction"}  # argparse actions that consume no token
RE_FUNCS = {"match", "fullmatch", "search", "compile"}
SKIP_CALLS = {"split", "rsplit", "strip", "rstrip", "lstrip", "join", "replace", "send_header", "open", "Path",
              "exists", "isfile", "isdir", "mkdir", "makedirs", "write", "print", "log_message", "format", "encode",
              "decode", "urljoin", "sub", "add_argument", "add_parser", "getenv", "get_header", "info", "debug",
              "warning", "error", "exception", "removeprefix", "removesuffix", "partition", "rpartition"}
SERVER_HINT = re.compile(r"BaseHTTPRequestHandler|http\.server|socketserver|wsgiref|PATH_INFO|aiohttp|tornado|werkzeug")
VERB_CLASSES = {
    "create": {"add", "create", "new", "post", "insert", "make"},
    "list": {"list", "ls", "all", "index"},
    "show": {"show", "get", "view", "cat", "info", "read", "describe"},
    "update": {"edit", "update", "set", "rename", "mv", "modify", "patch", "put"},
    "delete": {"rm", "del", "delete", "remove", "destroy", "drop"},
    "done": {"done", "complete", "finish", "check", "close", "resolve"},
}
SKIP_COMMANDS = {"help", "version", "completion", "completions"}
ACTION_ORDER = {"create": 0, "list": 1, "show": 2, "update": 3, "done": 4, "delete": 9}


# ---------------------------------------------------------------- files

def list_files(repo: Path, max_files: int = 20000) -> list[str]:
    files: list[str] = []
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-co", "--exclude-standard", "-z"],
                             capture_output=True, check=True).stdout
        files = [f for f in out.decode(errors="replace").split("\0") if f]
        if not files:
            raise RuntimeError("empty")
    except (OSError, subprocess.CalledProcessError, RuntimeError):
        for dirpath, dirnames, filenames in os.walk(repo):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            rel = Path(dirpath).relative_to(repo).as_posix()
            for f in sorted(filenames):
                files.append(f if rel == "." else f"{rel}/{f}")
    files = [f for f in files if not any(p in SKIP_DIRS for p in f.split("/")[:-1]) and (repo / f).is_file()]
    return sorted(files)[:max_files]


def read(repo: Path, rel: str, limit: int = 1_000_000) -> str:
    p = repo / rel
    try:
        if p.stat().st_size > limit:
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


# ---------------------------------------------------------------- routes

def normalize_route(s: str, kind: str = "eq", declared: bool = False) -> str | None:
    """Route template for a path string, or None when it does not look like a route.

    declared=True is for paths the source declares as routes (router registrations, route decorators, route tables,
    "GET /x" strings, comparisons with the request path): those keep /home, /Users and other names that are also
    filesystem roots. Strings in containers or generic call arguments may be file paths, so FS_PREFIX drops them.
    """
    s = s.strip()
    if kind == "regex" or re.search(r"[\^$()\[\]\\+?|]", s):
        s = re.sub(r"^(\^|\\A)", "", s)
        s = re.sub(r"(/\?)?(\$|\\Z|\\z)$", "", s)
        s = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"<\1>", s)
        s = re.sub(r"\((?!\?)[^)]*\)", "<id>", s)
        s = re.sub(r"\[\^/\]\+|\\d\+|\\w\+|\[0-9\]\+|\[\\w-\]\+|\.\+|\.\*", "<id>", s)
        s = s.replace("\\/", "/").replace("\\.", ".").replace("\\-", "-")
        if re.search(r"[\^$()\[\]\\+?|]", s):
            return None
    s = re.sub(r"<(?:\w+:)?(\w+)>", r"<\1>", s)          # Flask <int:id>
    s = re.sub(r"\{(\w+)(?::[^}]*)?\}", r"<\1>", s)        # FastAPI/Go {id}
    s = re.sub(r"(?<=/):(\w+)", r"<\1>", s)                # Express :id
    if not s.startswith("/") or not re.fullmatch(r"/[\w\-./<>*~]*", s) or (not declared and FS_PREFIX.match(s)):
        return None
    if "." in s.rsplit("/", 1)[-1] and not s.endswith((".json", ".html", ".txt", ".ico", ".xml")):
        return None  # looks like a file path, not a route
    if len(s) > 1:
        s = s.rstrip("/") or "/"
    return s


def route_key(method: str, path: str) -> tuple[str, str]:
    return method.upper(), re.sub(r"<[^>]*>", "<>", path)


def route_regex(path: str) -> re.Pattern:
    """Match concrete or templated paths against a route; <*> spans segments, <x> one segment."""
    out, i = [], 0
    for m in re.finditer(r"<[^>]*>", path):
        out.append(re.escape(path[i:m.start()]))
        out.append(".+" if m.group(0) == "<*>" else "[^/]+")
        i = m.end()
    out.append(re.escape(path[i:]))
    return re.compile("^" + "".join(out) + "/?$")


def is_health(path: str) -> bool:
    last = [seg for seg in path.strip("/").split("/") if seg and not seg.startswith("<")]
    return bool(last) and last[-1].lower() in HEALTH_NAMES and len(last) <= 2


# ---------------------------------------------------------------- python

def _parents(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # type: ignore[attr-defined]


def _call_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def _str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _literal(node):
    try:
        return ast.literal_eval(node)
    except Exception:  # noqa: BLE001 - any non-literal
        return None


def _methods_in(node) -> list[str]:
    out = []
    for n in ast.walk(node):
        s = _str(n)
        if s in HTTP_METHODS and isinstance(getattr(n, "_parent", None),
                                            (ast.Compare, ast.Tuple, ast.List, ast.Set, ast.MatchValue)):
            out.append(s)
    return sorted(set(out), key=HTTP_METHODS.index)


def _method_context(node) -> list[str]:
    child, cur = node, getattr(node, "_parent", None)
    while cur is not None:
        if isinstance(cur, ast.If) and not any(child is c for c in cur.orelse):
            ms = _methods_in(cur.test)
            if ms:
                return ms
        if isinstance(cur, ast.match_case):
            ms = _methods_in(cur.pattern)
            if ms:
                return ms
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            m = re.fullmatch(r"do_([A-Z]+)", cur.name)
            return [m.group(1)] if m and m.group(1) in HTTP_METHODS else []
        child, cur = cur, getattr(cur, "_parent", None)
    return []


def _usage(node) -> str | None:
    p = getattr(node, "_parent", None)
    if isinstance(p, ast.Call):
        if node is p.func:
            return None
        name = _call_name(p)
        if name == "startswith":
            return "prefix"
        if name == "endswith":
            return "suffix"
        if name in RE_FUNCS:
            return "regex"
        if name in SKIP_CALLS:
            return None
        return "eq"
    if isinstance(p, (ast.Compare, ast.Tuple, ast.List, ast.Set, ast.Dict, ast.MatchValue)):
        return "eq"
    return None


def _function(node):
    if node is None:
        return None
    if not hasattr(node, "_function_scope"):
        node._function_scope = (node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                                else None if isinstance(node, ast.ClassDef)
                                else _function(getattr(node, "_parent", None)))
    return node._function_scope


def _target_bindings(target, value=None):
    """Pair bound names with known RHS elements; opaque or starred unpacking revokes provenance."""
    if isinstance(target, ast.Name):
        yield target.id, value
    elif isinstance(target, ast.Starred):
        yield from _target_bindings(target.value)
    elif isinstance(target, (ast.Tuple, ast.List)):
        known = (isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts)
                 and not any(isinstance(e, ast.Starred) for e in (*target.elts, *value.elts)))
        path_source = _query_path_source(value)
        for index, element in enumerate(target.elts):
            source = value.elts[index] if known else path_source if index == 0 else None
            yield from _target_bindings(element, source)


def _condition_branches(node, truth):
    """A true and / false or proves that every operand ran; other outcomes prove no prefix."""
    if isinstance(node, ast.NamedExpr):
        return _condition_branches(node.value, truth)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _condition_branches(node.operand, not truth)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And if truth else ast.Or):
        branches = {node: len(node.values) - 1}
        for value in node.values:
            branches.update(_condition_branches(value, truth))
        return branches
    return {}


def _binding_position(node, end=False):
    """Source order, except conditional-expression tests execute before either source arm."""
    if not hasattr(node, "_expression_context"):
        parent = getattr(node, "_parent", None)
        if parent is not None:
            _binding_position(parent)
        context = parent._expression_context if parent is not None else ()
        if isinstance(parent, ast.IfExp):
            arm = (parent.test, parent.body, parent.orelse).index(node)
            context += (parent.lineno, parent.col_offset, arm)
        node._expression_context = context
    return node._expression_context + ((node.end_lineno, node.end_col_offset) if end
                                       else (getattr(node, "lineno", 0), getattr(node, "col_offset", 0)))


def _if_branches(node, fn):
    """Cached control-flow arms and evaluated short-circuit prefixes."""
    if node is fn:
        return {}
    if not hasattr(node, "_branch_arms"):
        parent = getattr(node, "_parent", None)
        branches = _if_branches(parent, fn).copy()
        if isinstance(parent, ast.If):
            if node in parent.body:
                branches[parent] = "body"
                branches.update(_condition_branches(parent.test, True))
            elif node in parent.orelse:
                branches[parent] = "else"
                branches.update(_condition_branches(parent.test, False))
        elif isinstance(parent, ast.IfExp):
            if node is parent.body:
                branches[parent] = "body"
                branches.update(_condition_branches(parent.test, True))
            elif node is parent.orelse:
                branches[parent] = "else"
                branches.update(_condition_branches(parent.test, False))
        elif isinstance(parent, ast.BoolOp):
            if not hasattr(parent, "_operand_positions"):
                parent._operand_positions = {value: i for i, value in enumerate(parent.values)}
            index = parent._operand_positions[node]
            if index:
                branches[parent] = index  # Reaching operand N proves operands 0..N were evaluated.
        elif isinstance(parent, ast.Match) and isinstance(node, ast.match_case):
            branches[parent] = node
            if node.guard is not None:
                branches[node] = "entered"
        elif isinstance(parent, ast.match_case) and parent.guard is not None:
            # Pattern captures and guard assignments survive a false guard. They may reach later
            # cases, unlike body assignments, but do not establish a definite source at that join.
            if node in parent.body:
                branches.update(_condition_branches(parent.guard, True))
            else:
                branches.pop(parent._parent)
        elif isinstance(parent, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            if node in parent.body:
                branches[parent] = "body"
            elif node in parent.handlers:
                branches[parent] = node
            elif node in parent.orelse:
                branches[parent] = "else"
            elif node in parent.finalbody:
                branches[parent] = "finally"
        elif isinstance(parent, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
            if node in parent.body:
                branches[parent] = "body"
            elif node in getattr(parent, "orelse", []):
                branches[parent] = "else"
        elif isinstance(parent, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            branches[parent] = "body"
        node._branch_arms = branches
    return node._branch_arms


def _binding_index(fn):
    """Index each function's bindings once, sharing cached scope and branch ancestry."""
    if hasattr(fn, "_bindings"):
        return fn._bindings
    bindings = {}

    def record(at, pairs):
        end = _binding_position(at, end=True)
        # Reaching code after a finally suite proves it completed. Other try suites may
        # have been interrupted before this binding, even when their source precedes it.
        assigned_in = {branch: arm for branch, arm in _if_branches(at, fn).items() if arm != "finally"}
        for name, value in pairs:
            bindings.setdefault(name, []).append((end, value, assigned_in))

    for candidate in ast.walk(fn):
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if _function(getattr(candidate, "_parent", None)) is fn:
                record(candidate, [(candidate.name, None)])
            continue
        if _function(candidate) is not fn:
            continue
        if isinstance(candidate, ast.Assign):
            for target in candidate.targets:
                record(candidate, _target_bindings(target, candidate.value))
        elif isinstance(candidate, (ast.AnnAssign, ast.NamedExpr)):
            if candidate.value is not None:  # An annotation alone does not rebind an existing local.
                record(candidate, _target_bindings(candidate.target, candidate.value))
        elif isinstance(candidate, ast.AugAssign):
            record(candidate, _target_bindings(candidate.target))
        elif isinstance(candidate, (ast.For, ast.AsyncFor)):
            # The iterable is evaluated before binding the target, which takes effect inside the body.
            record(candidate.iter, _target_bindings(candidate.target))
        elif isinstance(candidate, (ast.With, ast.AsyncWith)):
            for item in candidate.items:
                if item.optional_vars is not None:
                    record(item.optional_vars, _target_bindings(item.optional_vars))
        elif isinstance(candidate, ast.Delete):
            for target in candidate.targets:
                record(candidate, _target_bindings(target))
        elif isinstance(candidate, (ast.Import, ast.ImportFrom)):
            record(candidate, [(alias.asname or alias.name.split('.')[0], None) for alias in candidate.names])
        elif isinstance(candidate, ast.ExceptHandler) and candidate.name:
            record(candidate.type, [(candidate.name, None)])
            record(candidate, [(candidate.name, None)])  # Python deletes the exception alias afterward.
        elif isinstance(candidate, (ast.MatchAs, ast.MatchStar)) and candidate.name:
            record(candidate, [(candidate.name, None)])
        elif isinstance(candidate, ast.MatchMapping) and candidate.rest:
            record(candidate, [(candidate.rest, None)])
    for values in bindings.values():
        values.sort(key=lambda binding: binding[0])
    fn._bindings = bindings
    return bindings


def _assigned_value(node):
    """Latest compatible binding; conditional joins with unknown sources revoke provenance."""
    if not isinstance(node, ast.Name) or (fn := _function(node)) is None:
        return None
    # Comprehension targets shadow enclosing aliases without rebinding them outside the comprehension.
    parent = getattr(node, "_parent", None)
    while parent is not fn:
        if isinstance(parent, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            if any(name == node.id for gen in parent.generators for name, _ in _target_bindings(gen.target)):
                return None
        parent = getattr(parent, "_parent", None)
    bindings = _binding_index(fn).get(node.id, [])
    position = _binding_position(node)
    branches = _if_branches(node, fn)
    end = bisect_right(bindings, position, key=lambda binding: binding[0])
    # Stable ordering also preserves left-to-right binding of repeated names in one unpacking assignment.
    for index in range(end - 1, -1, -1):
        _, value, assigned_in = bindings[index]
        definite = True
        for branch, arm in assigned_in.items():
            current = branches.get(branch)
            if isinstance(branch, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                if arm == "body":
                    # else proves the try body completed; a handler/finally/join does not.
                    definite &= current in ("body", "else")
                elif (current is not None and current != "finally" and current != arm
                      and not isinstance(branch, getattr(ast, "TryStar", ()))):
                    break  # Separate handlers and else are mutually exclusive.
                else:
                    definite &= current == arm
            elif isinstance(branch, (ast.For, ast.AsyncFor, ast.While)):
                # A loop's else suite can follow zero iterations or an interrupted body.
                definite &= current == arm
            elif current is not None and (current < arm if isinstance(branch, ast.BoolOp) else current != arm):
                break  # This binding is in a mutually exclusive arm.
            else:
                definite &= current is not None
        else:
            return value if definite else None
    return None


def _query_path_source(node):
    """The first split/partition element keeps the URL path when removing query or fragment text."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"split", "partition"} and node.args
            and _str(node.args[0]) in {"?", "#"}):
        return node.func.value
    return None


def _request_path(node) -> bool:
    """Recognize direct request paths and same-function aliases, never translated filesystem paths."""
    if isinstance(node, ast.Name):
        return _request_path(_assigned_value(node))
    if isinstance(node, ast.NamedExpr):
        return _request_path(node.value)
    if isinstance(node, ast.Subscript) and _literal(node.slice) == 0:
        return _request_path(_query_path_source(node.value))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"strip", "rstrip", "lstrip"}):
        return _request_path(node.func.value)
    if isinstance(node, ast.Attribute) and node.attr in {"path", "path_info"}:
        if isinstance(node.value, ast.Name):
            return node.value.id in {"self", "request", "req"}
        return (isinstance(node.value, ast.Call) and _call_name(node.value) in {"urlsplit", "urlparse"}
                and bool(node.value.args) and _request_path(node.value.args[0]))
    return False


def _request_segments(node) -> bool:
    if isinstance(node, ast.Name):
        return _request_segments(_assigned_value(node))
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "split" and node.args and _str(node.args[0]) == "/"
            and _request_path(node.func.value))


def _route_table(node) -> bool:
    """Only explicit route-table bindings authorize filesystem-like route names."""
    if _str(node.elts[0]) not in HTTP_METHODS:
        return False
    parent = getattr(node, "_parent", None)
    while isinstance(parent, (ast.List, ast.Tuple, ast.Dict)):
        parent = getattr(parent, "_parent", None)
    targets = (parent.targets if isinstance(parent, ast.Assign) else [parent.target]
               if isinstance(parent, ast.AnnAssign) else [])
    return any(isinstance(t, ast.Name) and t.id.lower() in {"routes", "route_table", "urlpatterns"} for t in targets)


def _compares_path(node) -> bool:
    """True for a string compared with the request path (==, match/case, startswith, re.match): a declared route.
    False for one in a container or passed to a generic call, where it may be a filesystem path."""
    p = getattr(node, "_parent", None)
    if isinstance(p, ast.Call):
        if _call_name(p) == "startswith" and isinstance(p.func, ast.Attribute):
            return node in p.args and _request_path(p.func.value)
        if _call_name(p) in RE_FUNCS and p.args and node is p.args[0]:
            subject = p.args[1] if len(p.args) > 1 else next((k.value for k in p.keywords if k.arg == "string"), None)
            return _request_path(subject)
    if isinstance(p, ast.Compare):
        return any(other is not node and _request_path(other) for other in (p.left, *p.comparators))
    if isinstance(p, ast.MatchValue):
        match = getattr(getattr(p, "_parent", None), "_parent", None)
        return isinstance(match, ast.Match) and _request_path(match.subject)
    return False


def _registered_argparse_actions(tree):
    """Resolve preceding parser.register('action', alias, argparse_action) calls without running repo code."""
    imports, registrations, options = {}, {}, {}
    nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom, ast.Call))]
    if not any(isinstance(node, ast.Call) and _call_name(node) == "register" for node in nodes):
        return options
    # Obtain the implementation classes from argparse's own built-in action registry, rather
    # than assuming a user-chosen registration string is a built-in action/class name.
    builtin = argparse.ArgumentParser(add_help=False)
    no_value_classes = {builtin._registry_get("action", name).__name__ for name in NO_VALUE_ACTIONS
                        if builtin._registry_get("action", name) is not None}
    no_value_classes.add(argparse.BooleanOptionalAction.__name__)
    no_value_classes = {"argparse." + name for name in no_value_classes}
    for node in sorted(nodes, key=lambda n: (n.lineno, n.col_offset)):
        scope = _function(node)
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[scope, alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imports[scope, alias.asname or alias.name] = node.module + "." + alias.name
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)):
            continue
        key = scope, node.func.value.id
        if node.func.attr == "register" and len(node.args) >= 3 and _str(node.args[0]) == "action":
            alias, action = _str(node.args[1]), node.args[2]
            if not alias:
                continue
            base = action.value if isinstance(action, ast.Attribute) else action
            imported = (imports.get((scope, base.id), imports.get((None, base.id), ""))
                        if isinstance(base, ast.Name) else "")
            qualified = imported + "." + action.attr if isinstance(action, ast.Attribute) else imported
            registrations.setdefault(key, {})[alias] = qualified not in no_value_classes
        elif node.func.attr == "add_argument":
            options[node] = registrations.get(key, {}).copy()
    return options


def _takes_value(keywords: list[ast.keyword], default, names: list[str], registered=None) -> bool:
    """Whether an argparse or click option consumes the next token. argparse supplies the default of store_true and
    the other no-value actions, so read action= (or nargs=0) and click's is_flag=, count=, flag_value= and
    --on/--off names instead of the default. A custom action class is opaque: guess from a bool default."""
    kw = {k.arg: k.value for k in keywords if k.arg}
    if any(_literal(kw[k]) is True for k in ("is_flag", "count") if k in kw):
        return False
    if "flag_value" in kw or any(n.startswith("-") and "/" in n for n in names):
        return False
    if "nargs" in kw:
        return _literal(kw["nargs"]) != 0
    action = kw.get("action")
    if action is None:
        return True
    if isinstance(action, ast.Constant):
        if registered is not None and action.value in registered:
            return registered[action.value]
        return action.value not in NO_VALUE_ACTIONS  # store, append and extend take a value
    if (action.attr if isinstance(action, ast.Attribute) else getattr(action, "id", None)) in NO_VALUE_ACTIONS:
        return False  # argparse.BooleanOptionalAction
    return not isinstance(default, bool)


def _enclosing_boolop(node):
    cur = getattr(node, "_parent", None)
    while cur is not None and not isinstance(cur, ast.stmt):
        if isinstance(cur, ast.BoolOp) and isinstance(cur.op, ast.And):
            top = cur
            nxt = getattr(cur, "_parent", None)
            while isinstance(nxt, ast.BoolOp) and isinstance(nxt.op, ast.And):
                top, nxt = nxt, getattr(nxt, "_parent", None)
            return top
        cur = getattr(cur, "_parent", None)
    return None


def _segment_route(test: ast.AST) -> tuple[str, bool] | None:
    """`parts == ["todos"]` or `len(parts) == 3 and parts[0] == "todos" and parts[2] == "done"`."""
    segs: dict[int, str] = {}
    length = None
    sources = []
    conds = test.values if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And) else [test]
    for c in conds:
        if not (isinstance(c, ast.Compare) and len(c.ops) == 1 and isinstance(c.ops[0], ast.Eq)):
            continue
        left, right = c.left, c.comparators[0]
        lit = _literal(right)
        if isinstance(lit, list | tuple) and lit and all(isinstance(x, str) for x in lit) and isinstance(left, ast.Name):
            if all(re.fullmatch(r"[\w.-]+", x) for x in lit):
                return "/" + "/".join(lit), _request_segments(left)
        if isinstance(left, ast.Subscript) and isinstance(right, ast.Constant) and isinstance(right.value, str):
            idx = _literal(left.slice)
            if isinstance(idx, int) and idx >= 0 and re.fullmatch(r"[\w.-]+", right.value):
                segs[idx] = right.value
                sources.append(left.value)
        if (isinstance(left, ast.Call) and _call_name(left) == "len" and isinstance(right, ast.Constant)
                and isinstance(right.value, int)):
            length = right.value
    if not segs or 0 not in segs:
        return None
    n = length or (max(segs) + 1)
    return "/" + "/".join(segs.get(i, "<id>") for i in range(n)), all(_request_segments(s) for s in sources)


def _body_keys(fn) -> list[str]:
    """JSON body fields a handler reads: body = json.loads(...); body.get("title") / body["title"] / request.json[...]."""
    names = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and _call_name(n.value) in ("loads", "load", "get_json"):
            names.update(t.id for t in n.targets if isinstance(t, ast.Name))

    def is_body(base) -> bool:
        return (isinstance(base, ast.Name) and base.id in names) or (isinstance(base, ast.Attribute) and base.attr == "json")

    keys: list[str] = []
    for n in ast.walk(fn):
        k = None
        if isinstance(n, ast.Call) and _call_name(n) == "get" and isinstance(n.func, ast.Attribute) and is_body(n.func.value):
            k = _str(n.args[0]) if n.args else None
        elif isinstance(n, ast.Subscript) and is_body(n.value):
            k = _str(n.slice)
        if k and re.fullmatch(r"[A-Za-z_][\w-]*", k) and k not in keys:
            keys.append(k)
    return keys


def analyze_python(rel: str, text: str) -> dict:
    info = {"routes": [], "subcommands": [], "parser_options": {}, "ports": [], "env": [], "main_guard": False,
            "server": bool(SERVER_HINT.search(text)), "imports": set(), "notes": []}
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as e:
        info["notes"].append(f"{rel}: not parsed ({e.__class__.__name__})")
        return info
    _parents(tree)
    registered_actions = _registered_argparse_actions(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            info["imports"].update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            info["imports"].add(node.module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and re.fullmatch(r"do_[A-Z]+", node.name):
            info["server"] = True

    def add_route(method: str, path: str | None, node, how: str, body: list[str] | None = None) -> None:
        if path:
            r = {"method": method, "path": path, "source": f"{rel}:{getattr(node, 'lineno', 0)}", "how": how}
            if body and method in ("POST", "PUT", "PATCH", "ANY"):
                r["body_keys"] = body
            info["routes"].append(r)

    for node in ast.walk(tree):
        # main guard
        if isinstance(node, ast.If) and isinstance(getattr(node, "_parent", None), ast.Module):
            t = node.test
            if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "__name__"
                    and any(_str(c) == "__main__" for c in t.comparators)):
                info["main_guard"] = True
        # decorators: Flask / FastAPI / click / typer
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            click_opts = []
            for d in node.decorator_list:
                if not isinstance(d, ast.Call):
                    continue
                name = _call_name(d)
                first = _str(d.args[0]) if d.args else None
                if name in ROUTE_DECOS and first and first.startswith("/"):
                    if name in ("route", "api_route"):
                        ms = next((_literal(k.value) for k in d.keywords if k.arg == "methods"), None) or ["GET"]
                    else:
                        ms = [name.upper()] if name.upper() in HTTP_METHODS else ["GET"]
                    for m in ms:
                        add_route(str(m).upper(), normalize_route(first, declared=True), d, "decorator", _body_keys(node))
                if name in ("option", "argument") and isinstance(d.func, ast.Attribute):
                    names = [s for s in (_str(a) for a in d.args) if s]
                    dflt = next((_literal(k.value) for k in d.keywords if k.arg == "default"), None)
                    click_opts.append({"names": names, "default": dflt, "positional": bool(names) and not names[0].startswith("-"),
                                       "takes_value": _takes_value(d.keywords, dflt, names)})
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and _call_name(d) in ("command", "group") and isinstance(d.func, ast.Attribute):
                    if not ({"click", "typer"} & {i.split(".")[0] for i in info["imports"]}):
                        continue
                    cname = (_str(d.args[0]) if d.args else None) or next(
                        (_str(k.value) for k in d.keywords if k.arg == "name"), None) or node.name.replace("_", "-")
                    if _call_name(d) == "command":
                        info["subcommands"].append({"name": cname, "line": node.lineno, "options": click_opts,
                                                    "via": "click"})
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        # Django path("todos/<int:pk>/", view)
        if name in ("path", "re_path") and rel.endswith("urls.py") and node.args:
            s = _str(node.args[0])
            if s is not None:
                add_route("ANY", normalize_route("/" + s.lstrip("^/"), "regex" if name == "re_path" else "eq", declared=True),
                          node, "django")
        # argparse
        if name == "add_parser" and node.args and _str(node.args[0]):
            p = getattr(node, "_parent", None)
            var = p.targets[0].id if isinstance(p, ast.Assign) and isinstance(p.targets[0], ast.Name) else None
            aliases = next((_literal(k.value) for k in node.keywords if k.arg == "aliases"), None) or []
            info["subcommands"].append({"name": _str(node.args[0]), "line": node.lineno, "var": var,
                                        "aliases": list(aliases), "via": "argparse"})
        if name == "add_argument" and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            names = [s for s in (_str(a) for a in node.args) if s]
            if names:
                dflt = next((_literal(k.value) for k in node.keywords if k.arg == "default"), None)
                info["parser_options"].setdefault(node.func.value.id, []).append(
                    {"names": names, "default": dflt, "positional": not names[0].startswith("-"),
                     "takes_value": _takes_value(node.keywords, dflt, names, registered_actions.get(node))})
        # ports
        if name in ("HTTPServer", "ThreadingHTTPServer", "TCPServer", "ThreadingTCPServer", "make_server") and node.args:
            addr = _literal(node.args[0])
            if isinstance(addr, tuple) and len(addr) == 2 and isinstance(addr[1], int) and addr[1] > 0:
                info["ports"].append({"port": addr[1], "source": f"{rel}:{node.lineno}"})
            if name == "make_server" and len(node.args) >= 2 and isinstance(_literal(node.args[1]), int):
                info["ports"].append({"port": _literal(node.args[1]), "source": f"{rel}:{node.lineno}"})
        for k in node.keywords:
            if k.arg == "port" and isinstance(_literal(k.value), int):
                info["ports"].append({"port": _literal(k.value), "source": f"{rel}:{node.lineno}"})
        # env vars
        if name in ("getenv", "get") and node.args and _str(node.args[0]):
            f = node.func
            is_env = (name == "getenv") or (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute)
                                            and f.value.attr == "environ")
            if is_env and re.fullmatch(r"[A-Z][A-Z0-9_]*", _str(node.args[0])):
                dflt = _literal(node.args[1]) if len(node.args) > 1 else None
                info["env"].append({"name": _str(node.args[0]), "default": dflt, "source": f"{rel}:{node.lineno}"})
                if _str(node.args[0]) == "PORT" and str(dflt or "").isdigit():
                    info["ports"].append({"port": int(dflt), "source": f"{rel}:{node.lineno}", "env": "PORT"})
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "environ"
                and _str(node.slice) and re.fullmatch(r"[A-Z][A-Z0-9_]*", _str(node.slice))):
            info["env"].append({"name": _str(node.slice), "default": None, "source": f"{rel}:{node.lineno}"})

    # "GET /path" strings anywhere
    for node in ast.walk(tree):
        s = _str(node)
        m = re.fullmatch(r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S*)", s or "")
        if m:
            add_route(m.group(1), normalize_route(m.group(2), declared=True), node, "method-path string")

    if info["server"]:
        # route tables: ("GET", r"^/todos/(\d+)$", handler)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Tuple, ast.List)) and 2 <= len(node.elts) <= 6:
                ms = [_str(e) for e in node.elts if _str(e) in HTTP_METHODS]
                paths = []
                for e in node.elts:
                    s = _str(e)
                    if s is None and isinstance(e, ast.Call) and _call_name(e) in RE_FUNCS and e.args:
                        s = _str(e.args[0])
                    if s and s not in HTTP_METHODS:
                        paths.append(normalize_route(s, "regex" if re.search(r"[\^$()\\]", s) else "eq", declared=_route_table(node)))
                for m in ms[:1]:
                    for p in paths:
                        add_route(m, p, node, "route table")
        # handler code: constants inside functions
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = _body_keys(fn)
            for node in ast.walk(fn):
                if isinstance(node, ast.If):
                    seg = _segment_route(node.test)
                    if seg:
                        for m in _method_context(node.test) or ["ANY"]:
                            add_route(m, normalize_route(seg[0], declared=seg[1]), node, "path segments", body)
                s = _str(node)
                if not s or not (s.startswith("/") or s.startswith("^/") or s.startswith("\\A/")):
                    continue
                kind = _usage(node)
                if kind is None or kind == "suffix":
                    continue
                if isinstance(getattr(node, "_parent", None), (ast.Tuple, ast.List)) and \
                        any(_str(e) in HTTP_METHODS for e in node._parent.elts):
                    continue  # route table, handled above
                methods = _method_context(node) or ["ANY"]
                if kind == "prefix":
                    suffixes = []
                    bo = _enclosing_boolop(node)
                    if bo is not None:
                        for n2 in ast.walk(bo):
                            if _str(n2) and _usage(n2) == "suffix":
                                suffixes.append(_str(n2))
                    base = s.rstrip("/")
                    paths = [base + "/<*>" + "/" + x.lstrip("/") for x in suffixes] or [base + "/<*>"]
                else:
                    paths = [s]
                # Only filesystem-like paths need provenance. Normalize first so anchored regexes
                # receive the same filtering, then share this work across all methods and suffixes.
                paths = [normalize_route(p, kind, declared=True) for p in paths]
                declared = not any(p and FS_PREFIX.match(p) for p in paths) or _compares_path(node)
                paths = [p for p in paths if p and (declared or not FS_PREFIX.match(p))]
                for m in methods:
                    for p in paths:
                        add_route(m, p, node, "startswith" if kind == "prefix" else kind, body)
    return info


def python_prog(rel: str, files: set[str], scripts: dict[str, str]) -> tuple[str | None, list[str]]:
    """Canonical invocation for the program a Python file belongs to, plus aliases."""
    parts = rel.split("/")
    env = ""
    if parts[0] == "src" and len(parts) > 1:
        env, parts = "PYTHONPATH=src ", parts[1:]
    prefix = "src/" if env else ""
    module = ".".join(parts)[:-3]
    aliases = [name for name, target in scripts.items() if target.split(":")[0] in (module, module.rsplit(".", 1)[0])]
    pkg_parts = parts[:-1]
    for i in range(1, len(pkg_parts) + 1):
        pkg = "/".join(pkg_parts[:i])
        if f"{prefix}{pkg}/__main__.py" in files:
            mod = ".".join(pkg_parts[:i])
            return f"{env}python3 -m {mod}", aliases + [f"python -m {mod}"]
    if parts[-1] == "__main__.py" and pkg_parts:
        mod = ".".join(pkg_parts)
        return f"{env}python3 -m {mod}", aliases + [f"python -m {mod}"]
    return None, aliases


# ---------------------------------------------------------------- node / go / make

NODE_ROUTE = re.compile(r"""\b(?:app|router|server|api|routes|r|fastify|instance)\s*\.\s*(get|post|put|patch|delete|head|options|all)\s*\(\s*(['"`])(/[^'"`]*)\2""")
NODE_CLI = re.compile(r"""\.command\(\s*(['"])([\w:-]+)""")
NODE_PORTS = [re.compile(r"\.listen\(\s*(\d{2,5})\b"), re.compile(r"\bPORT\s*(?:\|\||\?\?)\s*['\"]?(\d{2,5})")]
NODE_ENV = re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)|process\.env\[['\"]([A-Z][A-Z0-9_]*)['\"]\]")
GO_HANDLE = re.compile(r"""\.(?:HandleFunc|Handle)\(\s*"([^"]+)\"""")
GO_ROUTER = re.compile(r"""\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Get|Post|Put|Patch|Delete|Head|Options)\(\s*"(/[^"]*)\"""")
GO_HANDLE_RECEIVERS = {'http', 'mux', 'router', 'r', 'server', 'app'}
GO_ROUTER_RECEIVERS = {'r', 'router', 'mux', 'app', 'engine', 'group', 'api', 'server'}
GO_PORTS = [re.compile(r"""ListenAndServe(?:TLS)?\(\s*"[^"]*:(\d{2,5})\""""), re.compile(r"""Addr:\s*"[^"]*:(\d{2,5})\""""),
            re.compile(r"""flag\.(?:Int|String)\(\s*"(?:port|addr)"\s*,\s*"?:?(\d{2,5})""")]
GO_FLAGS = re.compile(r"""flag\.(?:Int|String|Bool)(?:Var)?\([^"]*"([\w-]+)\"""")
GO_COBRA = re.compile(r"""Use:\s*"([\w-]+)""")
GO_ENV = re.compile(r"""os\.Getenv\("([A-Z][A-Z0-9_]*)"\)""")
MAKE_TARGET = re.compile(r"^([A-Za-z0-9][\w.-]*)\s*:(?!=)", re.M)


def go_route_receiver(text: str, dot: int, known: set[str]) -> bool:
    """Conventional receivers may bypass FS filtering; ordinary paths need no receiver whitelist."""
    start = max(0, dot - 256)
    before = text[start:dot]
    receiver = re.search(r'\b(\w+)\s*$', before)
    prefix = before[:receiver.start()].rstrip() if receiver else ""
    return bool(receiver and receiver.group(1) in known
                and (prefix or start == 0) and not prefix.endswith('.'))


# ---------------------------------------------------------------- features

def singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def route_action(method: str, path: str) -> tuple[str, str]:
    segs = [s for s in path.strip("/").split("/") if s]
    static = [s for s in segs if not s.startswith("<")]
    resource = static[0] if static else "root"
    if resource in ("api",) and len(static) > 1:
        resource = static[1]
        segs = segs[segs.index(resource):]
    has_param = any(s.startswith("<") for s in segs)
    tail = segs[-1] if segs else ""
    if len(segs) >= 3 and not tail.startswith("<"):
        verb = tail.lower()
        for cls, words in VERB_CLASSES.items():
            if verb in words:
                return cls, resource
        return verb, resource
    if method == "GET":
        return ("show" if has_param else "list"), resource
    if method == "POST":
        return ("create" if not has_param else "update"), resource
    if method in ("PUT", "PATCH"):
        return "update", resource
    if method == "DELETE":
        return "delete", resource
    return method.lower(), resource


def verb_action(name: str) -> str:
    n = name.lower()
    for cls, words in VERB_CLASSES.items():
        if n in words:
            return cls
    return n


def feature_title(action: str, resource: str) -> str:
    one, many = singular(resource), resource
    titles = {"create": f"Create a {one}", "list": f"List {many}", "show": f"Show a {one}",
              "update": f"Update a {one}", "delete": f"Delete a {one}", "done": f"Mark a {one} done"}
    if action in titles:
        return titles[action]
    if action in ("archive", "archived", "open", "close", "closed", "complete", "completed", "read", "unread"):
        return f"Mark a {one} {action}"
    return f"{action.replace('-', ' ').capitalize()} a {one}"


def group_features(routes: list[dict], commands: list[dict]) -> list[dict]:
    feats: dict[tuple[str, str], dict] = {}
    for r in routes:
        if r.get("health"):
            continue
        action, resource = route_action(r["method"] if r["method"] != "ANY" else "GET", r["path"])
        f = feats.setdefault((action, resource), {"action": action, "resource": resource, "routes": [], "commands": []})
        f["routes"].append(r)
    resources = sorted({k[1] for k in feats})
    for c in commands:
        if c.get("launch") or c["name"] in SKIP_COMMANDS:
            continue
        action = verb_action(c["name"])
        prog_word = (c.get("prog") or "").split()[-1].split(".")[-1] if c.get("prog") else ""
        if not re.search(r"[A-Za-z0-9]", prog_word) and c.get("aliases"):
            prog_word = c["aliases"][0]  # `cargo run ... --` style progs end in a separator, not a name
        same_action = [k for k in feats if k[0] == action]
        if len(same_action) == 1:
            key = same_action[0]
        elif same_action:
            key = next((k for k in same_action if singular(k[1]) == singular(prog_word)), same_action[0])
        else:
            res = next((r for r in resources if singular(r) == singular(prog_word)), None) or prog_word or "app"
            key = (action, res)
            feats.setdefault(key, {"action": action, "resource": res, "routes": [], "commands": []})
        feats[key]["commands"].append(c)
    out = []
    for (action, resource), f in feats.items():
        fid = f"{action}-{resource if action == 'list' else singular(resource)}"
        fid = re.sub(r"[^a-z0-9-]+", "-", fid.lower()).strip("-")
        title = feature_title(action, resource)
        if not f["routes"] and f["commands"] and action not in VERB_CLASSES:
            # a CLI verb with no resource behind it (`agent-garden doctor`): name the command, not "Doctor a agent-garden"
            fid = re.sub(r"[^a-z0-9-]+", "-", action.lower()).strip("-")
            title = f"Run `{resource} {action}`"
        out.append({"id": fid, "title": title, **f})
    out.sort(key=lambda f: (f["resource"], ACTION_ORDER.get(f["action"], 5), f["id"]))
    seen = Counter()
    for f in out:
        seen[f["id"]] += 1
        if seen[f["id"]] > 1:
            f["id"] = f"{f['id']}-{seen[f['id']]}"
    return out


# ---------------------------------------------------------------- scan

def frontmatter_name(text: str) -> str | None:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return None
    n = re.search(r"^name:\s*['\"]?([^'\"\n]+)", m.group(1), re.M)
    return n.group(1).strip() if n else None


RUST_SUBCMD_ENUM = re.compile(r"#\[derive\([^)]*\bSubcommand\b[^)]*\)\]\s*(?:#\[[^\]]*\]\s*)*(?:pub(?:\([^)]*\))?\s+)?enum\s+\w+\s*\{", re.S)
RUST_VARIANT = re.compile(r"^\s{4}(?:#\[command\([^)]*name\s*=\s*\"([^\"]+)\"[^)]*\)\]\s*)?([A-Z][A-Za-z0-9]*)\s*(?:[{(,]|$)", re.M)


def _kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def _enum_body(text: str, start: int) -> str:
    depth, i = 0, start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return text[start + 1:]


def rust_clap_commands(repo: Path, files: list[str], src_files: list[str], notes: list[str]) -> list[dict]:
    """clap `#[derive(Subcommand)]` enums -> CLI subcommands, invoked through `cargo run -q -p PKG --bin BIN --`."""
    out: list[dict] = []
    for manifest in (f for f in files if Path(f).name == "Cargo.toml"):
        try:
            cargo = tomllib.loads(read(repo, manifest)) if tomllib is not None else {}
        except tomllib.TOMLDecodeError:
            notes.append(f"{manifest} did not parse")
            continue
        pkg = (cargo.get("package") or {}).get("name")
        if not pkg:
            continue
        crate_dir = str(Path(manifest).parent)
        crate_dir = "" if crate_dir == "." else crate_dir + "/"
        bins = [(b.get("name") or pkg, b.get("path") or "src/main.rs") for b in cargo.get("bin") or []]
        if not bins and f"{crate_dir}src/main.rs" in set(files):
            bins = [(pkg, "src/main.rs")]
        for bin_name, _ in bins:
            rs = [f for f in src_files if f.startswith(crate_dir + "src/") and f.endswith(".rs")]
            for rel in rs:
                text = read(repo, rel)
                for m in RUST_SUBCMD_ENUM.finditer(text):
                    body = _enum_body(text, m.end() - 1)
                    base = m.end()
                    for v in RUST_VARIANT.finditer(body):
                        name = v.group(1) or _kebab(v.group(2))
                        out.append({"prog": f"cargo run -q -p {pkg} --bin {bin_name} --", "aliases": [bin_name],
                                    "name": name, "options": [], "global_options": [],
                                    "source": f"{rel}:{line_of(text, base + v.start())}", "via": "clap"})
    return out


def scan(repo: Path, max_files: int = 20000) -> dict:
    repo = repo.resolve()
    files = list_files(repo, max_files)
    fileset = set(files)
    res: dict = {"repo": str(repo), "languages": [], "markers": [], "build_commands": [], "test_commands": [],
                 "launch_candidates": [], "http_routes": [], "health_routes": [], "cli_commands": [], "env_vars": [],
                 "skill_dirs": [], "features": [], "notes": []}
    langs = Counter(LANG_EXT[Path(f).suffix] for f in files if Path(f).suffix in LANG_EXT)
    res["languages"] = [{"name": k, "files": v} for k, v in langs.most_common()]
    res["markers"] = [m for m in MARKERS if m in fileset]
    src_files = [f for f in files if not TEST_PATH.search(f)]

    routes: list[dict] = []
    commands: list[dict] = []
    ports: list[dict] = []
    env: list[dict] = []

    # ---- python
    scripts: dict[str, str] = {}
    if "pyproject.toml" in fileset and tomllib is not None:
        try:
            pp = tomllib.loads(read(repo, "pyproject.toml"))
            scripts.update(pp.get("project", {}).get("scripts", {}) or {})
        except (tomllib.TOMLDecodeError, AttributeError):
            res["notes"].append("pyproject.toml did not parse")
    py_infos = {}
    for rel in src_files:
        if rel.endswith(".py"):
            py_infos[rel] = analyze_python(rel, read(repo, rel))
    for rel, info in py_infos.items():
        res["notes"].extend(info["notes"])
        routes.extend(info["routes"])
        ports.extend(info["ports"])
        env.extend(info["env"])
        prog, aliases = python_prog(rel, fileset, scripts)
        if prog is None and info["main_guard"] and info["subcommands"]:
            prog = f"python3 {rel}"
        if prog is None and info["subcommands"]:
            prog = (aliases[0] if aliases else None)
        for sc in info["subcommands"]:
            opts = info["parser_options"].get(sc.get("var") or "", []) if sc.get("via") == "argparse" else sc.get("options", [])
            global_opts = []
            for var, olist in info["parser_options"].items():
                if var not in {s.get("var") for s in info["subcommands"]}:
                    global_opts.extend(o for o in olist if not o["positional"])
            commands.append({"prog": prog or "TODO(verify): how to invoke this CLI", "aliases": aliases,
                             "name": sc["name"], "options": opts, "global_options": global_opts,
                             "source": f"{rel}:{sc['line']}", "via": sc.get("via")})
            for o in opts:
                if any(n in PORT_FLAGS for n in o["names"]) and isinstance(o.get("default"), int):
                    ports.append({"port": o["default"], "source": f"{rel}:{sc['line']}", "flag": "--port"})
        if info["main_guard"] and info["server"] and not info["subcommands"]:
            p = prog or f"python3 {rel}"
            res["launch_candidates"].append({"kind": "server", "cmd": p, "source": rel, "port": None,
                                             "port_flag": None, "data_flag": None})
    # tests
    has_pytest = any(f.endswith(("pytest.ini", "conftest.py")) for f in files) or \
        "[tool.pytest" in read(repo, "pyproject.toml") or \
        any("pytest" in read(repo, f) for f in files if re.fullmatch(r"requirements[\w.-]*\.txt", Path(f).name))
    py_tests = [f for f in files if f.endswith(".py") and re.search(r"(^|/)test_[^/]*\.py$|_test\.py$", f)]
    if has_pytest:
        res["test_commands"].append({"cmd": "python3 -m pytest", "source": "pytest config"})
    elif py_tests:
        dirs = Counter(f.split("/")[0] for f in py_tests if "/" in f)
        tdir = dirs.most_common(1)[0][0] if dirs else None
        if tdir and f"{tdir}/__init__.py" not in fileset:
            res["test_commands"].append({"cmd": f"python3 -m unittest discover -s {tdir}", "source": f"{tdir}/"})
        else:
            res["test_commands"].append({"cmd": "python3 -m unittest", "source": ", ".join(py_tests[:3])})

    # ---- node
    if "package.json" in fileset:
        try:
            pkg = json.loads(read(repo, "package.json"))
        except json.JSONDecodeError:
            pkg = {}
            res["notes"].append("package.json did not parse")
        pm = "pnpm" if "pnpm-lock.yaml" in fileset else "yarn" if "yarn.lock" in fileset else "npm"
        run = (lambda s: f"{pm} {s}" if s in ("test", "start") else f"{pm} run {s}")
        sc = pkg.get("scripts") or {}
        if sc.get("test") and "no test specified" not in sc["test"]:
            res["test_commands"].append({"cmd": run("test"), "source": "package.json scripts.test"})
        if sc.get("build"):
            res["build_commands"].append({"cmd": run("build"), "source": "package.json scripts.build"})
        for s in ("start", "dev", "serve", "server"):
            if s in sc:
                res["launch_candidates"].append({"kind": "server", "cmd": run(s), "source": f"package.json scripts.{s}",
                                                 "port": None, "port_flag": None, "data_flag": None})
        bins = pkg.get("bin")
        bin_names = [pkg.get("name", "cli")] if isinstance(bins, str) else list((bins or {}).keys())
        for rel in src_files:
            if not rel.endswith((".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")):
                continue
            text = read(repo, rel)
            for m in NODE_ROUTE.finditer(text):
                method = m.group(1).upper()
                routes.append({"method": "ANY" if method == "ALL" else method,
                               "path": normalize_route(m.group(3), declared=True),
                               "source": f"{rel}:{line_of(text, m.start())}", "how": "node router"})
            for m in NODE_CLI.finditer(text):
                commands.append({"prog": (f"npx {bin_names[0]}" if bin_names else f"node {rel}"), "aliases": bin_names,
                                 "name": m.group(2), "options": [], "global_options": [],
                                 "source": f"{rel}:{line_of(text, m.start())}", "via": "commander/yargs"})
            for pat in NODE_PORTS:
                for m in pat.finditer(text):
                    ports.append({"port": int(m.group(1)), "source": f"{rel}:{line_of(text, m.start())}"})
            for m in NODE_ENV.finditer(text):
                env.append({"name": m.group(1) or m.group(2), "default": None,
                            "source": f"{rel}:{line_of(text, m.start())}"})

    # ---- go
    go_files = [f for f in src_files if f.endswith(".go")]
    if "go.mod" in fileset or go_files:
        res["build_commands"].append({"cmd": "go build ./...", "source": "go.mod"})
        if any(f.endswith("_test.go") for f in files):
            res["test_commands"].append({"cmd": "go test ./...", "source": "*_test.go"})
        main_dirs = set()
        for rel in go_files:
            text = read(repo, rel)
            if re.search(r"^package main\b", text, re.M):
                main_dirs.add(str(Path(rel).parent))
            for m in GO_HANDLE.finditer(text):
                raw = m.group(1)
                mm_ = re.fullmatch(r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)", raw)
                method, path = (mm_.group(1), mm_.group(2)) if mm_ else ("ANY", raw)
                if path.endswith("/") and len(path) > 1:
                    path = path + "<*>"
                path = normalize_route(path, declared=go_route_receiver(text, m.start(), GO_HANDLE_RECEIVERS))
                if path is not None:
                    routes.append({"method": method, "path": path,
                                   "source": f"{rel}:{line_of(text, m.start())}", "how": "net/http"})
            for m in GO_ROUTER.finditer(text):
                path = normalize_route(m.group(2), declared=go_route_receiver(text, m.start(), GO_ROUTER_RECEIVERS))
                if path is not None:
                    routes.append({"method": m.group(1).upper(), "path": path,
                                   "source": f"{rel}:{line_of(text, m.start())}", "how": "go router"})
            for pat in GO_PORTS:
                for m in pat.finditer(text):
                    ports.append({"port": int(m.group(1)), "source": f"{rel}:{line_of(text, m.start())}"})
            for m in GO_COBRA.finditer(text):
                commands.append({"prog": "go run .", "aliases": [], "name": m.group(1), "options": [],
                                 "global_options": [], "source": f"{rel}:{line_of(text, m.start())}", "via": "cobra"})
            for m in GO_ENV.finditer(text):
                env.append({"name": m.group(1), "default": None, "source": f"{rel}:{line_of(text, m.start())}"})
        for d in sorted(main_dirs):
            target = "." if d == "." else f"./{d}"
            flags = set()
            for rel in go_files:
                if str(Path(rel).parent) == d:
                    flags.update(GO_FLAGS.findall(read(repo, rel)))
            res["launch_candidates"].append({"kind": "server" if any(r["how"].startswith("go") or r["how"] == "net/http"
                                                                     for r in routes) else "cli",
                                             "cmd": f"go run {target}", "source": f"{d}/ (package main)", "port": None,
                                             "port_flag": "-port" if "port" in flags else ("-addr" if "addr" in flags else None),
                                             "data_flag": next((f"-{f}" for f in ("data", "data-dir", "db", "dir") if f in flags), None)})

    # ---- make
    if "Makefile" in fileset:
        targets = [t for t in MAKE_TARGET.findall(read(repo, "Makefile")) if not t.startswith(".")]
        for t in dict.fromkeys(targets):
            if t in ("test", "check", "tests"):
                res["test_commands"].append({"cmd": f"make {t}", "source": "Makefile"})
            elif t in ("build", "all", "compile"):
                res["build_commands"].append({"cmd": f"make {t}", "source": "Makefile"})
            elif t in ("run", "serve", "start", "dev", "up", "server"):
                res["launch_candidates"].append({"kind": "server", "cmd": f"make {t}", "source": "Makefile", "port": None,
                                                 "port_flag": None, "data_flag": None})
    if "Cargo.toml" in fileset:
        res["build_commands"].append({"cmd": "cargo build", "source": "Cargo.toml"})
        res["test_commands"].append({"cmd": "cargo test", "source": "Cargo.toml"})
        commands.extend(rust_clap_commands(repo, files, src_files, res["notes"]))

    # ---- routes: dedupe, health
    seen = {}
    for r in routes:
        if not r.get("path"):
            continue
        k = route_key(r["method"], r["path"])
        if k not in seen:
            r["health"] = is_health(r["path"])
            seen[k] = r
    # drop ANY routes when a concrete method covers the same path
    concrete = {k[1] for k in seen if k[0] != "ANY"}
    res["http_routes"] = sorted((r for k, r in seen.items() if not (k[0] == "ANY" and k[1] in concrete)),
                                key=lambda r: (r["path"], HTTP_METHODS.index(r["method"]) if r["method"] in HTTP_METHODS else 9))
    res["health_routes"] = [r for r in res["http_routes"] if r["health"]]

    # ---- commands: dedupe, launch detection
    cseen = {}
    for c in commands:
        k = (c["prog"], c["name"])
        if k in cseen:
            continue
        sub_flags = {n for o in c["options"] for n in o["names"]}
        flags = sub_flags | {n for o in c["global_options"] for n in o["names"]}
        port_flag = next((f for f in PORT_FLAGS if f in flags), None)
        data_flag = next((f for f in DATA_FLAGS if f in flags), None)
        c["port_flag"], c["data_flag"] = port_flag, data_flag
        # a global flag goes before the subcommand: `prog --data DIR add ...`
        c["port_flag_global"] = bool(port_flag) and port_flag not in sub_flags
        c["data_flag_global"] = bool(data_flag) and data_flag not in sub_flags
        c["positionals"] = [o["names"][0] for o in c["options"] if o["positional"]]
        c["launch"] = bool(port_flag) or (c["name"] in SERVE_NAMES and bool(routes))
        cseen[k] = c
    res["cli_commands"] = sorted(cseen.values(), key=lambda c: (c["prog"], c["name"]))
    for c in res["cli_commands"]:
        if c["launch"]:
            port = next((p["port"] for p in ports if p.get("flag") and p["source"].split(":")[0] == c["source"].split(":")[0]),
                        None) or next((p["port"] for p in ports), None)
            res["launch_candidates"].insert(0, {"kind": "server", "cmd": f"{c['prog']} {c['name']}", "port": port,
                                                "prog": c["prog"], "subcommand": c["name"],
                                                "port_flag": c["port_flag"], "data_flag": c["data_flag"],
                                                "port_flag_global": c["port_flag_global"],
                                                "data_flag_global": c["data_flag_global"], "source": c["source"]})
    progs = {}
    for c in res["cli_commands"]:
        if not c["launch"]:
            progs.setdefault(c["prog"], {"kind": "cli", "cmd": c["prog"], "subcommands": [], "source": c["source"],
                                         "port": None, "port_flag": None, "data_flag": c["data_flag"],
                                         "data_flag_global": c["data_flag_global"]})
            progs[c["prog"]]["subcommands"].append(c["name"])
    res["launch_candidates"].extend(progs.values())
    for lc in res["launch_candidates"]:
        if lc["kind"] == "server" and lc.get("port") is None and ports:
            lc["port"] = ports[0]["port"]
    res["ports"] = ports[:20]
    envseen = {}
    for e in env:
        envseen.setdefault(e["name"], e)
    res["env_vars"] = sorted(envseen.values(), key=lambda e: e["name"])

    # ---- skills already in the repo
    for root in SKILL_ROOTS:
        base = repo / root
        if not base.is_dir():
            continue
        for d in sorted(p for p in base.iterdir() if (p / "SKILL.md").is_file()):
            text = (d / "SKILL.md").read_text(encoding="utf-8", errors="replace")
            verify_like = bool(re.search(r"^##\s+Launch", text, re.M) and re.search(r"^##\s+Drive", text, re.M))
            res["skill_dirs"].append({"path": d.relative_to(repo).as_posix(), "name": frontmatter_name(text),
                                      "verify_like": verify_like, "has_feature_map": (d / "features" / "README.md").is_file()})

    res["features"] = group_features(res["http_routes"], res["cli_commands"])
    return res


def print_text(r: dict) -> None:
    print(f"repo: {r['repo']}")
    print("languages: " + (", ".join(f"{l['name']} ({l['files']})" for l in r["languages"]) or "none detected"))
    print("markers: " + (", ".join(r["markers"]) or "none"))
    for key, label in (("build_commands", "build"), ("test_commands", "test")):
        print(f"{label}: " + ("; ".join(c["cmd"] for c in r[key]) or "none found"))
    print("launch candidates:")
    for lc in r["launch_candidates"] or [{"kind": "-", "cmd": "none found", "source": ""}]:
        extra = []
        if lc.get("port"):
            extra.append(f"port {lc['port']}")
        if lc.get("port_flag"):
            extra.append(f"port flag {lc['port_flag']}")
        if lc.get("data_flag"):
            extra.append(f"data flag {lc['data_flag']}")
        if lc.get("subcommands"):
            extra.append("subcommands " + ", ".join(lc["subcommands"]))
        print(f"  [{lc['kind']}] {lc['cmd']}" + (f"  ({'; '.join(extra)})" if extra else "") + f"  <- {lc.get('source', '')}")
    print(f"http routes ({len(r['http_routes'])}):")
    for rt in r["http_routes"]:
        print(f"  {rt['method']:<7} {rt['path']:<32} {'[health] ' if rt['health'] else ''}<- {rt['source']} ({rt['how']})")
    print(f"cli commands ({len(r['cli_commands'])}):")
    for c in r["cli_commands"]:
        args = " ".join(f"<{p}>" for p in c["positionals"])
        print(f"  {c['prog']} {c['name']} {args}".rstrip() + f"{'  [launch]' if c['launch'] else ''}  <- {c['source']}")
    if r["env_vars"]:
        print("env vars: " + ", ".join(e["name"] + (f"={e['default']}" if e.get("default") not in (None, "") else "")
                                       for e in r["env_vars"]))
    print("existing skills: " + (", ".join(f"{s['path']}{' (verify)' if s['verify_like'] else ''}"
                                           for s in r["skill_dirs"]) or "none"))
    print(f"proposed features ({len(r['features'])}):")
    for f in r["features"]:
        entry = [f"{x['method']} {x['path']}" for x in f["routes"]] + [f"{c['prog']} {c['name']}" for c in f["commands"]]
        print(f"  {f['id']:<22} {f['title']:<26} " + "; ".join(entry))
    for n in r["notes"]:
        print(f"note: {n}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="scan_repo.py",
        description="Inventory a repo for a verification skill: languages, build/test commands, launch candidates "
                    "(servers and CLIs with ports), HTTP routes, CLI subcommands, env vars, existing skill dirs, "
                    "and a proposed feature grouping. Reads files only; never runs repo code.",
        epilog="examples:\n  scan_repo.py .\n  scan_repo.py ../todo-app --json > inventory.json\n\n"
               "Heuristics cover Python (AST), Node, Go, and Make. Anything missed becomes a TODO(verify)\n"
               "marker when scaffold.py writes the skill.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("repo", type=Path, help="repository root")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--max-files", type=int, default=20000, help="cap on files read (default 20000)")
    args = ap.parse_args(argv)
    if not args.repo.is_dir():
        print(f"scan_repo: {args.repo} is not a directory", file=sys.stderr)
        return 2
    r = scan(args.repo, args.max_files)
    if args.json:
        print(json.dumps(r, indent=2, default=lambda o: sorted(o) if isinstance(o, set) else str(o)))
    else:
        print_text(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
