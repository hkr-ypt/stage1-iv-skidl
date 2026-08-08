#!/usr/bin/env python3
"""
resolve_kicad_libs.py — read-only resolver for the *installed* KiCad libraries.
==============================================================================

This module is the project's single source of truth for KiCad symbol and
footprint identifiers.  Nothing in this repository may assert a pin number, a
symbol name, or a footprint name from memory: every such value must be READ
from an installed library file through this resolver, and every answer it
returns carries the file path and line number it was read from.

WHAT IT DOES
------------
  * locates the installed KiCad symbol / footprint library trees
  * parses ``*.kicad_sym`` and enumerates ``(library_nickname, symbol_name)``
  * extracts every ``(pin_number, pin_name, electrical_type)`` of a symbol,
    following ``(extends ...)`` derivations to the base symbol
  * parses ``*.pretty/*.kicad_mod`` and enumerates
    ``(library_nickname, footprint_name, [pad_names])``
  * exposes a queryable Python API and a CLI that dumps JSON

LIBRARY RESOLUTION ORDER
------------------------
  1. environment variables, highest KiCad version first::

         KICAD9_SYMBOL_DIR / KICAD9_FOOTPRINT_DIR
         KICAD8_SYMBOL_DIR / KICAD8_FOOTPRINT_DIR
         KICAD7_SYMBOL_DIR / KICAD7_FOOTPRINT_DIR

  2. the project config file ``kicad_libs.json`` (repository root, or the path
     in ``$STAGE1_IV_KICAD_CONFIG``), keys ``symbol_dir`` / ``footprint_dir``

  3. per-platform default install locations

If none of those resolve to a directory that actually contains libraries, the
CLI exits **non-zero** with an actionable message and the API raises
:class:`LibraryNotFound`.  There is deliberately **no** guessing fallback.

CLI
---
::

    python3 tools/resolve_kicad_libs.py --paths
    python3 tools/resolve_kicad_libs.py --symbol Device:C
    python3 tools/resolve_kicad_libs.py --find-symbol 'INA128*'
    python3 tools/resolve_kicad_libs.py --footprint Package_SO:VSSOP-8_3x3mm_P0.65mm
    python3 tools/resolve_kicad_libs.py --find-footprint '*TO-220-7*'
    python3 tools/resolve_kicad_libs.py --dump-json libs.json
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import platform
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# Project-local symbol library (symbols with no upstream KiCad equivalent).
PROJECT_SYMBOL_DIR = os.path.join(REPO_ROOT, "libraries")


class LibraryNotFound(RuntimeError):
    """Raised when no installed KiCad library tree can be located."""


# =====================================================================
#  S-expression tokenizer / parser (line-number preserving)
# =====================================================================

_TOKEN_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"'      # quoted string
    r"|\("                    # open
    r"|\)"                    # close
    r"|[^\s()\"]+"            # bare atom
)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}

# Cheap symbol-name index (see KicadLibraries.symbol_names).
_SYMBOL_NAME_RE = re.compile(r'\(\s*symbol\s+("(?:[^"\\]|\\.)*")')
_UNIT_SUFFIX_RE = re.compile(r"^(.*)_\d+_\d+$")


class SexpList(list):
    """A parsed s-expression node that remembers the line it started on."""

    __slots__ = ("line",)

    def __init__(self, iterable=(), line=0):
        super().__init__(iterable)
        self.line = line


class Atom(str):
    """A parsed atom that remembers its line and whether it was quoted."""

    __slots__ = ("line", "quoted")

    def __new__(cls, value, line=0, quoted=False):
        obj = super().__new__(cls, value)
        obj.line = line
        obj.quoted = quoted
        return obj


def _unquote(tok: str) -> str:
    body = tok[1:-1]
    if "\\" not in body:
        return body
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            i += 1
            out.append(_ESCAPES.get(body[i], body[i]))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def parse_sexp(text: str) -> SexpList:
    """Parse an s-expression document into nested :class:`SexpList` nodes."""
    root = SexpList(line=1)
    stack = [root]
    line = 1
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        line += text.count("\n", pos, m.start())
        pos = m.start()
        tok = m.group(0)
        if tok == "(":
            node = SexpList(line=line)
            stack[-1].append(node)
            stack.append(node)
        elif tok == ")":
            if len(stack) == 1:
                raise ValueError(f"unbalanced ')' at line {line}")
            stack.pop()
        elif tok[0] == '"':
            stack[-1].append(Atom(_unquote(tok), line, True))
        else:
            stack[-1].append(Atom(tok, line, False))
    return root


def _head(node) -> str:
    return node[0] if isinstance(node, SexpList) and node and isinstance(node[0], str) else ""


def _children(node, head):
    for child in node:
        if isinstance(child, SexpList) and _head(child) == head:
            yield child


def _first(node, head):
    for child in _children(node, head):
        return child
    return None


# =====================================================================
#  Data records
# =====================================================================

class SymbolPin:
    __slots__ = ("number", "name", "etype", "style", "unit", "file", "line")

    def __init__(self, number, name, etype, style, unit, file, line):
        self.number = number
        self.name = name
        self.etype = etype
        self.style = style
        self.unit = unit
        self.file = file
        self.line = line

    def as_dict(self):
        return {
            "number": self.number, "name": self.name, "type": self.etype,
            "style": self.style, "unit": self.unit,
            "source": f"{self.file}:{self.line}",
        }

    def __repr__(self):
        return f"<Pin {self.number} {self.name!r} {self.etype}>"


class Symbol:
    __slots__ = ("lib", "name", "file", "line", "pins", "properties",
                 "extends", "units")

    def __init__(self, lib, name, file, line):
        self.lib = lib
        self.name = name
        self.file = file
        self.line = line
        self.pins = []
        self.properties = {}
        self.extends = None
        self.units = set()

    @property
    def lib_id(self):
        return f"{self.lib}:{self.name}"

    @property
    def pin_numbers(self):
        return {p.number for p in self.pins}

    def pin_by_number(self, number):
        for p in self.pins:
            if p.number == number:
                return p
        return None

    def pins_by_name(self, name):
        return [p for p in self.pins if p.name == name]

    def as_dict(self):
        return {
            "lib_id": self.lib_id, "lib": self.lib, "name": self.name,
            "source": f"{self.file}:{self.line}",
            "extends": self.extends,
            "properties": self.properties,
            "units": sorted(self.units),
            "pins": [p.as_dict() for p in self.pins],
        }

    def __repr__(self):
        return f"<Symbol {self.lib_id} ({len(self.pins)} pins)>"


class Footprint:
    __slots__ = ("lib", "name", "file", "line", "pads", "descr", "tags")

    def __init__(self, lib, name, file, line):
        self.lib = lib
        self.name = name
        self.file = file
        self.line = line
        self.pads = []          # list of (pad_name, pad_type, line)
        self.descr = ""
        self.tags = ""

    @property
    def fp_id(self):
        return f"{self.lib}:{self.name}"

    @property
    def pad_names(self):
        """Unique pad names (a name may appear on several physical pads)."""
        return {name for name, _t, _l in self.pads if name != ""}

    @property
    def pad_count(self):
        """Number of *physical* pads (duplicated names counted separately)."""
        return len(self.pads)

    def pads_named(self, name):
        return [p for p in self.pads if p[0] == name]

    def as_dict(self):
        return {
            "fp_id": self.fp_id, "lib": self.lib, "name": self.name,
            "source": f"{self.file}:{self.line}",
            "descr": self.descr, "tags": self.tags,
            "pad_count": self.pad_count,
            "pad_names": sorted(self.pad_names),
            "pads": [{"name": n, "type": t, "source": f"{self.file}:{ln}"}
                     for n, t, ln in self.pads],
        }

    def __repr__(self):
        return f"<Footprint {self.fp_id} ({self.pad_count} pads)>"


# =====================================================================
#  Library location
# =====================================================================

_ENV_VERSIONS = ("9", "8", "7")


def _looks_like_symbol_dir(path):
    if not path or not os.path.isdir(path):
        return False
    try:
        return any(f.endswith(".kicad_sym") for f in os.listdir(path))
    except OSError:
        return False


def _looks_like_footprint_dir(path):
    if not path or not os.path.isdir(path):
        return False
    try:
        return any(f.endswith(".pretty") for f in os.listdir(path))
    except OSError:
        return False


def _platform_defaults():
    """Return candidate (symbol_dir, footprint_dir) pairs for this platform."""
    system = platform.system()
    if system == "Darwin":
        base = "/Applications/KiCad/KiCad.app/Contents/SharedSupport"
        return [(f"{base}/symbols", f"{base}/footprints")]
    if system == "Windows":
        out = []
        for prog in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            kicad = os.path.join(prog, "KiCad")
            if os.path.isdir(kicad):
                for ver in sorted(os.listdir(kicad), reverse=True):
                    share = os.path.join(kicad, ver, "share", "kicad")
                    out.append((os.path.join(share, "symbols"),
                                os.path.join(share, "footprints")))
        return out
    # Linux / BSD
    return [
        ("/usr/share/kicad/symbols", "/usr/share/kicad/footprints"),
        ("/usr/local/share/kicad/symbols", "/usr/local/share/kicad/footprints"),
        ("/usr/share/kicad-nightly/symbols", "/usr/share/kicad-nightly/footprints"),
        (os.path.expanduser("~/.local/share/kicad/symbols"),
         os.path.expanduser("~/.local/share/kicad/footprints")),
    ]


def config_path():
    """Path of the project KiCad-library config file."""
    return os.environ.get(
        "STAGE1_IV_KICAD_CONFIG", os.path.join(REPO_ROOT, "kicad_libs.json"))


def _read_config():
    path = config_path()
    if not os.path.isfile(path):
        return {}, path
    with open(path) as fh:
        try:
            return json.load(fh), path
        except json.JSONDecodeError as exc:
            raise LibraryNotFound(
                f"{path} is not valid JSON: {exc}") from exc


RESOLUTION_HELP = """\
No installed KiCad symbol/footprint library could be located.

