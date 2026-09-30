#!/usr/bin/env python3
"""Combine oemof/Pyomo scenario LPs into a multi-scenario LP.

The objective is the raw sum of the source objectives.  Investment variables
are made non-anticipative by equating every non-reference scenario to the
reference scenario.

Usage:
    python create_multi_scenario_lp.py output.lp.gz \
        --scenario base base.lp.gz \
        --scenario low low.lp.gz \
        --scenario high high.lp.gz \
        --reference-scenario base

Example:
    python create_multi_scenario_lp.py /scratch/opt/lmehl/instances/2ssp/integrated/debug/oemofB3_debug_3cm_br_ivm_4scens_elec_heat_1p2.lp \
        --scenario base /scratch/opt/lmehl/instances/2ssp/single_scenarios/debug/oemofB3_debug_3costmods_boundsremoved_investvarsmerged.lp \
        --scenario elec1p2 /scratch/opt/lmehl/instances/2ssp/single_scenarios/debug/oemofB3_debug_3costmods_boundsremoved_investvarsmerged_electricity1p2.lp \
        --scenario heatdc1p2 /scratch/opt/lmehl/instances/2ssp/single_scenarios/debug/oemofB3_debug_3costmods_boundsremoved_investvarsmerged_heat_decentral1p2.lp \
        --scenario elecheatdc1p2 /scratch/opt/lmehl/instances/2ssp/single_scenarios/debug/oemofB3_debug_3costmods_boundsremoved_investvarsmerged_electricity1p2_heat_decentral1p2.lp \
        --reference-scenario elecheatdc1p2

    python create_multi_scenario_lp.py /scratch/opt/lmehl/instances/2ssp/integrated/3cm-br-ivm_base_elec1p01_heatboth1p01_elecheatboth1p01.lp.gz \
        --scenario base /scratch/opt/lmehl/instances/base/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged.lp \
        --scenario elec1p01 /scratch/opt/lmehl/instances/scenario_catalogue/electricity/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged_electricity_d1p01.lp.gz  \
        --scenario heatboth1p01 /scratch/opt/lmehl/instances/scenario_catalogue/heat_both/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged_heat_central_heat_decentral_d1p01.lp.gz \
        --scenario elecheatboth1p01 /scratch/opt/lmehl/instances/2ssp/single_scenarios/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged_heat_central_heat_decentral_electricity_d1p01.lp.gz \
        --reference-scenario elecheatboth1p01

    python create_multi_scenario_lp.py /scratch/opt/lmehl/instances/2ssp/integrated/nomods_base_elec1p01_heatboth1p01_elecheatboth1p01.lp.gz \
        --scenario base /scratch/opt/lmehl/instances/base/2050-100-el_eff-nothingmodified.lp \
        --scenario elec1p01 /scratch/opt/lmehl/instances/scenario_catalogue/electricity/2050-100-el_eff-nothingmodified_electricity_d1p01.lp.gz \
        --scenario heatboth1p01 /scratch/opt/lmehl/instances/scenario_catalogue/heat_both/2050-100-el_eff-nothingmodified_heat_central_heat_decentral_d1p01.lp.gz \
        --scenario elecheatboth1p01 /scratch/opt/lmehl/instances/2ssp/single_scenarios/2050-100-el_eff-nothingmodified_heat_central_heat_decentral_electricity_d1p01.lp.gz \
        --reference-scenario elecheatboth1p01
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

from create_two_scenario_lp import (
    InputError,
    investment_variables,
    open_lp,
    scenario_name,
    write_section,
)

SCENARIO_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")


def merge(
    scenarios: list[tuple[str, Path]],
    output_path: Path,
    reference_scenario: str | None = None,
) -> int:
    """Write a multi-scenario LP and return its non-anticipativity row count."""
    if len(scenarios) < 2:
        raise InputError("At least two scenarios are required.")

    names = [name for name, _path in scenarios]
    if len(set(names)) != len(names):
        raise InputError("Scenario identifiers must be unique.")
    if any(not SCENARIO_RE.fullmatch(name) for name in names):
        raise InputError("Scenario identifiers must match [A-Za-z_][A-Za-z0-9_-]*.")
    if reference_scenario is None:
        reference_scenario = names[0]
    if reference_scenario not in names:
        raise InputError(f"Reference scenario is not an input scenario: {reference_scenario}")

    for _name, path in scenarios:
        if not path.is_file():
            raise InputError(f"Input LP does not exist: {path}")
        if path.resolve() == output_path.resolve():
            raise InputError("Output file must differ from every input LP.")

    investments_by_scenario = [(name, investment_variables(path)) for name, path in scenarios]
    reference_investments = dict(investments_by_scenario)[reference_scenario]
    for name, investments in investments_by_scenario:
        if investments == reference_investments:
            continue
        only_reference = sorted(reference_investments - investments)
        only_current = sorted(investments - reference_investments)
        details = []
        if only_reference:
            details.append("only in reference: " + ", ".join(only_reference[:3]))
        if only_current:
            details.append(f"only in {name}: " + ", ".join(only_current[:3]))
        raise InputError("Investment variables do not match exactly (" + "; ".join(details) + ").")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=output_path.stem + ".",
        suffix=output_path.suffix,
        dir=output_path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)

    try:
        with open_lp(temporary_path, "wt") as output:
            output.write("\\* Multi-scenario LP generated by create_multi_scenario_lp.py *\\\n\n")
            output.write("min\nobjective:\n")
            for name, path in scenarios:
                write_section(output, path, "objective", name)
            output.write("s.t.\n")
            for name, path in scenarios:
                write_section(output, path, "constraints", name)
            output.write("\n")
            for variable in sorted(reference_investments):
                for name, _path in scenarios:
                    if name == reference_scenario:
                        continue
                    output.write(f"c_e_nonanticipativity_({variable}_{reference_scenario}_{name})_:\n")
                    output.write(f"+1 {scenario_name(variable, reference_scenario)}\n")
                    output.write(f"-1 {scenario_name(variable, name)}\n")
                    output.write("= 0\n\n")
            output.write("bounds\n")
            for name, path in scenarios:
                write_section(output, path, "bounds", name)
            output.write("End\n")
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return len(reference_investments) * (len(scenarios) - 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_lp", type=Path, help="output .lp or .lp.gz model")
    parser.add_argument(
        "--scenario",
        action="append",
        nargs=2,
        metavar=("NAME", "LP"),
        required=True,
        help="scenario identifier and source .lp or .lp.gz model; repeat for each scenario",
    )
    parser.add_argument(
        "--reference-scenario",
        help="scenario used on the +1 side of non-anticipativity rows (default: first scenario)",
    )
    args = parser.parse_args()
    scenarios = [(name, Path(path)) for name, path in args.scenario]
    try:
        count = merge(scenarios, args.output_lp, args.reference_scenario)
    except InputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    print(f"Wrote {args.output_lp} with {count} non-anticipativity constraints.")


if __name__ == "__main__":
    main()
