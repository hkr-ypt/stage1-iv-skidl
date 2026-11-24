"""
assertions.py — Custom connectivity assertions for the Stage 1 I–V system.
=========================================================================

These encode the design intent that ERC cannot: topology, reference sharing,
Kelvin independence, bus separation, star grounding, the bipolar
measurement zero (both INA128 REF pins and the ADC's AINCOM on VREF_2V5), and
the audit-F2 safe state (the sweep midpoint from DAC channel B, the DAC
reference gate, and the rule that only a toggling heartbeat can arm it).
Each check works on the actual net membership of the built circuit and
returns (passed, message).

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

def a_shared_reference(n, circuit):
    """The DAC and the ADC are referenced to the same REF5025 output.

    The DAC's reference reaches VREF_2V5 THROUGH the gate U11 (audit F2):
    U11.S on VREF_2V5, U11.D on the DAC's VREF pin. A U4.VREF wired straight
    to VREF_2V5 would bypass the reset-safe gate, so that is a failure too.
    """
    dac_ref = _net_of(_pin(circuit, "U4", "VREF"))
    ok = (_has(n.VREF_2V5, "U8", "VREFP") and _has(n.VREF_2V5, "U11", "S")
          and dac_ref is not None and _has(dac_ref, "U11", "D")
          and not _has(n.VREF_2V5, "U4", "VREF"))
    return ("DAC ref (U4.VREF, via gate U11) and ADC ref (U8.VREFP) share "
            "VREF_2V5", ok,
            f"U4.VREF on {dac_ref.name if dac_ref else None}, "
            f"refs on VREF_2V5 = {sorted(_refs_on(n.VREF_2V5))}")


def a_force_path_chain(n, circuit):
    # U5 -> R9 -> VO_SENSE -> F1 -> F1_MID -> R30 -> FORCE_SW -> U12 (the
    # photorelay's output) -> FORCE+ -> D5 & J4
    c1 = _has(n.VO_SENSE, "U5") and _has(n.VO_SENSE, "R9") and _has(n.VO_SENSE, "F1")
    c2 = _has(n.F1_MID, "F1") and _has(n.F1_MID, "R30")
    sw = None
    r30 = next((p for p in n.F1_MID.get_pins() if p.part.ref == "R30"), None)
    if r30 is not None:
        sw = _net_of(_two_pin_far(r30.part, r30))
    c3 = (sw is not None and sorted(_refs_on(sw)) == ["R30", "U12"]
          and {p.name for p in sw.get_pins() if p.part.ref == "U12"}
          <= {"OUT1", "OUT2"})
    c4 = (_has(n.FORCE_PLUS, "U12") and _has(n.FORCE_PLUS, "D5")
          and _has(n.FORCE_PLUS, "J4") and not _has(n.FORCE_PLUS, "R30"))
    ok = c1 and c2 and c3 and c4
    return ("FORCE+ path = power amp (U5) -> R9 -> F1(fuse) -> R30 -> U12 "
            "(photorelay) -> D5(TVS) -> J4",
            ok, f"VO_SENSE={sorted(_refs_on(n.VO_SENSE))}, "
                f"F1_MID={sorted(_refs_on(n.F1_MID))}, "
                f"R30->U12={sorted(_refs_on(sw)) if sw else 'none'}, "
                f"FORCE+={sorted(_refs_on(n.FORCE_PLUS))}")


def a_force_minus_chain(n, circuit):
    """J7 -> F2 -> ISENSE_HI -> R10 -> AGND, and nothing around the shunt.

    Every ampere returning through FORCE- must cross R10, so the shunt node
    may hold only R10, F2 (in series), the sense amplifier's +IN and its test
    point. A part from ISENSE_HI or FORCE- to anything else (a clamp to
    AGND, say) is a current path that bypasses the measurement.
    """
    problems = []
    fm = sorted(_refs_on(n.FORCE_MINUS))
    if fm != ["F2", "J7"]:
        problems.append(f"FORCE- holds {fm}, not exactly J7 and F2")
    pins = list(n.ISENSE_HI.get_pins())
    hi = sorted(p.part.ref for p in pins)
    if hi != ["F2", "R10", "TP3", "U6"]:
        problems.append(f"ISENSE_HI holds {hi}; expected exactly one pin "
                        f"each of F2, R10, TP3 and U6")
    if not _has(n.ISENSE_HI, "U6", "+IN"):
        problems.append("U6.+IN is not on ISENSE_HI")
    r10 = next((p for p in pins if p.part.ref == "R10"), None)
    if r10 is None or _two_pin_far(r10.part, r10) not in n.AGND.get_pins():
        problems.append("R10 does not run from ISENSE_HI to AGND")
    if not _has(n.AGND, "U6", "-IN"):
        problems.append("U6.-IN is not on AGND (the shunt's low side)")
    ok = not problems
    return ("FORCE- path = J7 -> F2(fuse) -> ISENSE_HI -> R10 -> AGND, "
            "nothing across the shunt", ok,
            "; ".join(problems) if problems else
            f"FORCE-={fm}, ISENSE_HI={hi}, R10 to AGND")


def _series_walk(start_net, stop_nets, origin, limit=4):
    """Two-pin parts in series from `start_net` until a net in `stop_nets`.

    `origin` is the ref the walk starts from (not part of the chain).
    Returns ([(part, pin_in, pin_out)], end_net), pin_in being the pin on the
    origin side, or (None, why) if the chain branches or does not reach a
    stop net within `limit` parts.
    """
    chain, net, seen = [], start_net, {origin}
    for _ in range(limit):
        pins = [p for p in net.get_pins() if p.part.ref not in seen]
        if len(pins) != 1 or len(pins[0].part.pins) != 2:
            return None, (f"{net.name} branches or ends at "
                          f"{sorted(p.part.ref for p in pins)}")
        p = pins[0]
        far = _two_pin_far(p.part, p)
        chain.append((p.part, p, far))
        seen.add(p.part.ref)
        net = _net_of(far)
        if net is None:
            return None, f"{p.part.ref}.{far.name} is unconnected"
        if net.name in stop_nets:
            return chain, net
    return None, f"no rail within {limit} parts"


def a_dut_disconnect_needs_both_rails(n, circuit):
    """U12's LED is lit only by the +15VA -> -15VA difference, through a zener.

    The string must run from +15VA to -15VA with no tap to AGND or any other
    net (a tap would let ONE rail light the LED), and must hold a zener
    pointing against the current (cathode towards +15VA): that is the
    threshold which keeps U12 open with either rail absent.
    """
    problems = []
    a, k = _pin(circuit, "U12", "A"), _pin(circuit, "U12", "K")
    if a is None or k is None:
        return ("Photorelay U12 (DUT disconnect) needs both +/-15 V rails",
                False, "U12 or its LED pins are missing")
    rails = {"+15VA", "-15VA"}
    up, end_up = _series_walk(_net_of(a), rails, "U12")
    down, end_down = _series_walk(_net_of(k), rails, "U12")
    if up is None or down is None:
        problems.append(f"LED string is not a plain series chain: "
                        f"anode side {end_up if up is None else 'ok'}, "
                        f"cathode side {end_down if down is None else 'ok'}")
    else:
        if (end_up.name, end_down.name) != ("+15VA", "-15VA"):
            problems.append(f"anode side ends on {end_up.name}, cathode side "
                            f"on {end_down.name}")
        parts = up + down
        # Current runs +15VA -> anode chain -> LED -> cathode chain -> -15VA,
        # entering anode-side parts at pin_out and cathode-side parts at
        # pin_in. A zener that blocks is one the current enters at K.
        entry = [(part, pin_out) for part, _i, pin_out in up] + \
                [(part, pin_in) for part, pin_in, _o in down]
        ok_z = [part.ref for part, pin in entry
                if part.fields.get("KiCad_Lib") == "Device:D_Zener"
                and pin.name == "K"]
        if len(ok_z) != 1:
            problems.append(f"expected one reverse-biased zener in the string, "
                            f"found {ok_z or 'none'}")
        if not any(part.ref[0] == "R" for part, _i, _o in parts):
            problems.append("no current-limiting resistor in the string")
    for net in (_net_of(a), _net_of(k)):
        if net is not None and net.name in ("AGND", "DGND"):
            problems.append(f"U12 LED pin on {net.name}")
    # Compare by membership: SKiDL may hand back a merged alias of a Net.
    if not (_has(n.FORCE_PLUS, "U12", "OUT1")
            or _has(n.FORCE_PLUS, "U12", "OUT2")):
        problems.append("U12's output does not switch FORCE+")
    ok = not problems
    return ("Photorelay U12 (DUT disconnect) needs both +/-15 V rails", ok,
            "; ".join(problems) if problems else
            "+15VA -> " + " -> ".join(p.ref for p, _i, _o in reversed(up))
            + " -> LED -> " + " -> ".join(p.ref for p, _i, _o in down)
            + " -> -15VA; output in FORCE+")


def a_dead_rail_clamps(n, circuit):
    """Each +/-15 V rail has a Schottky to AGND, reverse-biased in service."""
    plus = [p.part.ref for p in n.P15VA.get_pins()
            if p.part.fields.get("KiCad_Lib") == "Device:D_Schottky"
            and p.name == "K" and _has(n.AGND, p.part.ref, "A")]
    minus = [p.part.ref for p in n.N15VA.get_pins()
             if p.part.fields.get("KiCad_Lib") == "Device:D_Schottky"
             and p.name == "A" and _has(n.AGND, p.part.ref, "K")]
    ok = bool(plus) and bool(minus)
    return ("Dead-rail clamps: Schottky AGND -> +15VA and -15VA -> AGND", ok,
            f"+15VA clamp={plus or 'none'}, -15VA clamp={minus or 'none'}")


def a_kelvin_independent(n):
    # Kelvin sense nets must share NO pins with the force nets.
    sp = _pin_ids(n.SENSE_PLUS) & _pin_ids(n.FORCE_PLUS)
    sm = (_pin_ids(n.SENSE_MINUS) & _pin_ids(n.FORCE_MINUS)) | \
        (_pin_ids(n.SENSE_MINUS) & _pin_ids(n.ISENSE_HI))
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


def _pin(circuit, ref, pin_name):
    part = next((p for p in circuit.parts if p.ref == ref), None)
    return None if part is None else part[pin_name]


def _net_of(pin):
    nets = [] if pin is None else list(pin.get_nets())
    return nets[0] if nets else None


def a_bipolar_measurement_zero(n, circuit):
    """Both INA128 outputs, and the ADC's negative input, share one mid-scale.

    The ADS1256 runs from a single +5 V supply, so each input pin must stay
    inside AGND - 0.1 V .. AVDD + 0.1 V. With U6/U7 REF on AGND every negative
    current or voltage fell below that floor. REF on VREF_2V5 moves zero to
    2.500 V, and AINCOM on the SAME net makes the conversion AIN - AINCOM,
    in which the 2.5 V cancels and the sign survives. All three have to move
    together: any one left on AGND puts a 2.5 V offset (or the old clipping)
    back into a channel.
    """
    want = [("U6", "REF"), ("U7", "REF"), ("U8", "AINCOM")]
    off = [f"{r}.{p}" for r, p in want if not _has(n.VREF_2V5, r, p)]
    on_agnd = [f"{r}.{p}" for r, p in want if _has(n.AGND, r, p)]
    ok = not off and not on_agnd
    return ("Measurement zero: U6.REF, U7.REF and ADC AINCOM all on VREF_2V5",
            ok, f"not on VREF_2V5={off or 'none'}, still on AGND="
                f"{on_agnd or 'none'}")


def a_kelvin_attenuator(n, circuit):
    """SENSE+/- reach U7 only through two single-ended dividers to AGND.

    Each U7 input net must hold exactly the INA128 pin, one series resistor
    from its SENSE jack and one shunt resistor to AGND. A resistor straight
    across the two inputs would pass V_K/R through the DUT's return and the
    R10 shunt and corrupt the current reading, so its absence is checked too.
    """
    problems = []
    legs = (("+IN", n.SENSE_PLUS, "SENSE+"), ("-IN", n.SENSE_MINUS, "SENSE-"))
    amp_nets = []
    for pin_name, sense_net, label in legs:
        net = _net_of(_pin(circuit, "U7", pin_name))
        if net is None:
            problems.append(f"U7.{pin_name} is not connected")
            continue
        amp_nets.append(net)
        others = [p for p in net.get_pins() if p.part.ref != "U7"]
        series, shunt, stray = [], [], []
        for p in others:
            if p.part.ref[0] != "R" or len(p.part.pins) != 2:
                stray.append(p.part.ref)
                continue
            far = next(q for q in p.part.pins if q is not p)
            if _has(sense_net, p.part.ref, far.name):
                series.append(p.part.ref)
            elif _has(n.AGND, p.part.ref, far.name):
                shunt.append(p.part.ref)
            else:
                stray.append(p.part.ref)
        if len(series) != 1 or len(shunt) != 1 or stray:
            problems.append(f"U7.{pin_name}: series from {label}={series}, "
                            f"shunt to AGND={shunt}, other={stray}")
    if len(amp_nets) == 2:
        across = ({p.part.ref for p in amp_nets[0].get_pins()}
                  & {p.part.ref for p in amp_nets[1].get_pins()}) - {"U7"}
        if across:
            problems.append(f"part(s) across U7's inputs: {sorted(across)}")
    ok = not problems
    return ("Kelvin attenuator: SENSE+/- -> matched dividers to AGND -> U7",
            ok, "; ".join(problems) if problems else
            "U7.+IN and U7.-IN each fed by one series R from its SENSE jack "
            "and one shunt R to AGND, nothing across the inputs")


def _two_pin_far(part, pin):
    """The other pin of a two-pin part."""
    return next(q for q in part.pins if q is not pin)


def a_sweep_midpoint_from_dac_b(n, circuit):
    """VREF_HALF is DAC channel B halved, not a fixed VREF_2V5 divider (F2).

    R1 from U4.VOUTB to U3A's input, R2 from there to AGND: both channels
    then power up at zero scale together and V_SWEEP starts at 0 V. Any R1/R2
    pin on VREF_2V5 brings back the -10 V power-up output.
    """
    problems = []
    vb = _net_of(_pin(circuit, "U4", "VOUTB"))
    mid = _net_of(_pin(circuit, "U3", "+INA"))
    if vb is None or not _has(vb, "R1"):
        problems.append(f"R1 not on U4.VOUTB ({vb.name if vb else None})")
    if mid is None or not (_has(mid, "R1") and _has(mid, "R2")):
        problems.append("U3.+INA is not the R1/R2 midpoint")
    if not _has(n.AGND, "R2"):
        problems.append("R2 does not return to AGND")
    on_ref = sorted(r for r in ("R1", "R2") if _has(n.VREF_2V5, r))
    if on_ref:
        problems.append(f"{on_ref} still on VREF_2V5")
    return ("Sweep midpoint = U4 channel B / 2 (R1 on VOUTB, R2 to AGND)",
            not problems, "; ".join(problems) or
            f"VOUTB net {vb.name}: {sorted(_refs_on(vb))}; midpoint "
            f"{mid.name}: {sorted(_refs_on(mid))}")


def a_dac_reference_gate(n, circuit):
    """U4's VREF pin is VREF_2V5 only through U11, and 0 V otherwise (F2)."""
    problems = []
    dac_ref = _net_of(_pin(circuit, "U4", "VREF"))
    if dac_ref is None or not _has(dac_ref, "U11", "D"):
        problems.append("U4.VREF is not on U11.D")
    else:
        # Virtual symbols (the PWR_FLAG that states DAC_VREF's source to ERC)
        # are schematic-only and carry no current.
        members = {(p.part.ref, p.name) for p in dac_ref.get_pins()
                   if not p.part.fields.get("Symbol_Class")}
        extra = {m for m in members if m[0] not in ("U4", "U11", "R35")}
        if extra:
            problems.append(f"other parts on the DAC's reference: {extra}")
        if not _has(dac_ref, "R35"):
            problems.append("no pull-down (R35) on the DAC's reference")
    r35 = next((p for p in circuit.parts if p.ref == "R35"), None)
    if r35 is not None and not _has(n.AGND, "R35"):
        problems.append("R35 does not return to AGND")
    if not _has(n.VREF_2V5, "U11", "S"):
        problems.append("U11.S is not on VREF_2V5")
    if not _has(n.DAC_ARM, "U11", "SEL"):
        problems.append("U11.SEL is not driven by DAC_ARM")
    if not (_has(n.P5VA, "U11", "VDD") and _has(n.AGND, "U11", "GND")):
        problems.append("U11 is not powered from +5VA/AGND")
    return ("DAC reference gate: U4.VREF = VREF_2V5 via U11 only when armed, "
            "R35 to AGND otherwise", not problems,
            "; ".join(problems) or
            f"DAC ref net {dac_ref.name}: {sorted(_refs_on(dac_ref))}")


