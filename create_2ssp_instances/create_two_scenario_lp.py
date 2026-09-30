#!/usr/bin/env python3
"""Combine two oemof/Pyomo scenario LPs into a two-scenario LP.

The objective is the raw sum of the two source objectives.  With equal
probabilities this is a constant multiple of the expected-cost objective,
provided the two source models have identical investment-cost coefficients.

Usage:
    python create_two_scenario_lp.py first.lp.gz second.lp.gz two_stage.lp.gz \
        --scenario-a low --scenario-b high

Example:
    python /scratch/htc/lmehl/master_thesis/instances_scripts/create_2ssp_instances/create_two_scenario_lp.py \
        /scratch/opt/lmehl/instances/base/2050-100-el_eff-nothingmodified.lp \
        /scratch/opt/lmehl/instances/scenario_catalogue/2050-100-el_eff-nothingmodified_d1p01.lp.gz \
        /scratch/opt/lmehl/instances/2ssp/integrated/nomods_base_d1p01.lp.gz \
        --scenario-a base --scenario-b d1p01

    python /scratch/htc/lmehl/master_thesis/instances_scripts/create_2ssp_instances/create_two_scenario_lp.py \
        /scratch/opt/lmehl/instances/base/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged.lp \
        /scratch/opt/lmehl/instances/scenario_catalogue/electricity/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged_electricity_d1p05.lp.gz \
        /scratch/opt/lmehl/instances/2ssp/integrated/3cm-br-ivm_base_elec1p05.lp.gz \
        --scenario-a base --scenario-b elec1p05

    python /scratch/htc/lmehl/master_thesis/instances_scripts/create_2ssp_instances/create_two_scenario_lp.py \
        /scratch/opt/lmehl/instances/base/2050-100-el_eff-nothingmodified.lp \
        /scratch/opt/lmehl/instances/scenario_catalogue/electricity/2050-100-el_eff-nothingmodified_electricity_d1p05.lp.gz \
        /scratch/opt/lmehl/instances/2ssp/integrated/nomods_base_elec1p05.lp.gz \
        --scenario-a base --scenario-b elec1p05

    python /scratch/htc/lmehl/master_thesis/instances_scripts/create_2ssp_instances/create_two_scenario_lp.py \
        /scratch/opt/lmehl/instances/base/2050-100-el_eff-nothingmodified.lp \
        /scratch/opt/lmehl/instances/2ssp/single_scenarios/factor1p2_on4carriers/nomods/2050-100-el_eff-nothingmodified_electricity1p2.lp.gz \
        /scratch/opt/lmehl/instances/2ssp/integrated/nomods_base_elec1p2.lp.gz \
        --scenario-a base --scenario-b elec1p2
"""

from __future__ import annotations

import argparse
import gzip
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterator, TextIO


INVESTMENT_NAME_RE = re.compile(
    r"(?:"
    r"InvestmentFlowBlock_(?:invest|total|capacity|flowcapacity)"
    r"|GenericInvestmentStorageBlock_(?:invest|total|contentcapacity)"
    r")\([^()\r\n]+\)"
)
IDENTIFIER_RE = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_.]*)\((?P<key>[^()\r\n]*)\)")
BARE_CONSTRAINT_HEADER_RE = re.compile(
    r"^(?P<indent>\s*)(?P<name>[A-Za-z_][A-Za-z0-9_.]*)(?P<colon>:\s*)$"
)
BOUND_RE = re.compile(
    r"^\s*(?:[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*"
    r"<=\s*[A-Za-z_][A-Za-z0-9_.]*\([^()\r\n]+\)\s*<="
)
SCENARIO_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class InputError(RuntimeError):
    """The source LP does not use the expected oemof/Pyomo layout."""


def open_lp(path: Path, mode: str) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, mode, encoding="utf-8", newline="")
    return path.open(mode, encoding="utf-8", newline="")


def is_min_or_max(line: str) -> bool:
    return line.strip().lower() in {"min", "minimize", "max", "maximize"}


def is_subject_to(line: str) -> bool:
    return line.strip().lower() in {"s.t.", "st", "subject to", "such that"}


def is_bounds_header(line: str) -> bool:
    return line.strip().lower() == "bounds"


