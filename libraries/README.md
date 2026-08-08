# `libraries/`

## `stage1_iv.kicad_sym` — GENERATED, do not edit by hand

```bash
python3 tools/author_project_symbols.py           # regenerate
python3 tools/author_project_symbols.py --check   # verify it is current
python3 tools/check_kicad_loads.py                # verify KiCad can read it
```

Every symbol the schematic instantiates lives here. The bodies are produced by
the **same** `generators/schematic.py::SymbolDef` objects that the schematic
embeds in each sheet's `(lib_symbols)` block, so the schematic's cache and this
library cannot disagree and KiCad's `lib_symbol_mismatch` cannot fire.

Per-symbol pin provenance is in
[`provenance/stage1_iv_pin_provenance.json`](provenance/stage1_iv_pin_provenance.json):
`ADS1256` and `OPA548T` are parsed from the committed datasheet/library
excerpts in `provenance/`; every other symbol is read from the installed
upstream KiCad libraries at generation time, at the revision recorded in that
same file.

> The "generated file" banner lives here rather than at the top of the
> `.kicad_sym` because **KiCad's parser rejects a file that does not begin with
> `(kicad_symbol_lib`** — a leading `;` comment block makes the whole library
> unloadable. `tools/check_kicad_loads.py` guards against that returning.

### Schema version

The library is emitted in the **KiCad 6/7 symbol schema** (`(version 20211014)`),
which KiCad 7 reads natively and KiCad 9 migrates on load. It deliberately does
**not** carry `(generator_version)`, and its `(version)` stamp matches the
syntax of the symbol bodies — mismatching those is what made KiCad 9 refuse the
library and report "symbol library not found" 118 times.

## `custom_symbols.py`

Part factories. Pin data resolves in this order, and the build banner reports
which source answered: datasheet-authored provenance → installed upstream KiCad
libraries → this committed project library (the no-KiCad fallback).
