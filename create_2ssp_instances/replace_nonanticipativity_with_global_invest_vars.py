#!/usr/bin/env python3
"""Replace indexed investment variables with global variables in an integrated LP.

The input must be an LP created by create_two_scenario_lp.py or
create_multi_scenario_lp.py.  The transformation replaces every supported
investment variable indexed by a supplied scenario with one global variable,
then removes the now-redundant non-anticipativity constraints.

Usage:
    python replace_nonanticipativity_with_global_invest_vars.py input.lp.gz output.lp.gz \
        --scenario base --scenario elec1p2 --scenario heat_decentral1p2
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import re
import sys
import tempfile
from pathlib import Path

from create_two_scenario_lp import InputError, is_bounds_header, is_subject_to, open_lp


INVESTMENT_IDENTIFIER_RE = re.compile(
    r"(?P<name>InvestmentFlowBlock_(?:invest|total|capacity|flowcapacity)"
    r"|GenericInvestmentStorageBlock_(?:invest|total|contentcapacity))"
    r"\((?P<key>[^()\r\n]+)\)"
)
NONANTICIPATIVITY_HEADER_RE = re.compile(r"^\s*c_e_nonanticipativity_\(.*\)_:\s*$")
CONSTRAINT_HEADER_RE = re.compile(r"^\s*[^+\-=\s][^:\r\n]*:\s*$")
SCENARIO_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")


def globalize_line(line: str, scenarios: list[str], global_suffix: str) -> tuple[str, set[str]]:
    replaced = set()

    def replace(match: re.Match[str]) -> str:
        key = match.group("key")
        # Scenario names can be suffixes of compound names, e.g.
        # heat_decentral1p2 and elec1p2_heat_decentral1p2.
        for scenario in sorted(scenarios, key=len, reverse=True):
            suffix = f"_{scenario}"
            if key.endswith(suffix):
                replaced.add(scenario)
                return f"{match.group('name')}({key[:-len(suffix)]}_{global_suffix})"
        return match.group(0)

    return INVESTMENT_IDENTIFIER_RE.sub(replace, line), replaced


def objective_term(line: str, variable_match: re.Match[str]) -> Decimal:
    prefix = line[:variable_match.start()].strip().replace(" ", "")
    suffix = line[variable_match.end():].strip()
    if suffix or not prefix:
        raise InputError(f"Unsupported objective term for global investment variable: {line.rstrip()}")
    try:
        return Decimal(prefix)
    except InvalidOperation as error:
        raise InputError(f"Unsupported objective coefficient: {prefix!r}") from error


def bound_value(text: str) -> Decimal | None:
    value = text.strip().lower()
    if value in {"-inf", "-infinity"}:
        return None
    if value in {"+inf", "inf", "+infinity", "infinity"}:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise InputError(f"Unsupported investment bound value: {text!r}") from error


def range_bound(line: str, variable_match: re.Match[str]) -> tuple[Decimal | None, Decimal | None]:
    prefix, suffix = line[:variable_match.start()].strip(), line[variable_match.end():].strip()
    if not prefix.endswith("<=") or not suffix.startswith("<="):
        raise InputError(f"Unsupported investment bound: {line.rstrip()}")
    return bound_value(prefix[:-2]), bound_value(suffix[2:])


def format_decimal(value: Decimal) -> str:
    return format(value, "f")


def format_objective_coefficient(value: Decimal) -> str:
    return ("+" if value >= 0 else "") + format_decimal(value)


def format_bound(value: Decimal | None, lower: bool) -> str:
    if value is None:
        return "-inf" if lower else "+inf"
    return format_decimal(value)


def merge(input_path: Path, output_path: Path, scenarios: list[str], global_suffix: str = "global") -> tuple[int, int]:
    if not input_path.is_file():
        raise InputError(f"Input LP does not exist: {input_path}")
    if input_path.resolve() == output_path.resolve():
        raise InputError("Output file must differ from the input LP.")
    if len(scenarios) < 2 or len(set(scenarios)) != len(scenarios):
        raise InputError("Provide at least two unique scenario identifiers.")
    if any(not SCENARIO_RE.fullmatch(scenario) for scenario in scenarios):
        raise InputError("Scenario identifiers must match [A-Za-z_][A-Za-z0-9_-]*.")
    if not SCENARIO_RE.fullmatch(global_suffix):
        raise InputError("--global-suffix must match [A-Za-z_][A-Za-z0-9_-]*.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="wb", prefix=output_path.stem + ".", suffix=output_path.suffix, dir=output_path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)

    removed_rows = 0
    replaced_scenarios = set()
    objective_coefficients: dict[str, dict[str, Decimal]] = {}
    global_bounds: dict[str, dict[str, tuple[Decimal | None, Decimal | None]]] = {}
    state = "before_constraints"
    skipping_row = False
    try:
        with open_lp(input_path, "rt") as source, open_lp(temporary_path, "wt") as output:
            for line in source:
                if state == "before_constraints" and is_subject_to(line):
                    for variable, coefficients in objective_coefficients.items():
                        output.write(f"{format_objective_coefficient(sum(coefficients.values(), Decimal()))} {variable}\n")
                    state = "constraints"
                elif state == "constraints" and is_bounds_header(line):
                    state = "bounds"
                    skipping_row = False

                if state == "constraints" and NONANTICIPATIVITY_HEADER_RE.fullmatch(line):
                    skipping_row = True
                    removed_rows += 1
                    continue
                if skipping_row:
                    if state == "bounds" or CONSTRAINT_HEADER_RE.fullmatch(line):
                        skipping_row = False
                    else:
                        continue

                rewritten, replaced = globalize_line(line, scenarios, global_suffix)
                replaced_scenarios.update(replaced)
                variable = INVESTMENT_IDENTIFIER_RE.search(rewritten) if replaced else None
                if state == "before_constraints" and variable is not None:
                    if len(replaced) != 1:
                        raise InputError(f"Objective term must contain one indexed investment variable: {line.rstrip()}")
                    scenario = replaced.pop()
                    objective_coefficients.setdefault(variable.group(0), {}).setdefault(scenario, Decimal())
                    objective_coefficients[variable.group(0)][scenario] += objective_term(rewritten, variable)
                    continue
                if state == "bounds" and variable is not None:
                    if variable is None:
                        raise AssertionError("Global investment bound lost its identifier")
                    name = variable.group(0)
                    if len(replaced) != 1:
                        raise InputError(f"Bound must contain one indexed investment variable: {line.rstrip()}")
                    global_bounds.setdefault(name, {})[replaced.pop()] = range_bound(rewritten, variable)
                    continue
                if state == "bounds" and line.strip().lower() == "end":
                    for variable, bounds in global_bounds.items():
                        lower = max((item[0] for item in bounds.values() if item[0] is not None), default=None)
                        upper = min((item[1] for item in bounds.values() if item[1] is not None), default=None)
                        if lower is not None and upper is not None and lower > upper:
                            raise InputError(f"Global investment bounds are empty for {variable}")
                        output.write(f"   {format_bound(lower, True)} <= {variable} <= {format_bound(upper, False)}\n")
                output.write(rewritten)
        if not removed_rows:
            raise InputError("No non-anticipativity constraints were found.")
        missing = set(scenarios) - replaced_scenarios
        if missing:
            raise InputError(f"No indexed investment variables were replaced for scenarios: {sorted(missing)}")
        for variable, coefficients in objective_coefficients.items():
            if set(coefficients) != set(scenarios) or len(set(coefficients.values())) != 1:
                print(f"WARNING: objective costs differ across scenarios for {variable}: {coefficients}", file=sys.stderr)
        for variable, bounds in global_bounds.items():
            if set(bounds) != set(scenarios) or len(set(bounds.values())) != 1:
                print(f"WARNING: bounds differ across scenarios for {variable}: {bounds}; using their intersection.", file=sys.stderr)
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return removed_rows, len(global_bounds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_lp", type=Path)
    parser.add_argument("output_lp", type=Path)
    parser.add_argument("--scenario", action="append", required=True, help="scenario identifier used in the integrated LP; repeat for every scenario")
    parser.add_argument("--global-suffix", default="global", help="suffix for shared investment variables")
    args = parser.parse_args()
    try:
        removed, global_variables = merge(args.input_lp, args.output_lp, args.scenario, args.global_suffix)
    except InputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    print(f"Wrote {args.output_lp}: removed {removed} non-anticipativity rows and created {global_variables} global investment variables.")


if __name__ == "__main__":
    main()
