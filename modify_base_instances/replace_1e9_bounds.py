#!/usr/bin/env python3
"""
replace_1e9_bounds.py

Create a copy of an LP file in which positive bounds equal to 10^9 are
replaced by +inf.

Only the LP's "bounds" section is modified. For every group of variables
that differs only in its final numeric index, the first modified variable
is printed once.

A warning is printed whenever a modified variable does not contain any of:
    excess
    shortage
    import
    export
    curtailment

Usage:
    python replace_1e9_bounds.py input.lp output.lp
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


# Matches positive representations of 10^9, including:
#   1000000000
#   1000000000.0
#   1e9
#   1e+09
#   1.0E+09
BOUND_1E9_PATTERN = re.compile(
    r"""
    (?<![\w.+-])
    \+?
    (?:
        1000000000(?:\.0*)?
        |
        1(?:\.0*)?[eE]\+?0*9
    )
    (?![\w.])
    """,
    re.VERBOSE,
)


# A variable name in an LP bounds line.
VARIABLE_PATTERN = re.compile(
    r"""
    (?:
        [^\s<>=]+
        \([^)]*\)
        |
        [^\s<>=]+
    )
    """,
    re.VERBOSE,
)


BOUNDS_HEADER_PATTERN = re.compile(r"^\s*bounds\s*$", re.IGNORECASE)
END_HEADER_PATTERN = re.compile(r"^\s*end\s*$", re.IGNORECASE)


EXPECTED_UNBOUNDED_NAME_PARTS = (
    "excess",
    "shortage",
    "import",
    "export",
    "curtailment",
)


def extract_variable_name(line: str) -> str | None:
    """
    Extract the variable name from a line in the bounds section.

    Examples:
        0 <= x <= 1000000000
        x <= 1000000000
        1000000000 >= x
        x = 1000000000
    """
    candidates = VARIABLE_PATTERN.findall(line)

    for candidate in candidates:
        candidate = candidate.strip()

        if candidate.lower() in {
            "inf",
            "+inf",
            "-inf",
            "infinity",
            "+infinity",
            "-infinity",
        }:
            continue

        try:
            float(candidate)
            continue
        except ValueError:
            pass

        return candidate

    return None


def variable_type_key(variable_name: str) -> str:
    """
    Return a key that ignores a final numeric index.

    Examples:
        flow(a_b_0_0)  -> flow(a_b_0_<index>)
        flow(a_b_0_1)  -> flow(a_b_0_<index>)

    Thus, only the first modified variable from this group is printed.

    For variables without parentheses, a trailing '_<integer>' is treated
    in the same way.
    """
    if variable_name.endswith(")") and "(" in variable_name:
        prefix, arguments = variable_name.rsplit("(", 1)
        arguments = arguments[:-1]

        normalized_arguments = re.sub(
            r"(?<=_)0*\d+$",
            "<index>",
            arguments,
        )
        return f"{prefix}({normalized_arguments})"

    return re.sub(
        r"(?<=_)0*\d+$",
        "<index>",
        variable_name,
    )


def has_expected_name_part(variable_name: str) -> bool:
    """
    Return True if the variable name contains at least one expected keyword.
    """
    name_lower = variable_name.lower()

    return any(
        keyword in name_lower
        for keyword in EXPECTED_UNBOUNDED_NAME_PARTS
    )


def replace_bounds(
    input_path: Path,
    output_path: Path,
) -> tuple[int, int, int]:
    """
    Process the LP file line by line.

    Returns:
        A tuple containing:
          1. number of numeric bounds replaced;
          2. number of unique variable types modified;
          3. number of warnings.
    """
    in_bounds_section = False
    replacements = 0
    warning_count = 0

    reported_types: set[str] = set()
    warned_variables: set[str] = set()

    with (
        input_path.open(
            "r",
            encoding="utf-8",
            errors="surrogateescape",
        ) as source,
        output_path.open(
            "w",
            encoding="utf-8",
            errors="surrogateescape",
        ) as target,
    ):
        for line in source:
            if not in_bounds_section:
                target.write(line)

                if BOUNDS_HEADER_PATTERN.match(line):
                    in_bounds_section = True

                continue

            if END_HEADER_PATTERN.match(line):
                in_bounds_section = False
                target.write(line)
                continue

            modified_line, number_of_replacements = BOUND_1E9_PATTERN.subn(
                "+inf",
                line,
            )

            if number_of_replacements:
                replacements += number_of_replacements

                variable_name = extract_variable_name(line)

                if variable_name is not None:
                    type_key = variable_type_key(variable_name)

                    # Print modified variable type only once.
                    if type_key not in reported_types:
                        print(variable_name, flush=True)
                        reported_types.add(type_key)

                    # Warn if the modified variable does not contain
                    # one of the expected keywords.
                    if (
                        not has_expected_name_part(variable_name)
                        and variable_name not in warned_variables
                    ):
                        print(
                            "WARNING: removed 1e9 bound from unexpected "
                            f"variable: {variable_name}",
                            flush=True,
                        )
                        warned_variables.add(variable_name)
                        warning_count += 1

            target.write(modified_line)

    return replacements, len(reported_types), warning_count


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replace positive bounds equal to 10^9 with +inf in the bounds "
            "section of an LP file."
        )
    )

    parser.add_argument(
        "input_lp",
        type=Path,
        help="Input .lp file",
    )

    parser.add_argument(
        "output_lp",
        type=Path,
        help="Output .lp file",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    input_path: Path = args.input_lp
    output_path: Path = args.output_lp

    if not input_path.is_file():
        raise SystemExit(
            f"Error: input file does not exist: {input_path}"
        )

    if input_path.resolve() == output_path.resolve():
        raise SystemExit(
            "Error: input and output paths must be different. "
            "The script does not modify the input file in place."
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    replacements, unique_types, warning_count = replace_bounds(
        input_path,
        output_path,
    )

    print(
        f"\nReplaced {replacements} bound(s) for "
        f"{unique_types} unique variable type(s)."
    )

    if warning_count:
        print(
            f"WARNING: {warning_count} modified variable(s) did not contain "
            "any of the expected keywords: "
            + ", ".join(EXPECTED_UNBOUNDED_NAME_PARTS)
        )
    else:
        print(
            "All modified variables contained at least one expected keyword."
        )

    print(f"Output written to: {output_path}")


if __name__ == "__main__":
    main()