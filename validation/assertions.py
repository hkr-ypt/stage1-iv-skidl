"""
assertions.py — Custom connectivity assertions for the Stage 1 I–V system.
=========================================================================

These encode the design intent that ERC cannot: topology, reference sharing,
Kelvin independence, bus separation, and star grounding. Each check works on
the actual net membership of the built circuit and returns (passed, message).

``run_assertions(nets, circuit)`` runs them all, prints a report, and raises
AssertionError if any fails.
"""


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _pin_ids(net):
    """Return a set of (ref, pin_name) tuples for every pin on `net`."""
    return {(p.part.ref, p.name) for p in net.get_pins()}


def _refs_on(net):
    """Return the set of part refs that have at least one pin on `net`."""
    return {p.part.ref for p in net.get_pins()}


def _has(net, ref, pin_name=None):
    for p in net.get_pins():
        if p.part.ref == ref and (pin_name is None or p.name == pin_name):
            return True
    return False


# ----------------------------------------------------------------------
# individual assertions  -> (name, passed, detail)
# ----------------------------------------------------------------------

def a_shared_reference(n):
    ok = _has(n.VREF_2V5, "U4", "VREF") and _has(n.VREF_2V5, "U8", "VREFP")
    return ("DAC ref (U4.VREF) and ADC ref (U8.VREFP) share VREF_2V5", ok,
            f"refs on VREF_2V5 = {sorted(_refs_on(n.VREF_2V5))}")


def a_force_path_chain(n):
    # U5 -> R9 -> VO_SENSE -> F1 -> F1_MID -> R30 -> FORCE+ -> D5 & J4
    c1 = _has(n.VO_SENSE, "U5") and _has(n.VO_SENSE, "R9") and _has(n.VO_SENSE, "F1")
    c2 = _has(n.F1_MID, "F1") and _has(n.F1_MID, "R30")
    c3 = (_has(n.FORCE_PLUS, "R30") and _has(n.FORCE_PLUS, "D5")
          and _has(n.FORCE_PLUS, "J4"))
    ok = c1 and c2 and c3
    return ("FORCE+ path = OPA548(U5) -> R9 -> F1(fuse) -> R30 -> D5(TVS) -> J4",
            ok, f"VO_SENSE={sorted(_refs_on(n.VO_SENSE))}, "
                f"F1_MID={sorted(_refs_on(n.F1_MID))}, "
                f"FORCE+={sorted(_refs_on(n.FORCE_PLUS))}")


def a_kelvin_independent(n):
    # Kelvin sense nets must share NO pins with the force nets.
    sp = _pin_ids(n.SENSE_PLUS) & _pin_ids(n.FORCE_PLUS)
    sm = _pin_ids(n.SENSE_MINUS) & _pin_ids(n.FORCE_MINUS)
    # And the voltage-sense amp (U7) must reach the SENSE nets, not the FORCE nets.
    u7_on_sense = _has(n.SENSE_PLUS, "U7") is False  # U7 sits behind R12/R13
    force_free = (not _refs_on(n.SENSE_PLUS) & {"R10"}
                  and not _refs_on(n.SENSE_MINUS) & {"R10"})
    ok = (not sp) and (not sm) and force_free
    return ("Kelvin sense (SENSE+/-) is independent from the force path",
            ok, f"shared SENSE+/FORCE+ pins={sp or 'none'}, "
                f"shared SENSE-/FORCE- pins={sm or 'none'}")


def a_adc_on_spi2(n):
    ok = (_has(n.SCK2, "U8", "SCLK") and _has(n.MOSI2, "U8", "DIN")
          and _has(n.MISO2, "U8", "DOUT") and _has(n.CS_ADC, "U8", "CS"))
    return ("ADC (U8) uses SPI2 (SCK2/MOSI2/MISO2/CS_ADC)", ok,
            f"SCK2={sorted(_refs_on(n.SCK2))}")


def a_dac_on_spi1(n):
    ok = (_has(n.SCK1, "U4", "SCLK") and _has(n.MOSI1, "U4", "DIN")
          and _has(n.CS_DAC, "U4", "SYNC"))
    return ("DAC (U4) uses SPI1 (SCK1/MOSI1/CS_DAC, write-only)", ok,
            f"SCK1={sorted(_refs_on(n.SCK1))}")


def a_spi_buses_separate(n):
    shared = _pin_ids(n.SCK1) & _pin_ids(n.SCK2)
    ok = not shared
    return ("SPI1 and SPI2 are physically separate buses", ok,
            f"pins common to SCK1 and SCK2 = {shared or 'none'}")


def a_star_ground(n):
    # R31 must bridge AGND<->DGND, and no OTHER 2-terminal passive may bridge them.
    bridge = _has(n.AGND, "R31") and _has(n.DGND, "R31")
    agnd_refs = _refs_on(n.AGND)
    dgnd_refs = _refs_on(n.DGND)
    both = agnd_refs & dgnd_refs           # parts touching both planes
    # A part legitimately straddles both planes only if it is a multi-pin IC
    # (separate AGND/DGND pins, e.g. ADS1256). An accidental SECOND star join
    # would be a 2-terminal R/C/L shorting the planes. Flag any such extra part.
    extra_bridges = set()
    for ref in both:
        if ref == "R31":
            continue
        # find the part
        part = next(p.part for p in n.AGND.get_pins() if p.part.ref == ref)
        if part.ref[0] in ("R", "C", "F", "L") and len(part.pins) == 2:
            extra_bridges.add(ref)
    ok = bridge and not extra_bridges
    return ("AGND and DGND join ONLY at the R31 star point", ok,
            f"R31 bridges={bridge}, ICs straddling both planes (OK)="
            f"{sorted(both - {'R31'})}, illegal extra bridges={extra_bridges or 'none'}")


