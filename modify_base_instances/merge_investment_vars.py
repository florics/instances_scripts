#!/usr/bin/env python3
"""
merge_investmentflow_variables_v2.py

Simplify duplicated capacity variables in an oemof/Pyomo LP file.

The script performs two mandatory transformations and one optional one.

1) InvestmentFlowBlock invest/total duplication

       InvestmentFlowBlock_invest(KEY)
       InvestmentFlowBlock_total(KEY)

   linked by

       c_e_InvestmentFlowBlock_total_rule(KEY)_:
       -1 InvestmentFlowBlock_invest(KEY)
       +1 InvestmentFlowBlock_total(KEY)
       = 0

   becomes

       InvestmentFlowBlock_capacity(KEY)

2) GenericInvestmentStorageBlock invest/total duplication

       GenericInvestmentStorageBlock_invest(KEY)
       GenericInvestmentStorageBlock_total(KEY)

   linked by

       c_e_GenericInvestmentStorageBlock_total_storage_rule(KEY)_:
       -1 GenericInvestmentStorageBlock_invest(KEY)
       +1 GenericInvestmentStorageBlock_total(KEY)
       = 0

   becomes

       GenericInvestmentStorageBlock_contentcapacity(KEY)

3) Optional storage charge/discharge power-capacity duplication

   AFTER transformation (1), a constraint of the form

       c_e_GenericInvestmentStorageBlock_power_coupled(STORAGE_KEY)_:
       -1 InvestmentFlowBlock_capacity(FLOW_KEY_1)
       +1 InvestmentFlowBlock_capacity(FLOW_KEY_2)
       = 0

   is used to replace both InvestmentFlowBlock_capacity variables by

       InvestmentFlowBlock_flowcapacity(STORAGE_KEY)

   and the coupling constraint is removed.

   Disable only this third transformation with --no-merge-storage-power.

Safety checks
-------------
Before writing a transformed model, the script verifies the structural
assumptions needed for the substitutions. In particular, it checks:

* one exact equality rule per invest/total pair;
* one supported bound line per old variable;
* every eliminated *_total variable has bounds [0, +inf];
* every eliminated *_total variable has zero objective coefficient;
* the retained *_invest bound already implies the total lower bound 0;
* generated variable names do not already occur;
* for storage power merging, the check is deliberately performed on the
  intermediate LP after InvestmentFlowBlock_invest/total have become
  InvestmentFlowBlock_capacity;
* every power_coupled row contains exactly two distinct
  InvestmentFlowBlock_capacity variables with coefficients -1 and +1 and
  right-hand side 0;
* each power-capacity variable belongs to at most one such coupling row;
* the new power-capacity bound is the intersection of the two old bounds,
  and that intersection is nonempty.

The transformations are exact variable substitutions, not approximations.

Usage:
    python merge_investmentflow_variables_v2.py input.lp output.lp

    python merge_investmentflow_variables_v2.py \\
        --no-merge-storage-power input.lp output.lp
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

IF_INVEST = "InvestmentFlowBlock_invest"
IF_TOTAL = "InvestmentFlowBlock_total"
IF_CAPACITY = "InvestmentFlowBlock_capacity"
IF_FLOWCAPACITY = "InvestmentFlowBlock_flowcapacity"

GIS_INVEST = "GenericInvestmentStorageBlock_invest"
GIS_TOTAL = "GenericInvestmentStorageBlock_total"
GIS_CONTENTCAPACITY = "GenericInvestmentStorageBlock_contentcapacity"

IF_TOTAL_RULE = "c_e_InvestmentFlowBlock_total_rule"
GIS_TOTAL_RULE = "c_e_GenericInvestmentStorageBlock_total_storage_rule"
GIS_POWER_RULE = "c_e_GenericInvestmentStorageBlock_power_coupled"


# ---------------------------------------------------------------------------
# LP syntax used by the Pyomo-written files in question
# ---------------------------------------------------------------------------

NUM = r"[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"

CONSTRAINT_HEADER_RE = re.compile(r"^\s*\S+:\s*$")
RHS_EQ_RE = re.compile(rf"^\s*=\s*(?P<rhs>{NUM})\s*$")

IF_OLD_VAR_RE = re.compile(
    r"InvestmentFlowBlock_(?P<kind>invest|total)"
    r"\((?P<key>[^()\r\n]+)\)"
)
GIS_OLD_VAR_RE = re.compile(
    r"GenericInvestmentStorageBlock_(?P<kind>invest|total)"
    r"\((?P<key>[^()\r\n]+)\)"
)
IF_CAPACITY_VAR_RE = re.compile(
    r"InvestmentFlowBlock_capacity\((?P<key>[^()\r\n]+)\)"
)

IF_TOTAL_RULE_HEADER_RE = re.compile(
    r"^\s*c_e_InvestmentFlowBlock_total_rule"
    r"\((?P<key>[^()\r\n]+)\)_:\s*$"
)
GIS_TOTAL_RULE_HEADER_RE = re.compile(
    r"^\s*c_e_GenericInvestmentStorageBlock_total_storage_rule"
    r"\((?P<key>[^()\r\n]+)\)_:\s*$"
)
GIS_POWER_RULE_HEADER_RE = re.compile(
    r"^\s*c_e_GenericInvestmentStorageBlock_power_coupled"
    r"\((?P<key>[^()\r\n]+)\)_:\s*$"
)

IF_LINK_TERM_RE = re.compile(
    rf"^\s*(?P<coef>{NUM})\s+"
    r"(?P<var>InvestmentFlowBlock_(?:invest|total)\([^()\r\n]+\))\s*$"
)
GIS_LINK_TERM_RE = re.compile(
    rf"^\s*(?P<coef>{NUM})\s+"
    r"(?P<var>GenericInvestmentStorageBlock_(?:invest|total)"
    r"\([^()\r\n]+\))\s*$"
)
IF_CAPACITY_TERM_RE = re.compile(
    rf"^\s*(?P<coef>{NUM})\s+"
    r"(?P<var>InvestmentFlowBlock_capacity\([^()\r\n]+\))\s*$"
)

TARGET_BOUND_RE = re.compile(
    r"^(?P<indent>\s*)"
    r"(?P<lb>\S+)\s*<=\s*"
    r"(?P<var>"
    r"(?:InvestmentFlowBlock_(?:invest|total|capacity)"
    r"|GenericInvestmentStorageBlock_(?:invest|total))"
    r"\([^()\r\n]+\))"
    r"\s*<=\s*(?P<ub>\S+)\s*$"
)

# Objective occurrences of an eliminated *_total variable.  If such a token
# occurs in the objective but cannot be parsed as a coefficient-variable term,
# validation aborts instead of silently assuming its coefficient is zero.
OBJECTIVE_TOTAL_TERM_RE = re.compile(
    rf"(?P<coef>{NUM})\s+"
    r"(?P<var>"
    r"(?:InvestmentFlowBlock_total|GenericInvestmentStorageBlock_total)"
    r"\([^()\r\n]+\))"
)
OBJECTIVE_TOTAL_VAR_RE = re.compile(
    r"(?:InvestmentFlowBlock_total|GenericInvestmentStorageBlock_total)"
    r"\([^()\r\n]+\)"
)


class InputError(RuntimeError):
    """Input LP does not satisfy a structural assumption."""


@dataclass(frozen=True)
class Bound:
    line_no: int
    lb: float
    ub: float
    lb_token: str
    ub_token: str


@dataclass(frozen=True)
class PowerPair:
    storage_key: str
    first_var: str
    second_var: str
    new_var: str
    lb_token: str
    ub_token: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def numeric_value(token: str) -> float:
    t = token.strip().lower()
    if t in {"+inf", "inf", "+infinity", "infinity"}:
        return math.inf
    if t in {"-inf", "-infinity"}:
        return -math.inf
    try:
        return float(t)
    except ValueError as exc:
        raise InputError(f"Cannot parse numeric bound value {token!r}.") from exc


def is_zero(x: float) -> bool:
    # The structural rows are written with exact small integer coefficients.
    # float("0.0") etc. compare exactly to zero.
    return x == 0.0


def split_line_ending(raw: str) -> tuple[str, str]:
    if raw.endswith("\r\n"):
        return raw[:-2], "\r\n"
    if raw.endswith("\n") or raw.endswith("\r"):
        return raw[:-1], raw[-1]
    return raw, ""


def require_single_lp_sections(path: Path) -> None:
    counts = {"s.t.": 0, "bounds": 0, "end": 0}
    with path.open("r", encoding="utf-8", newline="") as f:
        for raw in f:
            low = raw.strip().lower()
            if low in counts:
                counts[low] += 1
    if counts != {"s.t.": 1, "bounds": 1, "end": 1}:
        raise InputError(
            "Expected exactly one 's.t.', one 'bounds', and one 'end' section; "
            f"found {counts}."
        )


def check_exact_equality_rule(
    *,
    key: str,
    header_line: int,
    body: list[tuple[int, str]],
    first_var: str,
    second_var: str,
    term_re: re.Pattern[str],
    rule_name: str,
) -> None:
    """Require exactly -first + second = 0 (or the sign-reversed equation)."""
    nonblank = [(n, s) for n, s in body if s.strip()]
    if len(nonblank) != 3:
        raise InputError(
            f"Line {header_line}: {rule_name}({key}) must contain exactly "
            "two variable terms and one equality RHS line."
        )

    terms: dict[str, float] = {}
    rhs: float | None = None

    for line_no, line in nonblank:
        m = term_re.match(line)
        if m:
            var = m.group("var")
            if var in terms:
                raise InputError(
                    f"Line {line_no}: duplicate variable term {var!r} in "
                    f"{rule_name}({key})."
                )
            terms[var] = float(m.group("coef"))
            continue

        m = RHS_EQ_RE.match(line)
        if m:
            if rhs is not None:
                raise InputError(
                    f"Line {line_no}: multiple RHS lines in {rule_name}({key})."
                )
            rhs = float(m.group("rhs"))
            continue

        raise InputError(
            f"Line {line_no}: unexpected content in {rule_name}({key}):\n"
            f"    {line}"
        )

    if set(terms) != {first_var, second_var} or rhs != 0.0:
        raise InputError(
            f"Line {header_line}: {rule_name}({key}) does not contain exactly "
            f"the expected pair {first_var!r}, {second_var!r} with RHS 0."
        )

    c1 = terms[first_var]
    c2 = terms[second_var]
    if not ((c1 == -1.0 and c2 == 1.0) or (c1 == 1.0 and c2 == -1.0)):
        raise InputError(
            f"Line {header_line}: {rule_name}({key}) must be an exact equality "
            f"between the two variables (coefficients -1/+1); got {c1} and {c2}."
        )


def choose_max_bound(a: Bound, b: Bound) -> tuple[float, str]:
    if a.lb >= b.lb:
        return a.lb, a.lb_token
    return b.lb, b.lb_token


def choose_min_bound(a: Bound, b: Bound) -> tuple[float, str]:
    if a.ub <= b.ub:
        return a.ub, a.ub_token
    return b.ub, b.ub_token


# ---------------------------------------------------------------------------
# Stage 1+2 validation:
#   InvestmentFlowBlock invest/total -> capacity
#   GenericInvestmentStorageBlock invest/total -> contentcapacity
# ---------------------------------------------------------------------------


def validate_invest_total_pairs(path: Path) -> tuple[set[str], set[str]]:
    require_single_lp_sections(path)

    if_keys_invest: set[str] = set()
    if_keys_total: set[str] = set()
    if_rule_keys: set[str] = set()

    gis_keys_invest: set[str] = set()
    gis_keys_total: set[str] = set()
    gis_rule_keys: set[str] = set()

    bounds: dict[tuple[str, str, str], Bound] = {}

    # Store nonzero objective occurrences of eliminated totals.  We require
    # each occurrence, not merely the net coefficient, to be zero.
    nonzero_total_objective: list[tuple[int, str, float]] = []

    section = "pre"
    active_kind: str | None = None
    active_key: str | None = None
    active_header_line = -1
    active_body: list[tuple[int, str]] = []

    def finish_active_rule() -> None:
        nonlocal active_kind, active_key, active_header_line, active_body
        if active_kind is None or active_key is None:
            return

        if active_kind == "if":
            if active_key in if_rule_keys:
                raise InputError(
                    f"Line {active_header_line}: duplicate {IF_TOTAL_RULE} rule "
                    f"for key {active_key!r}."
                )
            check_exact_equality_rule(
                key=active_key,
                header_line=active_header_line,
                body=active_body,
                first_var=f"{IF_INVEST}({active_key})",
                second_var=f"{IF_TOTAL}({active_key})",
                term_re=IF_LINK_TERM_RE,
                rule_name=IF_TOTAL_RULE,
            )
            if_rule_keys.add(active_key)

        elif active_kind == "gis":
            if active_key in gis_rule_keys:
                raise InputError(
                    f"Line {active_header_line}: duplicate {GIS_TOTAL_RULE} rule "
                    f"for key {active_key!r}."
                )
            check_exact_equality_rule(
                key=active_key,
                header_line=active_header_line,
                body=active_body,
                first_var=f"{GIS_INVEST}({active_key})",
                second_var=f"{GIS_TOTAL}({active_key})",
                term_re=GIS_LINK_TERM_RE,
                rule_name=GIS_TOTAL_RULE,
            )
            gis_rule_keys.add(active_key)

        active_kind = None
        active_key = None
        active_header_line = -1
        active_body = []

    with path.open("r", encoding="utf-8", newline="") as f:
        for line_no, raw in enumerate(f, 1):
            line, _ = split_line_ending(raw)
            stripped = line.strip()
            low = stripped.lower()

            if f"{IF_CAPACITY}(" in line:
                raise InputError(
                    f"Line {line_no}: generated name {IF_CAPACITY}(...) "
                    "already occurs in the input."
                )
            if f"{GIS_CONTENTCAPACITY}(" in line:
                raise InputError(
                    f"Line {line_no}: generated name {GIS_CONTENTCAPACITY}(...) "
                    "already occurs in the input."
                )
            if f"{IF_FLOWCAPACITY}(" in line:
                raise InputError(
                    f"Line {line_no}: generated name {IF_FLOWCAPACITY}(...) "
                    "already occurs in the input."
                )

            # Ignore LP comment lines for variable-occurrence accounting.
            is_comment = stripped.startswith("\\*")
            if not is_comment:
                for m in IF_OLD_VAR_RE.finditer(line):
                    if m.group("kind") == "invest":
                        if_keys_invest.add(m.group("key"))
                    else:
                        if_keys_total.add(m.group("key"))
                for m in GIS_OLD_VAR_RE.finditer(line):
                    if m.group("kind") == "invest":
                        gis_keys_invest.add(m.group("key"))
                    else:
                        gis_keys_total.add(m.group("key"))

            if low == "s.t.":
                finish_active_rule()
                section = "constraints"
                continue
            if low == "bounds":
                finish_active_rule()
                section = "bounds"
                continue
            if low == "end":
                finish_active_rule()
                section = "end"
                continue

            if section == "pre" and not is_comment:
                total_tokens = OBJECTIVE_TOTAL_VAR_RE.findall(line)
                parsed = list(OBJECTIVE_TOTAL_TERM_RE.finditer(line))
                parsed_vars = [m.group("var") for m in parsed]

                # Every total token in the objective must be associated with a
                # numeric coefficient we can inspect.
                if sorted(total_tokens) != sorted(parsed_vars):
                    raise InputError(
                        f"Line {line_no}: cannot unambiguously parse objective "
                        "coefficient of an eliminated *_total variable:\n"
                        f"    {line}"
                    )
                for m in parsed:
                    coef = float(m.group("coef"))
                    if coef != 0.0:
                        nonzero_total_objective.append(
                            (line_no, m.group("var"), coef)
                        )

            if section == "constraints":
                if CONSTRAINT_HEADER_RE.match(line):
                    finish_active_rule()
                    m = IF_TOTAL_RULE_HEADER_RE.match(line)
                    if m:
                        active_kind = "if"
                        active_key = m.group("key")
                        active_header_line = line_no
                        continue
                    m = GIS_TOTAL_RULE_HEADER_RE.match(line)
                    if m:
                        active_kind = "gis"
                        active_key = m.group("key")
                        active_header_line = line_no
                        continue
                    continue

                if active_kind is not None:
                    active_body.append((line_no, line))

            elif section == "bounds" and not is_comment:
                if_old = list(IF_OLD_VAR_RE.finditer(line))
                gis_old = list(GIS_OLD_VAR_RE.finditer(line))
                if not if_old and not gis_old:
                    continue

                m = TARGET_BOUND_RE.match(line)
                if not m:
                    raise InputError(
                        f"Line {line_no}: unsupported bound syntax for a variable "
                        f"that the script must transform:\n    {line}"
                    )

                var = m.group("var")
                vm_if = IF_OLD_VAR_RE.fullmatch(var)
                vm_gis = GIS_OLD_VAR_RE.fullmatch(var)
                if vm_if:
                    family = "if"
                    kind = vm_if.group("kind")
                    key = vm_if.group("key")
                elif vm_gis:
                    family = "gis"
                    kind = vm_gis.group("kind")
                    key = vm_gis.group("key")
                else:
                    raise InputError(
                        f"Line {line_no}: internal bound parser mismatch for {var}."
                    )

                item = (family, kind, key)
                if item in bounds:
                    raise InputError(
                        f"Line {line_no}: duplicate bound for {var}."
                    )
                bounds[item] = Bound(
                    line_no=line_no,
                    lb=numeric_value(m.group("lb")),
                    ub=numeric_value(m.group("ub")),
                    lb_token=m.group("lb"),
                    ub_token=m.group("ub"),
                )

    finish_active_rule()

    if nonzero_total_objective:
        details = "\n".join(
            f"  line {n}: coefficient {coef} for {var}"
            for n, var, coef in nonzero_total_objective[:20]
        )
        if len(nonzero_total_objective) > 20:
            details += f"\n  ... and {len(nonzero_total_objective)-20} more"
        raise InputError(
            "An eliminated *_total variable has a nonzero objective coefficient.\n"
            + details
        )

    if not if_rule_keys:
        raise InputError(f"No {IF_TOTAL_RULE}(...) constraints found.")

    # The storage-energy family is optional in principle, but if any old GIS
    # invest/total variables exist, their pairing must be complete.
    if if_keys_invest != if_rule_keys or if_keys_total != if_rule_keys:
        raise InputError(
            "InvestmentFlowBlock invest/total variables and total_rule keys do "
            "not match exactly.\n"
            f"invest without rule: {sorted(if_keys_invest - if_rule_keys)}\n"
            f"total without rule: {sorted(if_keys_total - if_rule_keys)}\n"
            f"rule without invest: {sorted(if_rule_keys - if_keys_invest)}\n"
            f"rule without total: {sorted(if_rule_keys - if_keys_total)}"
        )

    if gis_keys_invest or gis_keys_total or gis_rule_keys:
        if gis_keys_invest != gis_rule_keys or gis_keys_total != gis_rule_keys:
            raise InputError(
                "GenericInvestmentStorageBlock invest/total variables and "
                "total_storage_rule keys do not match exactly.\n"
                f"invest without rule: {sorted(gis_keys_invest - gis_rule_keys)}\n"
                f"total without rule: {sorted(gis_keys_total - gis_rule_keys)}\n"
                f"rule without invest: {sorted(gis_rule_keys - gis_keys_invest)}\n"
                f"rule without total: {sorted(gis_rule_keys - gis_keys_total)}"
            )

    expected_bounds = {
        *(('if', kind, key) for key in if_rule_keys for kind in ('invest', 'total')),
        *(('gis', kind, key) for key in gis_rule_keys for kind in ('invest', 'total')),
    }
    found_relevant_bounds = {
        item for item in bounds
        if item[0] in {"if", "gis"}
    }
    if found_relevant_bounds != expected_bounds:
        raise InputError(
            "Expected exactly one supported bound for each transformed invest "
            "and total variable.\n"
            f"missing: {sorted(expected_bounds - found_relevant_bounds)}\n"
            f"extra: {sorted(found_relevant_bounds - expected_bounds)}"
        )

    for family, keys, invest_name, total_name in (
        ("if", if_rule_keys, IF_INVEST, IF_TOTAL),
        ("gis", gis_rule_keys, GIS_INVEST, GIS_TOTAL),
    ):
        for key in sorted(keys):
            inv = bounds[(family, "invest", key)]
            tot = bounds[(family, "total", key)]

            if tot.lb != 0.0 or not (math.isinf(tot.ub) and tot.ub > 0):
                raise InputError(
                    f"Line {tot.line_no}: expected eliminated variable "
                    f"{total_name}({key}) to have bounds [0,+inf], got "
                    f"[{tot.lb_token},{tot.ub_token}]."
                )
            if inv.lb < 0.0:
                raise InputError(
                    f"Line {inv.line_no}: {invest_name}({key}) has lower bound "
                    f"{inv.lb_token}. Because its paired total variable has lower "
                    "bound 0, simply retaining the invest bound would not be "
                    "equivalent."
                )
            if inv.lb > inv.ub:
                raise InputError(
                    f"Line {inv.line_no}: invalid bound interval for "
                    f"{invest_name}({key}): [{inv.lb_token},{inv.ub_token}]."
                )

    return if_rule_keys, gis_rule_keys


def rename_stage12(line: str) -> str:
    line = IF_OLD_VAR_RE.sub(
        lambda m: f"{IF_CAPACITY}({m.group('key')})",
        line,
    )
    line = GIS_OLD_VAR_RE.sub(
        lambda m: f"{GIS_CONTENTCAPACITY}({m.group('key')})",
        line,
    )
    return line


def transform_stage12(
    src_path: Path,
    dst_path: Path,
    if_keys: set[str],
    gis_keys: set[str],
) -> None:
    """Write the intermediate LP after the two invest/total substitutions."""
    section = "pre"
    skip_rule = False
    removed_if_rules = 0
    removed_gis_rules = 0
    removed_if_total_bounds = 0
    removed_gis_total_bounds = 0

    with (
        src_path.open("r", encoding="utf-8", newline="") as src,
        dst_path.open("w", encoding="utf-8", newline="") as dst,
    ):
        for raw in src:
            line, eol = split_line_ending(raw)
            low = line.strip().lower()

            if low == "s.t.":
                section = "constraints"
                skip_rule = False
                dst.write(raw)
                continue
            if low == "bounds":
                section = "bounds"
                skip_rule = False
                dst.write(raw)
                continue
            if low == "end":
                section = "end"
                skip_rule = False
                dst.write(raw)
                continue

            if section == "constraints" and CONSTRAINT_HEADER_RE.match(line):
                if IF_TOTAL_RULE_HEADER_RE.match(line):
                    skip_rule = True
                    removed_if_rules += 1
                    continue
                if GIS_TOTAL_RULE_HEADER_RE.match(line):
                    skip_rule = True
                    removed_gis_rules += 1
                    continue
                skip_rule = False

            if section == "constraints" and skip_rule:
                continue

            if section == "bounds":
                m = TARGET_BOUND_RE.match(line)
                if m:
                    var = m.group("var")
                    vm_if = IF_OLD_VAR_RE.fullmatch(var)
                    if vm_if and vm_if.group("kind") == "total":
                        removed_if_total_bounds += 1
                        continue
                    vm_gis = GIS_OLD_VAR_RE.fullmatch(var)
                    if vm_gis and vm_gis.group("kind") == "total":
                        removed_gis_total_bounds += 1
                        continue

            dst.write(rename_stage12(line) + eol)

    if removed_if_rules != len(if_keys):
        raise InputError(
            f"Internal transformation error: removed {removed_if_rules} "
            f"{IF_TOTAL_RULE} rows, expected {len(if_keys)}."
        )
    if removed_gis_rules != len(gis_keys):
        raise InputError(
            f"Internal transformation error: removed {removed_gis_rules} "
            f"{GIS_TOTAL_RULE} rows, expected {len(gis_keys)}."
        )
    if removed_if_total_bounds != len(if_keys):
        raise InputError(
            f"Internal transformation error: removed {removed_if_total_bounds} "
            f"{IF_TOTAL} bounds, expected {len(if_keys)}."
        )
    if removed_gis_total_bounds != len(gis_keys):
        raise InputError(
            f"Internal transformation error: removed {removed_gis_total_bounds} "
            f"{GIS_TOTAL} bounds, expected {len(gis_keys)}."
        )


# ---------------------------------------------------------------------------
# Stage 3 validation, intentionally against the intermediate LP
# ---------------------------------------------------------------------------


def validate_power_couplings(path: Path) -> dict[str, PowerPair]:
    require_single_lp_sections(path)

    bounds: dict[str, Bound] = {}
    raw_pairs: dict[str, tuple[str, str, int]] = {}
    variable_to_storage: dict[str, str] = {}

    section = "pre"
    active_storage: str | None = None
    active_header_line = -1
    active_body: list[tuple[int, str]] = []

    def finish_power_rule() -> None:
        nonlocal active_storage, active_header_line, active_body
        if active_storage is None:
            return

        nonblank = [(n, s) for n, s in active_body if s.strip()]
        if len(nonblank) != 3:
            raise InputError(
                f"Line {active_header_line}: {GIS_POWER_RULE}({active_storage}) "
                "must contain exactly two capacity terms and one '= 0' line "
                "after the InvestmentFlowBlock invest/total merge."
            )

        terms: dict[str, float] = {}
        rhs: float | None = None
        for line_no, line in nonblank:
            m = IF_CAPACITY_TERM_RE.match(line)
            if m:
                var = m.group("var")
                if var in terms:
                    raise InputError(
                        f"Line {line_no}: duplicate {var} in "
                        f"{GIS_POWER_RULE}({active_storage})."
                    )
                terms[var] = float(m.group("coef"))
                continue

            m = RHS_EQ_RE.match(line)
            if m:
                if rhs is not None:
                    raise InputError(
                        f"Line {line_no}: multiple RHS lines in "
                        f"{GIS_POWER_RULE}({active_storage})."
                    )
                rhs = float(m.group("rhs"))
                continue

            raise InputError(
                f"Line {line_no}: unexpected content in "
                f"{GIS_POWER_RULE}({active_storage}) after stage 1:\n"
                f"    {line}\n"
                "Expected InvestmentFlowBlock_capacity(...) terms here."
            )

        if len(terms) != 2 or rhs != 0.0:
            raise InputError(
                f"Line {active_header_line}: {GIS_POWER_RULE}({active_storage}) "
                "must equate exactly two distinct InvestmentFlowBlock_capacity "
                "variables with RHS 0."
            )

        coeffs = sorted(terms.values())
        if coeffs != [-1.0, 1.0]:
            raise InputError(
                f"Line {active_header_line}: {GIS_POWER_RULE}({active_storage}) "
                f"must use coefficients -1 and +1; got {coeffs}."
            )

        if active_storage in raw_pairs:
            raise InputError(
                f"Line {active_header_line}: duplicate {GIS_POWER_RULE} for "
                f"storage key {active_storage!r}."
            )

        vars_in_row = list(terms)
        for var in vars_in_row:
            if var in variable_to_storage:
                raise InputError(
                    f"Line {active_header_line}: {var} occurs in more than one "
                    f"{GIS_POWER_RULE} row ({variable_to_storage[var]!r} and "
                    f"{active_storage!r})."
                )
            variable_to_storage[var] = active_storage

        raw_pairs[active_storage] = (
            vars_in_row[0], vars_in_row[1], active_header_line
        )

        active_storage = None
        active_header_line = -1
        active_body = []

    with path.open("r", encoding="utf-8", newline="") as f:
        for line_no, raw in enumerate(f, 1):
            line, _ = split_line_ending(raw)
            stripped = line.strip()
            low = stripped.lower()

            if f"{IF_FLOWCAPACITY}(" in line:
                raise InputError(
                    f"Line {line_no}: generated name {IF_FLOWCAPACITY}(...) "
                    "already occurs before the power-capacity merge."
                )

            if low == "s.t.":
                finish_power_rule()
                section = "constraints"
                continue
            if low == "bounds":
                finish_power_rule()
                section = "bounds"
                continue
            if low == "end":
                finish_power_rule()
                section = "end"
                continue

            if section == "constraints":
                if CONSTRAINT_HEADER_RE.match(line):
                    finish_power_rule()
                    m = GIS_POWER_RULE_HEADER_RE.match(line)
                    if m:
                        active_storage = m.group("key")
                        active_header_line = line_no
                    continue

                if active_storage is not None:
                    active_body.append((line_no, line))

            elif section == "bounds":
                cap_matches = list(IF_CAPACITY_VAR_RE.finditer(line))
                if not cap_matches:
                    continue

                m = TARGET_BOUND_RE.match(line)
                if not m:
                    raise InputError(
                        f"Line {line_no}: unsupported bound syntax for "
                        f"InvestmentFlowBlock_capacity variable:\n    {line}"
                    )
                var = m.group("var")
                if not IF_CAPACITY_VAR_RE.fullmatch(var):
                    # This line may contain another target family, but then it
                    # should not also contain an IF capacity token.
                    raise InputError(
                        f"Line {line_no}: cannot parse capacity bound:\n    {line}"
                    )
                if var in bounds:
                    raise InputError(
                        f"Line {line_no}: duplicate bound for {var}."
                    )
                bounds[var] = Bound(
                    line_no=line_no,
                    lb=numeric_value(m.group("lb")),
                    ub=numeric_value(m.group("ub")),
                    lb_token=m.group("lb"),
                    ub_token=m.group("ub"),
                )

    finish_power_rule()

    # No storage power-coupling rows is a valid case: there is simply nothing
    # to merge in stage 3.
    pairs: dict[str, PowerPair] = {}
    for storage_key, (var1, var2, header_line) in raw_pairs.items():
        if var1 not in bounds or var2 not in bounds:
            missing = [v for v in (var1, var2) if v not in bounds]
            raise InputError(
                f"Line {header_line}: missing bound(s) for power-coupled "
                f"capacity variable(s): {missing}."
            )

        b1 = bounds[var1]
        b2 = bounds[var2]
        lb, lb_token = choose_max_bound(b1, b2)
        ub, ub_token = choose_min_bound(b1, b2)
        if lb > ub:
            raise InputError(
                f"Line {header_line}: bounds of {var1} and {var2} have empty "
                "intersection, so they cannot be replaced by one equivalent "
                f"variable. Intervals are [{b1.lb_token},{b1.ub_token}] and "
                f"[{b2.lb_token},{b2.ub_token}]."
            )

        pairs[storage_key] = PowerPair(
            storage_key=storage_key,
            first_var=var1,
            second_var=var2,
            new_var=f"{IF_FLOWCAPACITY}({storage_key})",
            lb_token=lb_token,
            ub_token=ub_token,
        )

    return pairs


def transform_power_couplings(
    src_path: Path,
    dst_path: Path,
    pairs: dict[str, PowerPair],
) -> None:
    old_to_new: dict[str, str] = {}
    old_to_storage: dict[str, str] = {}
    for storage_key, pair in pairs.items():
        for old_var in (pair.first_var, pair.second_var):
            old_to_new[old_var] = pair.new_var
            old_to_storage[old_var] = storage_key

    if old_to_new:
        # Longest first is defensive in case names share prefixes.
        alternatives = "|".join(
            re.escape(v) for v in sorted(old_to_new, key=len, reverse=True)
        )
        mapped_var_re = re.compile(alternatives)
    else:
        mapped_var_re = None

    section = "pre"
    skip_rule = False
    removed_rules = 0
    removed_old_bounds = 0
    written_storage_bounds: set[str] = set()

    with (
        src_path.open("r", encoding="utf-8", newline="") as src,
        dst_path.open("w", encoding="utf-8", newline="") as dst,
    ):
        for raw in src:
            line, eol = split_line_ending(raw)
            low = line.strip().lower()

            if low == "s.t.":
                section = "constraints"
                skip_rule = False
                dst.write(raw)
                continue
            if low == "bounds":
                section = "bounds"
                skip_rule = False
                dst.write(raw)
                continue
            if low == "end":
                section = "end"
                skip_rule = False
                dst.write(raw)
                continue

            if section == "constraints" and CONSTRAINT_HEADER_RE.match(line):
                m = GIS_POWER_RULE_HEADER_RE.match(line)
                if m:
                    storage_key = m.group("key")
                    if storage_key not in pairs:
                        raise InputError(
                            f"Internal transformation error: encountered "
                            f"unvalidated {GIS_POWER_RULE}({storage_key})."
                        )
                    skip_rule = True
                    removed_rules += 1
                    continue
                skip_rule = False

            if section == "constraints" and skip_rule:
                continue

            if section == "bounds" and old_to_new:
                m = TARGET_BOUND_RE.match(line)
                if m and m.group("var") in old_to_new:
                    old_var = m.group("var")
                    storage_key = old_to_storage[old_var]
                    removed_old_bounds += 1
                    if storage_key not in written_storage_bounds:
                        pair = pairs[storage_key]
                        dst.write(
                            f"{m.group('indent')}{pair.lb_token} <= "
                            f"{pair.new_var} <= {pair.ub_token}{eol}"
                        )
                        written_storage_bounds.add(storage_key)
                    continue

            if mapped_var_re is not None:
                line = mapped_var_re.sub(lambda m: old_to_new[m.group(0)], line)
            dst.write(line + eol)

    if removed_rules != len(pairs):
        raise InputError(
            f"Internal transformation error: removed {removed_rules} power "
            f"coupling rows, expected {len(pairs)}."
        )
    if removed_old_bounds != 2 * len(pairs):
        raise InputError(
            f"Internal transformation error: removed {removed_old_bounds} old "
            f"power-capacity bounds, expected {2 * len(pairs)}."
        )
    if written_storage_bounds != set(pairs):
        raise InputError(
            "Internal transformation error while writing merged "
            "InvestmentFlowBlock_flowcapacity bounds."
        )


# ---------------------------------------------------------------------------
# Final structural validation
# ---------------------------------------------------------------------------


def validate_final_output(path: Path, merge_storage_power: bool) -> None:
    forbidden_tokens = [
        f"{IF_INVEST}(",
        f"{IF_TOTAL}(",
        f"{GIS_INVEST}(",
        f"{GIS_TOTAL}(",
    ]
    forbidden_headers = [
        f"{IF_TOTAL_RULE}(",
        f"{GIS_TOTAL_RULE}(",
    ]
    if merge_storage_power:
        forbidden_headers.append(f"{GIS_POWER_RULE}(")

    with path.open("r", encoding="utf-8", newline="") as f:
        for line_no, raw in enumerate(f, 1):
            stripped = raw.strip()
            if stripped.startswith("\\*"):
                continue
            for token in forbidden_tokens:
                if token in raw:
                    raise InputError(
                        f"Post-validation failed at line {line_no}: old variable "
                        f"token {token!r} remains in output."
                    )
            for header in forbidden_headers:
                if header in raw:
                    raise InputError(
                        f"Post-validation failed at line {line_no}: removed "
                        f"constraint family {header!r} remains in output."
                    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run_transform(
    input_path: Path,
    output_path: Path,
    merge_storage_power: bool,
) -> tuple[int, int, int]:
    if not input_path.is_file():
        raise InputError(f"Input file not found: {input_path}")
    if input_path.resolve() == output_path.resolve():
        raise InputError("Input and output files must be different files.")

    if_keys, gis_keys = validate_invest_total_pairs(input_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    stage_fd, stage_name = tempfile.mkstemp(
        prefix=output_path.name + ".stage12.",
        suffix=".lp",
        dir=output_path.parent,
    )
    os.close(stage_fd)
    stage_path = Path(stage_name)

    final_fd, final_name = tempfile.mkstemp(
        prefix=output_path.name + ".final.",
        suffix=".lp",
        dir=output_path.parent,
    )
    os.close(final_fd)
    final_path = Path(final_name)

    try:
        transform_stage12(input_path, stage_path, if_keys, gis_keys)

        if merge_storage_power:
            # This is intentionally validated on the intermediate LP.  At this
            # point the power_coupled rows contain InvestmentFlowBlock_capacity,
            # not InvestmentFlowBlock_invest/total.
            power_pairs = validate_power_couplings(stage_path)
            transform_power_couplings(stage_path, final_path, power_pairs)
        else:
            power_pairs = {}
            os.replace(stage_path, final_path)
            # stage_path no longer exists after os.replace.

        validate_final_output(final_path, merge_storage_power)
        os.replace(final_path, output_path)

        return len(if_keys), len(gis_keys), len(power_pairs)

    except Exception:
        stage_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    finally:
        stage_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Merge redundant oemof/Pyomo investment capacity variables in an "
            "LP file, with strict structural validation."
        )
    )
    parser.add_argument("input_lp", type=Path, help="input .lp file")
    parser.add_argument("output_lp", type=Path, help="output .lp file")
    parser.add_argument(
        "--no-merge-storage-power",
        action="store_true",
        help=(
            "do not merge the two InvestmentFlowBlock_capacity variables "
            "coupled by c_e_GenericInvestmentStorageBlock_power_coupled"
        ),
    )
    args = parser.parse_args()

    try:
        n_if, n_gis, n_power = run_transform(
            args.input_lp,
            args.output_lp,
            merge_storage_power=not args.no_merge_storage_power,
        )
    except InputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    print(
        f"Merged {n_if} InvestmentFlowBlock invest/total pairs into "
        f"{IF_CAPACITY}(...)."
    )
    print(
        f"Merged {n_gis} GenericInvestmentStorageBlock invest/total pairs into "
        f"{GIS_CONTENTCAPACITY}(...)."
    )
    if args.no_merge_storage_power:
        print(
            "Storage power-capacity merge disabled; "
            f"{GIS_POWER_RULE}(...) constraints were retained."
        )
    else:
        print(
            f"Merged {n_power} storage inflow/outflow capacity pairs into "
            f"{IF_FLOWCAPACITY}(...)."
        )
    print(f"Wrote: {args.output_lp}")


if __name__ == "__main__":
    main()