def a_arm_needs_a_heartbeat(n, circuit):
    """Nothing but a toggling PC5 can raise DAC_ARM (F2's fail-safe property).

    DAC_ARM may hold only: U11.SEL (an input), D6.K (the pump rectifier),
    capacitors, and resistors whose other end is a ground. D6's other diode
    clamps to DGND; the pump's AC node is fed only through C47; C47 is fed
    only through R36 from PC5. A resistor from DAC_ARM to any rail, or a DC
    path past C47, would let a static level (a reset, a halt, a stuck pin)
    hold the output armed — which is exactly what this design must not do.
    """
    problems = []
    grounds = (n.AGND, n.DGND)
    for p in n.DAC_ARM.get_pins():
        ref, name = p.part.ref, p.name
        if (ref, name) in (("U11", "SEL"), ("D6", "K")):
            continue
        if ref[0] == "C" and len(p.part.pins) == 2:
            far = _two_pin_far(p.part, p)
            if not any(_has(gn, ref, far.name) for gn in grounds):
                problems.append(f"{ref} on DAC_ARM does not go to ground")
            continue
        if ref[0] == "R" and len(p.part.pins) == 2:
            far = _two_pin_far(p.part, p)
            if not any(_has(gn, ref, far.name) for gn in grounds):
                problems.append(f"{ref} gives DAC_ARM a DC path to "
                                f"{[x.name for x in far.get_nets()]}")
            continue
        problems.append(f"unexpected {ref}.{name} on DAC_ARM")
    if not _has(n.DGND, "D6", "A"):
        problems.append("D6.A (clamp) is not on DGND")
    ac = _net_of(_pin(circuit, "D6", "COM"))
    ac_refs = _refs_on(ac) if ac is not None else set()
    if ac_refs != {"D6", "C47"}:
        problems.append(f"pump AC node holds {sorted(ac_refs)}, expected "
                        f"D6 and C47 only")
    hb = _net_of(_pin(circuit, "U9", "PC5"))
    if hb is None or _refs_on(hb) != {"U9", "R36"}:
        problems.append(f"PC5 does not drive the pump through R36 alone "
                        f"({sorted(_refs_on(hb)) if hb else None})")
    return ("DAC_ARM: only a toggling PC5 can arm it (AC-coupled pump, no DC "
            "path from any rail)", not problems,
            "; ".join(problems) or
            f"DAC_ARM: {sorted(_refs_on(n.DAC_ARM))}")


