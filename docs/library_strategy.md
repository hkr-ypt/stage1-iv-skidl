# Library strategy: pinning, portability, and lib_symbol_issues

## 1. Step 1 — diagnosing the installation

The brief's diagnostic commands name macOS paths. **The ERC run they describe
happened on the reporter's Mac; this build host is Linux.** Verbatim:

```
$ uname -a
Linux vm 6.18.5-fc-v20 #1 SMP PREEMPT_DYNAMIC @0 x86_64 x86_64 x86_64 GNU/Linux

$ ls -la /Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/ | head -60
ls: cannot access '/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/':
    No such file or directory

$ env | grep -i kicad
(no output — no KICAD* variables set)

$ cat ~/Library/Preferences/kicad/9.0/sym-lib-table
cat: /root/Library/Preferences/kicad/9.0/sym-lib-table: No such file or directory

$ cat ~/Library/Preferences/kicad/9.0/fp-lib-table
cat: /root/Library/Preferences/kicad/9.0/fp-lib-table: No such file or directory
```

### Verdict: CONSISTENT and mechanically reproduced — but NOT confirmed

I cannot confirm it, because I cannot inspect the machine where the ERC ran.
Saying "confirmed" would be a claim about a filesystem I have never seen. What
I *can* establish, and did:

**(a) The alphabetical pattern is real, and very unlikely to be coincidence.**
Sorted positions of the twelve libraries in the 223-library upstream set:

| library | position | ERC outcome |
|---|---|---|
| Amplifier_Instrumentation | 10 | resolved |
| Amplifier_Operational | 11 | resolved |
| Analog_ADC | 14 | resolved |
| Analog_DAC | 15 | resolved |
| Connector | 31 | failed |
| Connector_Generic | 33 | failed |
| Device | 43 | failed |
| MCU_ST_STM32F4 | 132 | failed |
| Power_Protection | 167 | failed |
| Reference_Voltage | 184 | failed |
| Regulator_Linear | 187 | failed |
| Switch | 209 | failed |

Every resolving library sorts at position ≤15; every failing one at ≥31. A cut
anywhere in positions 16–30 reproduces the observation exactly. If four of
twelve libraries failed for an unrelated reason, the chance the survivors are
precisely the four alphabetically-first is 1 in C(12,4) = **1 in 495**.

**(b) The mechanism reproduces exactly.** Copying only the first 20 libraries
of a complete installation — simulating an interrupted copy — and asking the
resolver which of the design's libraries resolve:

```
truncated dir has 20 of 223 libraries
RESOLVED (4): ['Amplifier_Instrumentation', 'Amplifier_Operational',
               'Analog_ADC', 'Analog_DAC']
FAILED   (9): ['Connector', 'Connector_Generic', 'Device', 'MCU_ST_STM32F4',
               'Power_Protection', 'Reference_Voltage', 'Regulator_Linear',
               'Switch', 'power']
```

The same four survive. (Nine fail rather than eight because `power` was added
by the PWR_FLAG work.)

**(c) Competing explanations are ruled out by the error text.** The reported
errors name a *path that does not contain the file* — a file-not-found at a
resolved location. So the `sym-lib-table` had entries for all twelve; the
nicknames resolved and the files were absent. That eliminates "missing lib
table entries". A broken `KICAD9_SYMBOL_DIR` would fail all twelve, not a
prefix. The doubled slash in the reported path is benign POSIX path joining.

### The definitive check, to run on the affected machine

```bash
python3 tools/kicad_env.py --check --diagnose
```

It reports `NOT FOUND` / `INCOMPLETE` / `COMPLETE`, names every missing
library, and — when the present ones sort before all the missing ones — says
so and points at the interrupted-copy explanation. Exit code 3 means
INCOMPLETE. Against the simulated truncation:

```
  installation state .. INCOMPLETE
  symbol libraries .... 21
  required symbol libs  3/12 present
      MISSING: ['Connector', 'Connector_Generic', 'Device', 'MCU_ST_STM32F4',
                'Power_Protection', 'Reference_Voltage', 'Regulator_Linear',
                'Switch', 'power']

  Every missing library is absent from the directory entirely, and the present
  ones sort alphabetically before them. That is the signature of an interrupted
  copy/sync of the symbols directory, not a missing KiCad install and not a bad
  environment variable (a bad variable fails all libraries, not a prefix).
  Re-run tools/install_kicad_libs.sh, or re-sync the symbols directory.
```

### Cause A confirmed empirically

The brief predicts that repairing the installation converts ~101 "library not
found" warnings into ~101 mismatches, leaving the count unimproved. This host
has a **complete** 9.0.9.1 installation, so it is the post-repair state. Running
the measurement against `main` as it stood before this change:

```
ERC lib_symbol_issues                  : 0
ERC lib_symbol_mismatch (upper bound)  : 46
    ['Amplifier_Instrumentation:INA128 (current_sense.kicad_sch)',
     'Amplifier_Operational:OPA2277 (dac_sweep.kicad_sch)',
     'Analog_DAC:DAC8552 (dac_sweep.kicad_sch)',
     'Connector:Screw_Terminal_01x04 (power.kicad_sch)', ...]
```

46 distinct cached-symbol entries across the nine sheets, covering **all 118**
symbol instances. Exactly the predicted outcome: repairing the library moves
the problem, it does not fix it. **Cause A is repo-side and had to be fixed in
the repository.**

---

## 2. Step 2 — strategy chosen: **Strategy 1, own the symbols**

Every schematic instance now names `stage1_iv:<SymbolName>`, and
`libraries/stage1_iv.kicad_sym` is a real KiCad symbol library committed to the
repository. Both the library and the copy embedded in each sheet's
`(lib_symbols)` block are emitted from the *same*
`generators/schematic.py::SymbolDef` objects, so they are the same bytes.

Verified, not assumed — all 25 symbols, across all nine sheets:

```
library symbols: 25
schematic symbols checked: 25 | identical bodies: 25 | differing: 0
```

### Why not Strategy 2

Strategy 2 — embedding the real upstream symbol graphics — produces
better-looking schematics, and I would have preferred it on aesthetics alone.
It loses on three counts that matter more here:

1. **It requires installed KiCad libraries at generation time.** Hard constraint
   4 says the design must build on a machine with no KiCad. Strategy 2 makes
   that impossible; Strategy 1 makes the committed project library the fallback.
2. **It leaves the failure mode reachable.** Under Strategy 2 the schematic
   still points at upstream libraries, so a truncated installation — the very
   condition diagnosed above, on a machine I cannot repair from here — brings
   `lib_symbol_issues` straight back. Under Strategy 1 the only library the
   schematic needs is one that ships with the repository.
3. **Output would change with the installed library version.** Pinning helps,
   but a contributor on a different KiCad would silently produce a different
   schematic.

### What was given up

* **Familiar symbol graphics.** Every symbol is a generated rectangle with the
  correct pins, not the conventional resistor zigzag or op-amp triangle. This is
  a real readability cost and the main argument against the choice.
* **`KiCad_Lib` is no longer the live link KiCad follows.** It remains on every
  instance as a property, and it is still verified against the installed
  libraries by `validation/footprint_check.py` on every build that has them —
  so it is documentation *plus* a checked assertion, not documentation alone.
* Placing a stock KiCad symbol beside a project one now needs the upstream
  library added to the project's `sym-lib-table` — which the generated table
  already does, for exactly the libraries this design depends on.

---

## 3. Before / after, at each stage

| ERC violation type | (a) as reported | (b) after library repair | (c) after Strategy 1 |
|---|---|---|---|
| `lib_symbol_issues` "library not found" | 105 | **0** | **0** |
| `lib_symbol_mismatch` | 2 | **46** entries / all 118 instances | **0** |
| `power_pin_not_driven` | 8 → 7 measured | 0 *(fixed earlier)* | **0** |
| `footprint_link_issues` | 0 | 0 | **0** |
| "Symbol not found in symbol library" | 0 | 0 | **0** |
| distinct lib_ids outside the project library | 24 | 24 | **0** |

Column (b) is measured on this host, which has a complete 9.0.9.1 installation
and is therefore the repaired state. Column (a) is the reporter's figure.

**These are re-implementations of KiCad's rules, evaluated on the emitted
`.kicad_sch` files — not a KiCad ERC run.** `kicad-cli sch erc` first shipped in
KiCad 8, and KiCad 7.0.11 is the newest version installable in this environment
(every distribution channel for a newer build is refused by the network egress
policy). See §6.

---

## 4. Step 7 — flat netlist vs hierarchical schematic

Confirmed: `stage1_iv.net` carries `(names "/")` for all 118 components, while
KiCad's own export of the schematic reports eight sheets:

```
16 (sheetpath (names "/adc/")        7 (sheetpath (names "/current_sense/")
12 (sheetpath (names "/dac_sweep/")  29 (sheetpath (names "/mcu/")
11 (sheetpath (names "/output_buffer/") 15 (sheetpath (names "/power/")
 7 (sheetpath (names "/protection/") 13 (sheetpath (names "/voltage_sense/")
```

### Decision: (b) — the schematic is the single source of truth for KiCad

`stage1_iv.net` remains flat and is **documentation and a validation input, not
a PCB input.**

Why not (a): SKiDL's `generate_netlist` has no hierarchy model that maps onto
this design's sheet structure, so emitting hierarchical sheetpaths means
patching netlist generation — and would still leave two artifacts free to drift.
Netlist import into Pcbnew has been the legacy path since KiCad 6; "Update PCB
from Schematic" is the supported one.