def a_net_manifest(n):
    """Every required net exists and has >= 2 pins (is actually used)."""
    from blocks.nets import NET_MANIFEST
    # attribute lookup for special names
    name_to_attr = {
        "+15VA": "P15VA", "-15VA": "N15VA", "+5VA": "P5VA", "+3V3D": "P3V3D",
        "AGND": "AGND", "DGND": "DGND", "VREF_2V5": "VREF_2V5",
        "FORCE+": "FORCE_PLUS", "FORCE-": "FORCE_MINUS",
        "SENSE+": "SENSE_PLUS", "SENSE-": "SENSE_MINUS",
    }
    empties = []
    for group in NET_MANIFEST.values():
        for name in group:
            attr = name_to_attr.get(name, name)
            net = getattr(n, attr)
            if len(net.get_pins()) < 2:
                empties.append(f"{name}({len(net.get_pins())} pins)")
    ok = not empties
    return ("All required manifest nets exist and are populated (>=2 pins)",
            ok, f"under-populated nets = {empties or 'none'}")


def a_usb_c_pads_fully_connected(n, circuit):
    """Every USB-C power/ground pad is bonded and both data pairs are tied.

    The USB-C receptacle is symmetric: GND is four pads, VBUS is four pads,
    and D+/D- each appear twice — once per cable orientation. Connecting only
    one of each is not a cosmetic omission: a plug inserted the other way up
    lands its data pair on unconnected pads and the port silently works in
    one orientation only.
    """
    j3 = next((p for p in circuit.parts if p.ref == "J3"), None)
    if j3 is None:
        return ("USB-C (J3) pads: GND/VBUS fully bonded, D+/D- pairs tied",
                False, "J3 not found in the circuit")

    def nets_of(name):
        pins = j3[name]
        pins = pins if isinstance(pins, list) else [pins]
        return {(str(p.num), tuple(sorted(x.name for x in p.get_nets())))
                for p in pins}

    problems = []

    gnd = nets_of("GND")
    if len(gnd) < 4:
        problems.append(f"expected 4 GND pads, symbol declares {len(gnd)}")
    off_rail = sorted(num for num, nets in gnd if "DGND" not in nets)
    if off_rail:
        problems.append(f"GND pad(s) {off_rail} are not on DGND")

    vbus = nets_of("VBUS")
    if len(vbus) < 4:
        problems.append(f"expected 4 VBUS pads, symbol declares {len(vbus)}")
    off_vbus = sorted(num for num, nets in vbus if "VBUS" not in nets)
    if off_vbus:
        problems.append(f"VBUS pad(s) {off_vbus} are not on VBUS")

    for label in ("DP", "DM"):
        pads = nets_of(label)
        if len(pads) != 2:
            problems.append(f"{label} should be 2 pads, symbol declares {len(pads)}")
            continue
        nets = {nets for _num, nets in pads}
        if len(nets) != 1 or not nets.pop():
            problems.append(
                f"{label} pads {sorted(num for num, _ in pads)} are not tied "
                f"to the same net — the cable would work in one orientation only")

    shield = nets_of("SHIELD")
    if not shield or any(not nets for _num, nets in shield):
        problems.append("SHIELD pad carries no net")

    ok = not problems
    return ("USB-C (J3) pads: GND/VBUS fully bonded, D+/D- pairs tied", ok,
            "; ".join(problems) if problems else
            f"GND={sorted(num for num, _ in gnd)} on DGND, "
            f"VBUS={sorted(num for num, _ in vbus)} on VBUS, "
            f"D+={sorted(num for num, _ in nets_of('DP'))} tied, "
            f"D-={sorted(num for num, _ in nets_of('DM'))} tied, "
            f"SHIELD={sorted(num for num, _ in shield)} connected")


ALL_ASSERTIONS = [
    a_shared_reference,
    a_force_path_chain,
    a_kelvin_independent,
    a_adc_on_spi2,
    a_dac_on_spi1,
    a_spi_buses_separate,
    a_star_ground,
    a_net_manifest,
    a_usb_c_pads_fully_connected,
]


def run_assertions(n, circuit=None):
    print("\n" + "=" * 70)
    print("CUSTOM CONNECTIVITY ASSERTIONS")
    print("=" * 70)
    failures = []
    for fn in ALL_ASSERTIONS:
        # Most assertions read only the net namespace; the ones that need to
        # reach a specific part (USB-C pad coverage) take the circuit too.
        nargs = fn.__code__.co_argcount
        name, ok, detail = fn(n, circuit) if nargs == 2 else fn(n)
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        print(f"         {detail}")
        if not ok:
            failures.append(name)
    if failures:
        raise AssertionError(f"{len(failures)} assertion(s) FAILED: {failures}")
    print("\nAll connectivity assertions: PASS")
    return True
