#!/usr/bin/env python3
"""
remove_small_objective_coefficients.py

Remove small objective coefficients from an LP file.

Usage:
    python remove_small_objective_coefficients.py input.lp threshold
    python remove_small_objective_coefficients.py input.lp threshold output.lp

Examples:
    python remove_small_objective_coefficients.py model.lp 1e-6
    python remove_small_objective_coefficients.py model.lp 1e-6 model_filtered.lp

Behavior
--------
1. In the objective section, remove every complete line whose objective
   coefficient is nonzero and satisfies

       abs(coefficient) <= threshold.

2. Report every removed variable.

3. For time-dependent variables, e.g.

       flow(BB_electricity_BB_electricity_liion_battery_0_0)
       flow(BB_electricity_BB_electricity_liion_battery_0_1)
       flow(BB_electricity_BB_electricity_liion_battery_0_2)

   report only the first removed variable of this time series.

4. Before collapsing such variables into one report entry, check that all
   objective coefficients belonging to the same time series are identical.
   Print a warning if they are not.

5. Scan all non-objective sections for small, nonzero coefficients
   (abs(coefficient) <= threshold) and print warnings if such coefficients
   occur there.

Expected LP structure
---------------------
    objective:
        ...
    s.t.
        ...
    bounds
        ...
    end

The script assumes that each objective term is on a separate line, e.g.

    +5.7830099133661314e-08 InvestmentFlowBlock_invest(...)
    +1e-07 flow(..._0)
    +1e-07 flow(..._1)
"""

from pathlib import Path
import argparse
import math
import re
import sys


# ---------------------------------------------------------------------------
# Regular expressions
# ---------------------------------------------------------------------------

# Floating-point number, including scientific notation.
NUMBER = r"""
[+-]?
(?:
    (?:\d+(?:\.\d*)?)
    |
    (?:\.\d+)
)
(?:[eE][+-]?\d+)?
"""

# Objective line:
#
#     +1e-07 flow(foo_0)
#     - 1e-07 flow(foo_0)
#
# Spaces between sign and number are supported.
OBJECTIVE_TERM_RE = re.compile(
    rf"""
    ^\s*
    (?P<sign>[+-]?)\s*
    (?P<number>
        (?:
            (?:\d+(?:\.\d*)?)
            |
            (?:\.\d+)
        )
        (?:[eE][+-]?\d+)?
    )
    \s+
    (?P<variable>\S+)
    \s*$
    """,
    re.VERBOSE,
)

# A coefficient immediately followed by a variable name.
#
# This is used for checking constraints and other non-objective sections.
# It deliberately requires a variable-like token after the number, so that
# RHS values such as
#
#     ... = 1000
#
# are not interpreted as coefficients.
NONOBJECTIVE_TERM_RE = re.compile(
    rf"""
    (?<![\w.])
    (?P<number>{NUMBER})
    \s+
    (?P<variable>
        [A-Za-z_]
        [^\s+\-<>=]*
    )
    """,
    re.VERBOSE,
)

# Time-dependent variables in the oemof-B3 LP files typically end in
#
#     _0)
#     _1)
#     ...
#
# Strip exactly the final integer index.
#
# Example:
#   flow(BB_electricity_BB_electricity_liion_battery_0_123)
#
# becomes the time-series key
#
#   flow(BB_electricity_BB_electricity_liion_battery_0_*)
TIME_INDEX_RE = re.compile(r"^(?P<prefix>.*)_(?P<time>\d+)\)$")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def parse_number(sign: str, number: str) -> float:
    """Convert separately parsed sign and number to float."""
    value = float(number)

    if sign == "-":
        value = -value

    return value


def time_series_key(variable: str):
    """
    Return a key identifying a time-dependent variable series.

    For example:

        flow(foo_0)
        flow(foo_1)
        flow(foo_2)

    all map to

        flow(foo_*)

    Returns None if the variable does not appear to have a final timestep
    index.
    """
    match = TIME_INDEX_RE.match(variable)

    if match is None:
        return None

    return match.group("prefix") + "_*)"