Enforced, not merely documented — `tools/kicad_roundtrip.py` now fails if:
* any component in `stage1_iv.net` acquires a non-`/` sheetpath (which would
  mean this decision was reversed without updating it), or
* KiCad's reading of the schematic assigns any component to a sheet other than
  the block it was built in.

```
sheet assignment         : 110 components across 8 sheets, all match the design's blocks
```

### Migration consequences for anyone with an existing board

* **A PCB previously imported from `stage1_iv.net` has flat sheetpaths.** On the
  first "Update PCB from Schematic" KiCad will re-associate every footprint with
  its hierarchical path. Footprints are matched by reference designator, so
  placement and routing survive; the timestamp/path metadata is rewritten.
* **`J4` became `J4`–`J7`** in the identifier work (the 4-position banana jack
  does not exist upstream). A board predating that has one 4-pin part where the
  schematic now has four 1-pin parts — this needs manual attention, and no tool
  can do it safely.
* **Eight `#FLG` power-flag symbols** are schematic-only; KiCad excludes
  `#`-prefixed references from the board and they will never appear on it.
* Do not import `stage1_iv.net` into Pcbnew again. If you have automation that
  does, point it at the schematic instead.

---

## 5. Fresh-clone reproducibility

Cloned into an empty directory and driven only by the README:

```
$ git clone <repo> fresh && cd fresh
$ python3 tools/kicad_env.py --check --diagnose
  resolved from ....... platform default (Linux)
  installation state .. COMPLETE
  symbols            pinned at 9.0.9.1 @ ad36cd14bcd1
  required symbol libs  12/12 present
  required fp libs      21/21 present

  python3 main.py                                exit=0
  python3 tools/author_project_symbols.py --check exit=0
  python3 tools/gen_project_config.py --check    exit=0
  python3 validation/footprint_check.py          exit=0
  python3 validation/erc.py                      exit=0
  python3 validation/schematic_check.py          exit=0
  python3 tools/kicad_erc_power.py               exit=0
  python3 tools/kicad_roundtrip.py               exit=0
  python3 tools/check_no_abs_paths.py            exit=0

$ git diff --quiet -- ':!stage1_iv.net'
  CLEAN — regenerating in a fresh clone reproduces the committed files
```

### Steps the README did not cover, and now does

* `ply` was missing from the documented `pip install` line; SKiDL needs it.
* `kinparse` fails to build against modern setuptools; it needs
  `SETUPTOOLS_USE_DISTUTILS=stdlib pip install --no-build-isolation kinparse`.
* Nothing told a reader how to check whether their KiCad installation was
  usable — the gap that produced this whole task. `tools/kicad_env.py
  --diagnose` is now the first step in the README.

### Building with no KiCad at all

```
  KiCad libraries ....... NOT FOUND
                          (run tools/kicad_env.py --diagnose)
  Symbol resolution ..... SKIPPED (no KiCad libraries to check against)
  Footprint resolution .. SKIPPED (no KiCad libraries to check against)
  SKiDL ERC ............. 0 errors, 0 warnings
  KiCad ERC ............. NOT RUN (open stage1_iv.kicad_sch in KiCad and run ERC;
                          see docs/LIBRARY_DEPENDENCIES.md)
```

and in the summary:

```
  Structural check ... SKIPPED — no KiCad libraries; identifiers
                       and pads were NOT verified
```

The netlist and schematic are still produced — pin data comes from the
committed project library — but nothing claims KiCad conformance. The
`build-without-kicad` CI job asserts these exact strings appear.

---

## 6. What was not done

**KiCad's ERC dialog was not run, on any machine reachable from here.**
`kicad-cli sch erc` first shipped in KiCad 8; KiCad 7.0.11 is the newest
installable version in this environment, because `ppa.launchpadcontent.net`,
`launchpadlibrarian.net`, flathub, snapcraft, opensuse and `downloads.kicad.org`
are all refused by the network egress policy. Every ERC number in this document
comes from re-implementations of KiCad's rules evaluated on the emitted
`.kicad_sch` files, and is labelled as such. The CI workflow runs
`kicad-cli sch erc` when the runner provides it and prints SKIPPED when it does
not — it never reports a pass it did not obtain.

The target of **total ERC violations = 0** is therefore *predicted*, not
observed, for the categories only the dialog can enumerate (geometric rules:
wire/label/pin overlap, dangling ends, bus errors). The four categories the
brief names — `lib_symbol_issues`, `lib_symbol_mismatch`,
`power_pin_not_driven`, `footprint_link_issues` — are each measured at 0.

**No ERC severity setting was changed.** `stage1_iv.kicad_pro` deliberately
contains no `erc` section at all, so KiCad applies its own defaults:

```
$ grep -ri "severit" --include="*.kicad_pro" --include="*.json" .
(no output)
```
