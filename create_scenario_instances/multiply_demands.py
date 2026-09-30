#!/usr/bin/env python3
"""
multiply_demands.py

Multiply demand values in an LP file by changing only the RHS of
c_e_BusBlock_balance(...) equality constraints.

Input compression
-----------------
Supported input formats are:
    *.lp / other plain-text files
    *.gz                 gzip-compressed text
    *.zip                ZIP archive containing exactly one regular file

Output compression
------------------
By default, output is gzip-compressed. If the requested output path does not
already end in ".gz", the suffix ".gz" is appended automatically.

Use --no-compress to write plain text instead. In that mode, the output path is
used exactly as specified and must not end in .gz or .zip.

Examples
--------
Apply one factor to all demands:

    python multiply_demands.py input.lp output.lp --factor 1.1

This writes:

    output.lp.gz

Read gzip input and write gzip output:

    python multiply_demands.py input.lp.gz output.lp.gz --factor 1.1

Write uncompressed output:

    python multiply_demands.py input.lp.gz output.lp \
        --factor 1.1 --no-compress

Carrier-specific factors are also retained from v1:

    python multiply_demands.py input.lp.gz output.lp \
        --carrier-factor electricity 1.1 \
        --carrier-factor h2 1.2

Numerics
--------
Demand values and factors are parsed directly from decimal strings using
Decimal, not binary float. Only selected RHS strings are numerically changed;
all other lines are copied as text.
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
import re
import sys
import tempfile
import zipfile
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Iterator, TextIO


BUS_HEADER_RE = re.compile(
    r"^(?P<indent>\s*)c_e_BusBlock_balance\((?P<bus>[^()\r\n]+)\)_:\s*$"
)
CONSTRAINT_HEADER_RE = re.compile(r"^\s*\S+:\s*$")
NUM = r"[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
RHS_RE = re.compile(rf"^(?P<indent>\s*)=\s*(?P<rhs>{NUM})\s*$")


class InputError(RuntimeError):
    pass


def parse_decimal(text: str, what: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise InputError(f"Cannot parse {what} {text!r} as a decimal number.") from exc
    if not value.is_finite():
        raise InputError(f"{what.capitalize()} must be finite, got {text!r}.")
    return value


def parse_bus_identifier(bus: str) -> tuple[str, int, int]:
    """Parse '<region>_<carrier>_<fixed-index>_<timestep>'."""
    parts = bus.rsplit("_", 2)
    if len(parts) != 3:
        raise InputError(
            f"Cannot parse BusBlock identifier {bus!r}: expected it to end in "
            "_<fixed_index>_<timestep>."
        )

    base, fixed_text, timestep_text = parts
    if not fixed_text.isdigit() or not timestep_text.isdigit():
        raise InputError(
            f"Cannot parse BusBlock identifier {bus!r}: expected numeric fixed index "
            "and timestep."
        )

    return base, int(fixed_text), int(timestep_text)


def matches_carrier(base_bus: str, carrier: str) -> bool:
    return base_bus.endswith("_" + carrier)


def decimal_product_to_string(value_text: str, factor: Decimal) -> str:
    value = parse_decimal(value_text, "RHS demand value")
    if value < 0:
        raise InputError(
            f"Targeted BusBlock RHS is negative ({value_text}); refusing to treat it as demand."
        )

    digits_needed = max(
        80,
        len(value.as_tuple().digits) + len(factor.as_tuple().digits) + 20,
    )
    with localcontext() as ctx:
        ctx.prec = digits_needed
        result = value * factor

    s = format(result, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in {"-0", "+0", ""}:
        s = "0"
    return s


def determine_factor(
    base_bus: str,
    global_factor: Decimal | None,
    carrier_factors: dict[str, Decimal],
) -> tuple[str | None, Decimal | None]:
    if global_factor is not None:
        return "<all>", global_factor

    matched = [carrier for carrier in carrier_factors if matches_carrier(base_bus, carrier)]
    if len(matched) > 1:
        raise InputError(
            f"Bus {base_bus!r} matches multiple requested carriers: {matched}. "
            "Use non-overlapping carrier names."
        )
    if not matched:
        return None, None

    carrier = matched[0]
    return carrier, carrier_factors[carrier]


@contextmanager
def open_input_text(path: Path) -> Iterator[TextIO]:
    """Open plain, gzip, or single-member ZIP input as a streaming text file."""
    suffix = path.suffix.lower()

    if suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            yield f
        return

    if suffix == ".zip":
        with zipfile.ZipFile(path, "r") as zf:
            members = [info for info in zf.infolist() if not info.is_dir()]
            if len(members) != 1:
                raise InputError(
                    f"ZIP input {path} contains {len(members)} regular files; "
                    "expected exactly one."
                )
            member = members[0]
            with zf.open(member, "r") as raw:
                with io.TextIOWrapper(raw, encoding="utf-8", newline="") as text:
                    yield text
        return

    with path.open("r", encoding="utf-8", newline="") as f:
        yield f


def effective_output_path(requested: Path, compress: bool) -> Path:
    if compress:
        if requested.suffix.lower() == ".zip":
            raise InputError(
                "Default output compression is gzip, not ZIP. Use an output name ending "
                "in .gz, or omit the compression suffix and let the script append .gz."
            )
        if requested.suffix.lower() == ".gz":
            return requested
        return Path(str(requested) + ".gz")

    if requested.suffix.lower() in {".gz", ".zip"}:
        raise InputError(
            "With --no-compress, use an output filename without .gz or .zip."
        )
    return requested


@contextmanager
def open_output_text(path: Path, compress: bool) -> Iterator[TextIO]:
    if compress:
        with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
            yield f
    else:
        with path.open("w", encoding="utf-8", newline="") as f:
            yield f


def process_constraint_block(
    header_line: str,
    body_lines: list[str],
    global_factor: Decimal | None,
    carrier_factors: dict[str, Decimal],
    matched_counts: Counter[str],
) -> list[str]:
    hm = BUS_HEADER_RE.match(header_line.rstrip("\r\n"))
    if hm is None:
        return [header_line, *body_lines]

    bus = hm.group("bus")
    base_bus, fixed_index, _timestep = parse_bus_identifier(bus)

    if fixed_index != 0:
        raise InputError(
            f"BusBlock constraint {bus!r} has fixed index {fixed_index}; expected 0."
        )

    carrier_label, factor = determine_factor(base_bus, global_factor, carrier_factors)
    if factor is None:
        return [header_line, *body_lines]

    rhs_positions: list[tuple[int, re.Match[str]]] = []
    for i, raw in enumerate(body_lines):
        m = RHS_RE.match(raw.rstrip("\r\n"))
        if m:
            rhs_positions.append((i, m))

    if len(rhs_positions) != 1:
        raise InputError(
            f"Targeted constraint c_e_BusBlock_balance({bus})_ has "
            f"{len(rhs_positions)} equality RHS lines; expected exactly one."
        )

    idx, rm = rhs_positions[0]
    old_rhs = rm.group("rhs")
    new_rhs = decimal_product_to_string(old_rhs, factor)

    raw = body_lines[idx]
    stripped = raw.rstrip("\r\n")
    eol = raw[len(stripped):]
    body_lines = list(body_lines)
    body_lines[idx] = f"{rm.group('indent')}= {new_rhs}{eol}"

    assert carrier_label is not None
    matched_counts[carrier_label] += 1
    return [header_line, *body_lines]


def transform(
    input_path: Path,
    requested_output_path: Path,
    compress_output: bool,
    global_factor: Decimal | None,
    carrier_factors: dict[str, Decimal],
) -> tuple[Path, Counter[str]]:
    output_path = effective_output_path(requested_output_path, compress_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if input_path.resolve() == output_path.resolve():
        raise InputError(
            "Input and effective output paths are identical; the input file is never overwritten."
        )

    # Keep the temporary file in the destination directory so os.replace() is atomic
    # on the same filesystem. Compression is determined explicitly, not by suffix.
    fd, tmp_name = tempfile.mkstemp(
        prefix=output_path.name + ".tmp.",
        dir=output_path.parent,
    )
    os.close(fd)
    tmp_path = Path(tmp_name)

    matched_counts: Counter[str] = Counter()

    try:
        with open_input_text(input_path) as src:
            with open_output_text(tmp_path, compress_output) as dst:
                current_header: str | None = None
                current_body: list[str] = []

                def flush_current() -> None:
                    nonlocal current_header, current_body
                    if current_header is None:
                        return
                    transformed = process_constraint_block(
                        current_header,
                        current_body,
                        global_factor,
                        carrier_factors,
                        matched_counts,
                    )
                    dst.writelines(transformed)
                    current_header = None
                    current_body = []

                in_constraints = False

                for raw in src:
                    stripped = raw.rstrip("\r\n")
                    low = stripped.strip().lower()

                    if low == "s.t.":
                        flush_current()
                        in_constraints = True
                        dst.write(raw)
                        continue

                    if low == "bounds":
                        flush_current()
                        in_constraints = False
                        dst.write(raw)
                        continue

                    if in_constraints and CONSTRAINT_HEADER_RE.match(stripped):
                        flush_current()
                        current_header = raw
                        current_body = []
                        continue

                    if current_header is not None:
                        current_body.append(raw)
                    else:
                        dst.write(raw)

                flush_current()

        if global_factor is None:
            missing = [carrier for carrier in carrier_factors if matched_counts[carrier] == 0]
            if missing:
                raise InputError(
                    "Requested carrier(s) matched no BusBlock balance constraints: "
                    + ", ".join(repr(x) for x in missing)
                )
        elif matched_counts["<all>"] == 0:
            raise InputError("No c_e_BusBlock_balance(...) constraints were found.")

        os.replace(tmp_path, output_path)
        return output_path, matched_counts

    except Exception:
        tmp_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Multiply BusBlock demand RHS values in a plain/.gz/.zip LP file; "
            "gzip output is the default."
        )
    )
    parser.add_argument("input_lp", type=Path, help="Input LP, LP.gz, or single-file ZIP archive.")
    parser.add_argument(
        "output_lp",
        type=Path,
        help=(
            "Requested output path. By default .gz is appended if needed; "
            "with --no-compress this path is used exactly."
        ),
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--factor",
        type=str,
        help="One positive decimal factor applied to all BusBlock demand RHS values.",
    )
    group.add_argument(
        "--carrier-factor",
        nargs=2,
        action="append",
        metavar=("CARRIER", "FACTOR"),
        help=(
            "Carrier-specific positive decimal factor. Repeat for multiple carriers, "
            "e.g. --carrier-factor electricity 1.1 --carrier-factor h2 1.2"
        ),
    )

    parser.add_argument(
        "--no-compress",
        action="store_true",
        help="Write plain-text output instead of the default gzip-compressed output.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        if not args.input_lp.is_file():
            raise InputError(f"Input file does not exist: {args.input_lp}")

        global_factor: Decimal | None = None
        carrier_factors: dict[str, Decimal] = {}

        if args.factor is not None:
            global_factor = parse_decimal(args.factor, "factor")
            if global_factor <= 0:
                raise InputError(f"Factor must be > 0, got {args.factor!r}.")
        else:
            assert args.carrier_factor is not None
            for carrier, factor_text in args.carrier_factor:
                if not carrier:
                    raise InputError("Carrier name must not be empty.")
                if carrier in carrier_factors:
                    raise InputError(f"Carrier {carrier!r} was specified more than once.")
                factor = parse_decimal(factor_text, f"factor for carrier {carrier!r}")
                if factor <= 0:
                    raise InputError(
                        f"Factor for carrier {carrier!r} must be > 0, got {factor_text!r}."
                    )
                carrier_factors[carrier] = factor

        output_path, counts = transform(
            args.input_lp,
            args.output_lp,
            compress_output=not args.no_compress,
            global_factor=global_factor,
            carrier_factors=carrier_factors,
        )

        print(f"Input kept unchanged: {args.input_lp}")
        print(f"Wrote: {output_path}")
        print(f"Output compression: {'gzip' if not args.no_compress else 'none'}")
        print("Modified BusBlock demand RHS values:")
        if global_factor is not None:
            print(f"  all carriers: factor {args.factor}, {counts['<all>']} constraints")
        else:
            for carrier, factor in carrier_factors.items():
                print(f"  {carrier}: factor {factor}, {counts[carrier]} constraints")
        print(f"Total modified RHS values: {sum(counts.values())}")
        return 0

    except (InputError, OSError, gzip.BadGzipFile, zipfile.BadZipFile, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