def coefficients_equal(a: float, b: float) -> bool:
    """
    Check whether two coefficients are effectively identical.

    LP coefficients are parsed from decimal strings. A very tight tolerance
    is used only to avoid irrelevant floating-point conversion effects.
    """
    return math.isclose(a, b, rel_tol=1e-14, abs_tol=0.0)


def detect_section(line: str):
    """
    Detect important LP section headers.

    Returns one of:
        "objective"
        "constraints"
        "bounds"
        "end"
        None
    """
    stripped = line.strip().lower()

    # Objective header expected by the user's LP files.
    if stripped == "objective:" or stripped == "objective":
        return "objective"

    # Common constraint spellings.
    if stripped in {
        "s.t.",
        "s.t",
        "st",
        "subject to",
        "subject to:",
        "such that",
        "such that:",
    }:
        return "constraints"

    if stripped == "bounds" or stripped == "bounds:":
        return "bounds"

    if stripped == "end":
        return "end"

    return None


# ---------------------------------------------------------------------------
# Objective analysis
# ---------------------------------------------------------------------------

def analyze_objective(lines, threshold):
    """
    Analyze objective terms.

    Returns:
        remove_line_indices
        removed_terms
        series_information

    removed_terms contains tuples:
        (line_index, variable, coefficient)

    series_information maps a time-series key to all objective terms belonging
    to that series, not merely the terms that will be removed. This permits
    verification that all time steps really have identical costs.
    """
    remove_line_indices = set()
    removed_terms = []

    # key -> list[(line_number, variable, coefficient)]
    series_information = {}

    in_objective = False

    for line_index, line in enumerate(lines):
        section = detect_section(line)

        if section == "objective":
            in_objective = True
            continue

        if section in {"constraints", "bounds", "end"}:
            if in_objective:
                in_objective = False

        if not in_objective:
            continue

        stripped = line.strip()

        if not stripped:
            continue

        match = OBJECTIVE_TERM_RE.match(line)

        if match is None:
            # Some LP writers may put the objective name on a separate line,
            # comments, etc. Leave such lines untouched.
            continue

        coefficient = parse_number(
            match.group("sign"),
            match.group("number"),
        )
        variable = match.group("variable")

        key = time_series_key(variable)

        if key is not None:
            series_information.setdefault(key, []).append(
                (line_index + 1, variable, coefficient)
            )

        if coefficient != 0.0 and abs(coefficient) <= threshold:
            remove_line_indices.add(line_index)
            removed_terms.append(
                (line_index + 1, variable, coefficient)
            )

    return remove_line_indices, removed_terms, series_information


def check_time_series_coefficients(series_information):
    """
    Warn if coefficients vary between timesteps of the same variable series.
    """
    inconsistent_series = set()

    for key, entries in series_information.items():
        if len(entries) <= 1:
            continue

        reference_line, reference_variable, reference_coefficient = entries[0]

        inconsistent = [
            entry
            for entry in entries[1:]
            if not coefficients_equal(entry[2], reference_coefficient)
        ]

        if inconsistent:
            inconsistent_series.add(key)

            print(
                "\nWARNING: objective coefficients differ across timesteps "
                f"for time-dependent variable series:\n"
                f"  {key}"
            )

            print(
                f"  line {reference_line}: "
                f"{reference_coefficient:.17g}  {reference_variable}"
            )

            for line_number, variable, coefficient in inconsistent:
                print(
                    f"  line {line_number}: "
                    f"{coefficient:.17g}  {variable}"
                )

    return inconsistent_series


def report_removed_terms(removed_terms):
    """
    Print removed objective terms.

    Time-dependent variables are printed only once. The first encountered
    member of the time series is shown.
    """
    print("\nRemoved objective coefficients:")

    if not removed_terms:
        print("  None")
        return

    reported_series = set()

    for line_number, variable, coefficient in removed_terms:
        key = time_series_key(variable)

        if key is not None:
            if key in reported_series:
                continue

            reported_series.add(key)

        print(
            f"  {variable}"
            f"    coefficient = {coefficient:.17g}"
        )


# ---------------------------------------------------------------------------
# Search for small coefficients elsewhere in the LP
# ---------------------------------------------------------------------------