Fix this by doing ONE of the following:

  1. Export the environment variables KiCad itself uses, e.g.

         export KICAD9_SYMBOL_DIR=/usr/share/kicad/symbols
         export KICAD9_FOOTPRINT_DIR=/usr/share/kicad/footprints

     (KICAD8_* and KICAD7_* are also accepted.)

  2. Write the paths into the project config file
         {config}
     as

         {{"symbol_dir": "/path/to/symbols",
           "footprint_dir": "/path/to/footprints"}}

  3. Install KiCad with its standard libraries so that the platform default
     location exists.  Candidates probed on this platform:
{defaults}

This project deliberately refuses to guess pin numbers, symbol names or
footprint names, so it cannot continue without a real library to read.
"""


def locate_libraries(symbol_dir=None, footprint_dir=None):
    """Locate the installed KiCad libraries.

    Returns ``(symbol_dir, footprint_dir, origin)`` where *origin* describes
    which rule produced the answer.  Raises :class:`LibraryNotFound` when no
    candidate resolves.
    """
    tried = []

    # -- explicit override (API/CLI argument) -------------------------------
    # An explicit override is AUTHORITATIVE: if it does not resolve we fail
    # rather than silently falling back, so that
    #   resolve_kicad_libs.py --symbol-dir /nonexistent --footprint-dir /nonexistent
    # is a reproducible test of the "no library found" path on any machine.
    if symbol_dir or footprint_dir:
        tried.append(("explicit argument", symbol_dir, footprint_dir))
        if _looks_like_symbol_dir(symbol_dir) and _looks_like_footprint_dir(footprint_dir):
            return symbol_dir, footprint_dir, "explicit argument"
        raise LibraryNotFound(
            RESOLUTION_HELP.format(config=config_path(), defaults="       (not probed: "
                                   "an explicit --symbol-dir/--footprint-dir override "
                                   "was given and is authoritative)")
            + f"\nExplicit override did not resolve:\n"
              f"    symbol_dir    = {symbol_dir!r} "
              f"(contains *.kicad_sym: {_looks_like_symbol_dir(symbol_dir)})\n"
              f"    footprint_dir = {footprint_dir!r} "
              f"(contains *.pretty: {_looks_like_footprint_dir(footprint_dir)})\n")

    # -- 1. environment variables ------------------------------------------
    for ver in _ENV_VERSIONS:
        s = os.environ.get(f"KICAD{ver}_SYMBOL_DIR")
        f = os.environ.get(f"KICAD{ver}_FOOTPRINT_DIR")
        if not s and not f:
            continue
        origin = f"environment KICAD{ver}_SYMBOL_DIR / KICAD{ver}_FOOTPRINT_DIR"
        tried.append((origin, s, f))
        if _looks_like_symbol_dir(s) and _looks_like_footprint_dir(f):
            return s, f, origin

    # -- 2. project config file --------------------------------------------
    cfg, cfg_path = _read_config()
    if cfg:
        s = cfg.get("symbol_dir")
        f = cfg.get("footprint_dir")
        origin = f"config file {cfg_path}"
        tried.append((origin, s, f))
        if _looks_like_symbol_dir(s) and _looks_like_footprint_dir(f):
            return s, f, origin

    # -- 3. platform defaults ----------------------------------------------
    for s, f in _platform_defaults():
        origin = f"platform default ({platform.system()})"
        tried.append((origin, s, f))
        if _looks_like_symbol_dir(s) and _looks_like_footprint_dir(f):
            return s, f, origin

    defaults = "\n".join(f"       {s}\n       {f}"
                         for s, f in _platform_defaults())
    detail = "\n".join(f"    [{o}] symbols={s!r} footprints={f!r}"
                       for o, s, f in tried) or "    (nothing configured)"
    raise LibraryNotFound(
        RESOLUTION_HELP.format(config=config_path(), defaults=defaults)
        + "\nCandidates probed:\n" + detail)


# =====================================================================
#  The resolver
# =====================================================================

class KicadLibraries:
    """Queryable view of one installed KiCad symbol + footprint library tree.

    Files are indexed eagerly (cheap: a directory walk) and parsed lazily
    (only the libraries actually queried are read).
    """

    def __init__(self, symbol_dir=None, footprint_dir=None,
                 project_symbol_dir=PROJECT_SYMBOL_DIR):
        self.symbol_dir, self.footprint_dir, self.origin = locate_libraries(
            symbol_dir, footprint_dir)
        self.project_symbol_dir = project_symbol_dir

        # nickname -> .kicad_sym path
        self._sym_files = {}
        for path, nick in self._scan_symbol_dirs():
            self._sym_files[nick] = path
        # nickname -> .pretty dir
        self._fp_dirs = {}
        for entry in sorted(os.listdir(self.footprint_dir)):
            if entry.endswith(".pretty"):
                self._fp_dirs[entry[: -len(".pretty")]] = os.path.join(
                    self.footprint_dir, entry)

        self._sym_cache = {}          # nickname -> {name: Symbol}
        self._sym_name_cache = {}     # nickname -> [names]  (cheap index)
        self._fp_cache = {}           # (nick, name) -> Footprint
        self._fp_name_cache = {}      # nickname -> [names]

    # ---------------------------------------------------------------- setup

    def _scan_symbol_dirs(self):
        dirs = [self.symbol_dir]
        # A project-local library shadows nothing; it simply adds nicknames.
        if self.project_symbol_dir and os.path.isdir(self.project_symbol_dir):
            dirs.append(self.project_symbol_dir)
        for d in dirs:
            for entry in sorted(os.listdir(d)):
                if entry.endswith(".kicad_sym"):
                    yield os.path.join(d, entry), entry[: -len(".kicad_sym")]

    def describe(self):
        return {
            "symbol_dir": self.symbol_dir,
            "footprint_dir": self.footprint_dir,
            "project_symbol_dir": self.project_symbol_dir,
            "origin": self.origin,
            "symbol_libraries": len(self._sym_files),
            "footprint_libraries": len(self._fp_dirs),
        }

    # -------------------------------------------------------------- symbols

    def symbol_libraries(self):
        return sorted(self._sym_files)

    def has_symbol_library(self, nickname):
        return nickname in self._sym_files

    def symbol_library_path(self, nickname):
        return self._sym_files.get(nickname)

    def _load_symbol_library(self, nickname):
        if nickname in self._sym_cache:
            return self._sym_cache[nickname]
        path = self._sym_files.get(nickname)
        if path is None:
            self._sym_cache[nickname] = {}
            return {}
        with open(path, encoding="utf-8") as fh:
            tree = parse_sexp(fh.read())
        symbols = {}
        for lib_node in _children(tree, "kicad_symbol_lib"):
            for sym_node in _children(lib_node, "symbol"):
                sym = self._build_symbol(nickname, path, sym_node)
                symbols[sym.name] = sym
        # resolve (extends ...) — derived symbols inherit the parent's pins
        for sym in symbols.values():
            if sym.extends:
                seen = set()
                base = sym
                while base.extends and base.extends not in seen:
                    seen.add(base.extends)
                    base = symbols.get(base.extends)
                    if base is None:
                        break
                if base is not None and base is not sym:
                    sym.pins = base.pins
                    sym.units = base.units
                    for key, val in base.properties.items():
                        sym.properties.setdefault(key, val)
        self._sym_cache[nickname] = symbols
        return symbols

    @staticmethod
    def _build_symbol(nickname, path, node):
        name = str(node[1])
        sym = Symbol(nickname, name, path, node.line)
        ext = _first(node, "extends")
        if ext is not None and len(ext) > 1:
            sym.extends = str(ext[1])
        for prop in _children(node, "property"):
            if len(prop) > 2:
                sym.properties[str(prop[1])] = str(prop[2])
        # pins live in the per-unit child symbols "<name>_<unit>_<style>"
        for unit_node in _children(node, "symbol"):
            unit_name = str(unit_node[1])
            unit, style = KicadLibraries._unit_style(unit_name, name)
            if style not in (0, 1):
                continue    # skip the DeMorgan / alternate body style
            sym.units.add(unit)
            for pin_node in _children(unit_node, "pin"):
                sym.pins.append(KicadLibraries._build_pin(
                    pin_node, unit, path))
        # a few symbols carry pins directly (no unit sub-symbol)
        for pin_node in _children(node, "pin"):
            sym.pins.append(KicadLibraries._build_pin(pin_node, 1, path))
        sym.pins.sort(key=lambda p: _natural_key(p.number))
        return sym

    @staticmethod
    def _unit_style(unit_name, parent_name):
        suffix = unit_name[len(parent_name):] if unit_name.startswith(parent_name) else unit_name
        parts = [p for p in suffix.split("_") if p != ""]
        try:
            return int(parts[-2]), int(parts[-1])
        except (IndexError, ValueError):
            return 1, 1

    @staticmethod
    def _build_pin(pin_node, unit, path):
        etype = str(pin_node[1]) if len(pin_node) > 1 else "unspecified"
        style = str(pin_node[2]) if len(pin_node) > 2 else "line"
        name_node = _first(pin_node, "name")
        num_node = _first(pin_node, "number")
        pin_name = str(name_node[1]) if name_node is not None and len(name_node) > 1 else "~"
        pin_num = str(num_node[1]) if num_node is not None and len(num_node) > 1 else ""
        line = num_node[1].line if num_node is not None and len(num_node) > 1 else pin_node.line
        return SymbolPin(pin_num, pin_name, etype, style, unit, path, line)

    def symbol_names(self, nickname):
        """Names of the top-level symbols in a library.

        Uses a cheap regex index rather than a full parse: enumerating every
        name across 200+ libraries for a search must not cost a full parse of
        every file.  Pin data always comes from the real parse in
        :meth:`get_symbol`.
        """
        if nickname in self._sym_name_cache:
            return self._sym_name_cache[nickname]
        if nickname in self._sym_cache:
            names = sorted(self._sym_cache[nickname])
            self._sym_name_cache[nickname] = names
            return names
        path = self._sym_files.get(nickname)
        if path is None:
            self._sym_name_cache[nickname] = []
            return []
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        raw = [_unquote(m.group(1)) for m in _SYMBOL_NAME_RE.finditer(text)]
        rawset = set(raw)
        # Drop the per-unit child symbols "<parent>_<unit>_<style>".
        names = []
        for name in raw:
            m = _UNIT_SUFFIX_RE.match(name)
            if m and m.group(1) in rawset:
                continue
            names.append(name)
        names = sorted(set(names))
        self._sym_name_cache[nickname] = names
        return names

    def get_symbol(self, lib_id):
        """Return the :class:`Symbol` for ``"Lib:Name"``, or None."""
        if ":" not in lib_id:
            return None
        nick, name = lib_id.split(":", 1)
        return self._load_symbol_library(nick).get(name)

    def find_symbols(self, pattern, libraries=None):
        """Glob-match symbol names across libraries. Returns sorted lib_ids."""
        pat = pattern if any(c in pattern for c in "*?[") else f"*{pattern}*"
        out = []
        for nick in (libraries or self.symbol_libraries()):
            for name in self.symbol_names(nick):
                if fnmatch.fnmatch(name.lower(), pat.lower()):
                    out.append(f"{nick}:{name}")
        return sorted(out)

    # ----------------------------------------------------------- footprints

    def footprint_libraries(self):
        return sorted(self._fp_dirs)

    def has_footprint_library(self, nickname):
        return nickname in self._fp_dirs

    def footprint_library_path(self, nickname):
        return self._fp_dirs.get(nickname)

    def footprint_names(self, nickname):
        if nickname in self._fp_name_cache:
            return self._fp_name_cache[nickname]
        d = self._fp_dirs.get(nickname)
        names = []
        if d:
            names = sorted(e[: -len(".kicad_mod")] for e in os.listdir(d)
                           if e.endswith(".kicad_mod"))
        self._fp_name_cache[nickname] = names
        return names

    def get_footprint(self, fp_id):
        """Return the :class:`Footprint` for ``"Lib:Name"``, or None."""
        if ":" not in fp_id:
            return None
        nick, name = fp_id.split(":", 1)
        key = (nick, name)
        if key in self._fp_cache:
            return self._fp_cache[key]
        d = self._fp_dirs.get(nick)
        fp = None
        if d:
            path = os.path.join(d, name + ".kicad_mod")
            if os.path.isfile(path):
                fp = self._parse_footprint(nick, name, path)
        self._fp_cache[key] = fp
        return fp

    @staticmethod
    def _parse_footprint(nick, name, path):
        with open(path, encoding="utf-8") as fh:
            tree = parse_sexp(fh.read())
        node = _first(tree, "footprint") or _first(tree, "module")
        fp = Footprint(nick, name, path, node.line if node is not None else 1)
        if node is None:
            return fp
        descr = _first(node, "descr")
        if descr is not None and len(descr) > 1:
            fp.descr = str(descr[1])
        tags = _first(node, "tags")
        if tags is not None and len(tags) > 1:
            fp.tags = str(tags[1])
        for pad in _children(node, "pad"):
            pad_name = str(pad[1]) if len(pad) > 1 else ""
            pad_type = str(pad[2]) if len(pad) > 2 else ""
            fp.pads.append((pad_name, pad_type, pad[1].line if len(pad) > 1 else pad.line))
        return fp

    def find_footprints(self, pattern, libraries=None):
        """Glob-match footprint names across libraries. Returns sorted fp_ids."""
        pat = pattern if any(c in pattern for c in "*?[") else f"*{pattern}*"
        out = []
        for nick in (libraries or self.footprint_libraries()):
            for name in self.footprint_names(nick):
                if fnmatch.fnmatch(name.lower(), pat.lower()):
                    out.append(f"{nick}:{name}")
        return sorted(out)

    # ------------------------------------------------------------ provenance

    def provenance(self):
        """Version/commit provenance of the installed libraries, if recorded.

        ``tools/install_kicad_libs.sh`` drops a ``.kicad_lib_provenance.json``
        stamp (upstream git tag + commit) into each library root; when it is
        present the exact upstream revision every value was read from is
        machine-readable rather than asserted.
        """
        out = {}
        for label, path in (("symbols", self.symbol_dir),
                            ("footprints", self.footprint_dir)):
            info = {"path": path}
            stamp = os.path.join(path, ".kicad_lib_provenance.json")
            if os.path.isfile(stamp):
                try:
                    with open(stamp) as fh:
                        info.update(json.load(fh))
                except (OSError, json.JSONDecodeError):
                    pass
            legacy = os.path.join(path, "VERSION")
            if "version" not in info and os.path.isfile(legacy):
                with open(legacy) as fh:
                    info["version"] = fh.read().strip()
            out[label] = info
        return out


_SPLIT_RE = re.compile(r"(\d+)")


def _natural_key(value):
    return tuple((int(p) if p.isdigit() else p) for p in _SPLIT_RE.split(str(value)))


# =====================================================================
#  CLI
# =====================================================================

def _dump_all(libs, limit_symbol_libs=None, limit_fp_libs=None):
    data = {"resolver": libs.describe(), "provenance": libs.provenance(),
            "symbols": {}, "footprints": {}}
    for nick in (limit_symbol_libs or libs.symbol_libraries()):
        data["symbols"][nick] = {
            name: [p.as_dict() for p in libs.get_symbol(f"{nick}:{name}").pins]
            for name in libs.symbol_names(nick)
        }
    for nick in (limit_fp_libs or libs.footprint_libraries()):
        entry = {}
        for name in libs.footprint_names(nick):
            fp = libs.get_footprint(f"{nick}:{name}")
            entry[name] = sorted(fp.pad_names) if fp else []
        data["footprints"][nick] = entry
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Resolve identifiers against the installed KiCad libraries.")
    ap.add_argument("--symbol-dir", help="override the symbol library directory")
    ap.add_argument("--footprint-dir", help="override the footprint library directory")
    ap.add_argument("--paths", action="store_true",
                    help="print the resolved library locations and exit")
    ap.add_argument("--symbol", metavar="LIB:NAME",
                    help="dump one symbol's pins")
    ap.add_argument("--find-symbol", metavar="PATTERN",
                    help="glob-search symbol names")
    ap.add_argument("--footprint", metavar="LIB:NAME",
                    help="dump one footprint's pads")
    ap.add_argument("--find-footprint", metavar="PATTERN",
                    help="glob-search footprint names")
    ap.add_argument("--in-lib", action="append", default=None,
                    help="restrict a search to these library nicknames")
    ap.add_argument("--dump-json", metavar="FILE",
                    help="dump every symbol and footprint to FILE (large)")
    ap.add_argument("--json", action="store_true",
                    help="emit query results as JSON")
    args = ap.parse_args(argv)

    try:
        libs = KicadLibraries(args.symbol_dir, args.footprint_dir)
    except LibraryNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2

    did = False

    if args.paths or not any([args.symbol, args.find_symbol, args.footprint,
                              args.find_footprint, args.dump_json]):
        info = libs.describe()
        if args.json:
            print(json.dumps({"resolver": info,
                              "provenance": libs.provenance()}, indent=2))
        else:
            print(f"resolved from : {info['origin']}")
            print(f"symbol_dir    : {info['symbol_dir']}")
            print(f"footprint_dir : {info['footprint_dir']}")
            print(f"project symbols: {info['project_symbol_dir']}")
            print(f"symbol libs   : {info['symbol_libraries']}")
            print(f"footprint libs: {info['footprint_libraries']}")
        did = True

    if args.symbol:
        sym = libs.get_symbol(args.symbol)
        if sym is None:
            print(f"NOT FOUND: symbol {args.symbol}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(sym.as_dict(), indent=2))
        else:
            print(f"{sym.lib_id}   [{sym.file}:{sym.line}]")
            if sym.extends:
                print(f"  extends: {sym.extends}")
            print(f"  {len(sym.pins)} pins")
            for p in sym.pins:
                print(f"    {p.number:>4}  {p.name:<24} {p.etype:<14} "
                      f"unit={p.unit}  [{os.path.basename(p.file)}:{p.line}]")
        did = True

    if args.find_symbol:
        hits = libs.find_symbols(args.find_symbol, args.in_lib)
        print(json.dumps(hits, indent=2) if args.json else "\n".join(hits))
        did = True

    if args.footprint:
        fp = libs.get_footprint(args.footprint)
        if fp is None:
            print(f"NOT FOUND: footprint {args.footprint}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(fp.as_dict(), indent=2))
        else:
            print(f"{fp.fp_id}   [{fp.file}:{fp.line}]")
            print(f"  descr: {fp.descr}")
            print(f"  {fp.pad_count} pads, {len(fp.pad_names)} distinct names")
            for name, ptype, line in fp.pads:
                print(f"    {name:>6}  {ptype:<8} [{os.path.basename(fp.file)}:{line}]")
        did = True

    if args.find_footprint:
        hits = libs.find_footprints(args.find_footprint, args.in_lib)
        print(json.dumps(hits, indent=2) if args.json else "\n".join(hits))
        did = True

    if args.dump_json:
        data = _dump_all(libs, args.in_lib, args.in_lib)
        with open(args.dump_json, "w") as fh:
            json.dump(data, fh, indent=1)
        print(f"wrote {args.dump_json}")
        did = True

    return 0 if did else 0


if __name__ == "__main__":
    sys.exit(main())