def source_sections(path: Path) -> Iterator[tuple[str, str]]:
    """Yield objective, constraint, and bound lines from a Pyomo LP file."""
    state = "before_objective"
    saw_subject_to = False

    with open_lp(path, "rt") as source:
        for line_no, line in enumerate(source, 1):
            if state == "before_objective":
                if is_min_or_max(line):
                    state = "objective_header"
                continue

            if state == "objective_header":
                if line.strip().endswith(":"):
                    state = "objective"
                    continue
                if line.strip():
                    raise InputError(f"{path}:{line_no}: expected objective label.")
                continue

            if state == "objective":
                if is_subject_to(line):
                    state = "constraints"
                    saw_subject_to = True
                else:
                    yield "objective", line
                continue

            if state == "constraints":
                if is_bounds_header(line):
                    state = "bounds"
                elif BOUND_RE.match(line):
                    state = "bounds"
                    yield "bounds", line
                elif line.strip().lower() == "end":
                    raise InputError(f"{path}:{line_no}: LP has no bounds section.")
                else:
                    yield "constraints", line
                continue

            if state == "bounds":
                if line.strip().lower() != "end":
                    yield "bounds", line

    if not saw_subject_to:
        raise InputError(f"{path}: missing 's.t.' or 'Subject To' section.")
    if state != "bounds":
        raise InputError(f"{path}: could not identify the first bound line.")


def investment_variables(path: Path) -> set[str]:
    variables = set()
    for _section, line in source_sections(path):
        variables.update(INVESTMENT_NAME_RE.findall(line))
    if not variables:
        raise InputError(f"{path}: no supported investment variables were found.")
    return variables


def scenario_name(variable: str, scenario: str) -> str:
    match = IDENTIFIER_RE.fullmatch(variable)
    if match is None:
        raise AssertionError(f"Unsupported variable syntax: {variable}")
    return f"{match.group('name')}({match.group('key')}_{scenario})"


def rewrite_identifiers(line: str, scenario: str) -> str:
    return IDENTIFIER_RE.sub(
        lambda match: f"{match.group('name')}({match.group('key')}_{scenario})",
        line,
    )


def write_section(output: TextIO, path: Path, section: str, scenario: str) -> None:
    for current_section, line in source_sections(path):
        if current_section == section:
            indexed_line = rewrite_identifiers(line, scenario)
            if section == "constraints":
                indexed_line = BARE_CONSTRAINT_HEADER_RE.sub(
                    lambda match: (
                        f"{match.group('indent')}{match.group('name')}"
                        f"{'' if match.group('name').endswith('_') else '_'}{scenario}_"
                        f"{match.group('colon')}"
                    ),
                    indexed_line,
                )
            output.write(indexed_line)


def merge(
    first: Path,
    second: Path,
    output_path: Path,
    scenario_a: str,
    scenario_b: str,
) -> int:
    if not first.is_file() or not second.is_file():
        raise InputError("Both input LP files must exist.")
    if first.resolve() == output_path.resolve() or second.resolve() == output_path.resolve():
        raise InputError("Output file must differ from both input files.")
    if scenario_a == scenario_b:
        raise InputError("Scenario identifiers must differ.")
    if not SCENARIO_RE.fullmatch(scenario_a) or not SCENARIO_RE.fullmatch(scenario_b):
        raise InputError("Scenario identifiers must match [A-Za-z_][A-Za-z0-9_]*.")

    first_investments = investment_variables(first)
    second_investments = investment_variables(second)
    if first_investments != second_investments:
        only_first = sorted(first_investments - second_investments)
        only_second = sorted(second_investments - first_investments)
        details = []
        if only_first:
            details.append("only in first: " + ", ".join(only_first[:3]))
        if only_second:
            details.append("only in second: " + ", ".join(only_second[:3]))
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
            output.write("\\* Two-scenario LP generated by create_two_scenario_lp.py *\\\n\n")
            output.write("min\nobjective:\n")
            write_section(output, first, "objective", scenario_a)
            write_section(output, second, "objective", scenario_b)
            output.write("s.t.\n")
            write_section(output, first, "constraints", scenario_a)
            write_section(output, second, "constraints", scenario_b)
            output.write("\n")
            for variable in sorted(first_investments):
                output.write(f"c_e_nonanticipativity_({variable}_{scenario_a}_{scenario_b})_:\n")
                output.write(f"+1 {scenario_name(variable, scenario_a)}\n")
                output.write(f"-1 {scenario_name(variable, scenario_b)}\n")
                output.write("= 0\n\n")
            output.write("bounds\n")
            write_section(output, first, "bounds", scenario_a)
            write_section(output, second, "bounds", scenario_b)
            output.write("End\n")
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return len(first_investments)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first_lp", type=Path, help="first .lp or .lp.gz scenario model")
    parser.add_argument("second_lp", type=Path, help="second .lp or .lp.gz scenario model")
    parser.add_argument("output_lp", type=Path, help="output .lp or .lp.gz model")
    parser.add_argument("--scenario-a", required=True, help="identifier appended to first-model names")
    parser.add_argument("--scenario-b", required=True, help="identifier appended to second-model names")
    args = parser.parse_args()
    try:
        count = merge(args.first_lp, args.second_lp, args.output_lp, args.scenario_a, args.scenario_b)
    except InputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    print(f"Wrote {args.output_lp} with {count} non-anticipativity constraints.")


if __name__ == "__main__":
    main()