def check_nonobjective_coefficients(lines, threshold):
    """
    Search non-objective LP sections for small nonzero coefficients.

    Only numeric values that appear immediately before a variable-like token
    are treated as coefficients. This prevents RHS values and ordinary bound
    values from being reported in most common LP syntax.

    Returns the number of warnings.
    """
    section = None
    warning_count = 0

    print(
        "\nChecking non-objective sections for other nonzero coefficients "
        f"with abs(coefficient) <= {threshold:.17g} ..."
    )

    for line_number, line in enumerate(lines, start=1):
        detected = detect_section(line)

        if detected is not None:
            section = detected
            continue

        if section in {None, "objective", "end"}:
            continue

        for match in NONOBJECTIVE_TERM_RE.finditer(line):
            try:
                coefficient = float(match.group("number"))
            except ValueError:
                continue

            variable = match.group("variable")

            if coefficient == 0.0:
                continue

            if abs(coefficient) <= threshold:
                warning_count += 1

                print(
                    "\nWARNING: small nonzero coefficient outside objective:"
                )
                print(f"  line       : {line_number}")
                print(f"  section    : {section}")
                print(f"  coefficient: {coefficient:.17g}")
                print(f"  variable   : {variable}")
                print(f"  LP line    : {line.rstrip()}")

    if warning_count == 0:
        print("  No such coefficients found.")
    else:
        print(
            f"\nWARNING: found {warning_count} small nonzero "
            "coefficient(s) outside the objective."
        )

    return warning_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Remove objective coefficients with nonzero absolute value "
            "<= a given threshold from an LP file."
        )
    )

    parser.add_argument(
        "input_lp",
        type=Path,
        help="Input .lp file",
    )

    parser.add_argument(
        "threshold",
        type=float,
        help="Threshold, e.g. 1e-6",
    )

    parser.add_argument(
        "output_lp",
        nargs="?",
        type=Path,
        help=(
            "Output .lp file. If omitted, '<input_stem>_"
            "small_objective_coefficients_removed.lp' is used."
        ),
    )

    args = parser.parse_args()

    input_path = args.input_lp
    threshold = args.threshold

    if threshold < 0:
        parser.error("threshold must be nonnegative")

    if not input_path.is_file():
        parser.error(f"input file does not exist: {input_path}")

    if args.output_lp is None:
        output_path = input_path.with_name(
            input_path.stem
            + "_small_objective_coefficients_removed"
            + input_path.suffix
        )
    else:
        output_path = args.output_lp

    if input_path.resolve() == output_path.resolve():
        parser.error(
            "input and output file must be different; "
            "the input file will not be overwritten"
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    with input_path.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    print(f"Input file : {input_path}")
    print(f"Output file: {output_path}")
    print(f"Threshold  : {threshold:.17g}")
    print(
        "Criterion  : 0 < abs(objective coefficient) <= threshold"
    )

    # ------------------------------------------------------------------
    # Analyze objective
    # ------------------------------------------------------------------

    (
        remove_line_indices,
        removed_terms,
        series_information,
    ) = analyze_objective(lines, threshold)

    # Check all time-dependent objective coefficients before changing file.
    check_time_series_coefficients(series_information)

    # Report removed variables, with time series collapsed.
    report_removed_terms(removed_terms)

    # ------------------------------------------------------------------
    # Check all other LP coefficients
    # ------------------------------------------------------------------

    check_nonobjective_coefficients(lines, threshold)

    # ------------------------------------------------------------------
    # Write modified LP
    # ------------------------------------------------------------------

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        for line_index, line in enumerate(lines):
            if line_index not in remove_line_indices:
                f.write(line)

    print("\nSummary:")
    print(
        f"  Objective lines removed : {len(remove_line_indices)}"
    )

    # Number of distinct reported variables / series.
    distinct_reports = set()

    for _, variable, _ in removed_terms:
        key = time_series_key(variable)

        if key is None:
            distinct_reports.add(("variable", variable))
        else:
            distinct_reports.add(("series", key))

    print(
        f"  Variables/series reported: {len(distinct_reports)}"
    )
    print(f"  Modified LP written to   : {output_path}")


if __name__ == "__main__":
    main()