def a_cs_dac_pulled_up(n, circuit):
    """SYNC idles high while the MCU's pins float (reset, boot)."""
    ups = []
    for p in n.CS_DAC.get_pins():
        if p.part.ref[0] == "R" and len(p.part.pins) == 2:
            far = _two_pin_far(p.part, p)
            rails = [x.name for x in far.get_nets()]
            if any(r in ("+5VA", "+3V3D") for r in rails):
                ups.append(f"{p.part.ref} to {rails[0]}")
    return ("CS_DAC (DAC SYNC) has a pull-up to a supply rail", bool(ups),
            f"pull-ups: {ups or 'none'}")


ALL_ASSERTIONS = [
    a_shared_reference,
    a_sweep_midpoint_from_dac_b,
    a_dac_reference_gate,
    a_arm_needs_a_heartbeat,
    a_cs_dac_pulled_up,
    a_force_path_chain,
    a_force_minus_chain,
    a_dut_disconnect_needs_both_rails,
    a_dead_rail_clamps,
    a_kelvin_independent,
    a_adc_on_spi2,
    a_dac_on_spi1,
    a_spi_buses_separate,
    a_star_ground,
    a_net_manifest,
    a_usb_c_pads_fully_connected,
    a_bipolar_measurement_zero,
    a_kelvin_attenuator,
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


def main():
    """Standalone gate: build the circuit and run every assertion.

    Exit 1 on any failure. ``tools/test_guard_fails.sh`` uses this to prove
    that each assertion fails when the topology it protects is removed.
    """
    import os
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    import main as project_main
    n, _parts, circuit = project_main.build()
    try:
        run_assertions(n, circuit)
    except AssertionError as exc:
        print(f"\nASSERTIONS: FAIL — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
