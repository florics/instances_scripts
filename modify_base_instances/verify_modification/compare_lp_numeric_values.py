#!/usr/bin/env python3
"""
compare_lp_numeric_values.py

Compare actual numerical constants in two LP files while ignoring digits that
are part of identifiers, carrier names, technology names, and indices.

Example:
    flow(BB_ch4_shortage_BB_ch4_0_2)
contributes no numerical tokens.

But:
    +1 flow(...)
    -0.2498 InvestmentFlowBlock_capacity(...)
    = 77000.0
contribute +1, -0.2498, and 77000.0 exactly as strings.

No float conversion is performed.

Usage:
    python compare_lp_numeric_values.py first.lp second.lp

Optional:
    --anchor-length 20
    --lookahead 20000
    --show 30
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import deque
from pathlib import Path
from typing import Deque, Iterator, Sequence


NUMBER_RE = re.compile(
    r"""
    (?<![A-Za-z0-9_.])
    [+-]?
    (?:
        (?:\d+\.\d*|\.\d+|\d+)
        (?:[eE][+-]?\d+)?
    )
    (?![A-Za-z0-9_.])
    """,
    re.VERBOSE,
)

# Match oemof/Pyomo-style names with one parenthesized index payload.
PAREN_IDENTIFIER_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*\([^()\r\n]*\)"
)

# Match remaining bare identifiers, including names such as ch4/h2.
BARE_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def mask_identifiers(line: str) -> str:
    line = PAREN_IDENTIFIER_RE.sub(" ", line)
    line = BARE_IDENTIFIER_RE.sub(" ", line)
    return line


def iter_numbers(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8", errors="strict") as f:
        for line in f:
            algebra = mask_identifiers(line)
            for match in NUMBER_RE.finditer(algebra):
                yield match.group(0)


class BufferedTokenReader:
    def __init__(self, iterator: Iterator[str]) -> None:
        self._iterator = iterator
        self._buffer: Deque[str] = deque()
        self.exhausted = False
        self.consumed = 0

    def _fill(self, n: int) -> None:
        while len(self._buffer) < n and not self.exhausted:
            try:
                self._buffer.append(next(self._iterator))
            except StopIteration:
                self.exhausted = True

    def first(self) -> str | None:
        self._fill(1)
        return self._buffer[0] if self._buffer else None

    def peek(self, n: int) -> list[str]:
        self._fill(n)
        out = []
        for k, token in enumerate(self._buffer):
            if k >= n:
                break
            out.append(token)
        return out

    def consume(self, n: int) -> list[str]:
        self._fill(n)
        n = min(n, len(self._buffer))
        out = [self._buffer.popleft() for _ in range(n)]
        self.consumed += n
        return out


def shorten(tokens: Sequence[str], limit: int) -> str:
    if not tokens:
        return "<empty>"
    if len(tokens) <= limit:
        return " ".join(tokens)
    h = max(1, limit // 2)
    t = max(1, limit - h)
    return " ".join(tokens[:h]) + "  ...  " + " ".join(tokens[-t:])


def find_resync(
    a: Sequence[str],
    b: Sequence[str],
    anchor_length: int,
) -> tuple[int, int, int] | None:
    if not a or not b:
        return None

    effective = min(anchor_length, len(a), len(b))
    if effective <= 0:
        return None

    prefix_len = min(4, effective)
    end_b = len(b) - effective + 1
    if end_b <= 0:
        return None

    index_b: dict[tuple[str, ...], list[int]] = {}
    for j in range(end_b):
        key = tuple(b[j:j + prefix_len])
        index_b.setdefault(key, []).append(j)

    end_a = len(a) - effective + 1
    best = None
    best_distance = None

    for i in range(end_a):
        key = tuple(a[i:i + prefix_len])
        candidates = index_b.get(key)
        if not candidates:
            continue

        for j in candidates:
            distance = i + j
            if best_distance is not None and distance >= best_distance:
                break

            if a[i:i + effective] == b[j:j + effective]:
                best = (i, j, effective)
                best_distance = distance
                break

        if best_distance == i:
            break

    return best


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Compare LP numerical constants as exact strings while ignoring "
            "digits inside names and indices."
        )
    )
    p.add_argument("first_lp", type=Path)
    p.add_argument("second_lp", type=Path)
    p.add_argument("--anchor-length", type=int, default=20)
    p.add_argument("--lookahead", type=int, default=20000)
    p.add_argument("--show", type=int, default=30)
    p.add_argument("--max-differences", type=int, default=1000)
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.anchor_length <= 0:
        print("ERROR: --anchor-length must be > 0.", file=sys.stderr)
        return 2
    if args.lookahead < args.anchor_length:
        print("ERROR: --lookahead must be >= --anchor-length.", file=sys.stderr)
        return 2
    if args.show <= 0:
        print("ERROR: --show must be > 0.", file=sys.stderr)
        return 2
    if args.max_differences < 0:
        print("ERROR: --max-differences must be >= 0.", file=sys.stderr)
        return 2

    for path in (args.first_lp, args.second_lp):
        if not path.is_file():
            print(f"ERROR: File does not exist: {path}", file=sys.stderr)
            return 2

    first = BufferedTokenReader(iter_numbers(args.first_lp))
    second = BufferedTokenReader(iter_numbers(args.second_lp))

    differences = 0
    equal_tokens = 0
    diff_tokens_first = 0
    diff_tokens_second = 0

    while True:
        a = first.first()
        b = second.first()

        if a is None and b is None:
            break

        if a is None or b is None:
            sf = first.consumed
            ss = second.consumed
            tf = first.peek(args.lookahead)
            ts = second.peek(args.lookahead)
            df = first.consume(len(tf))
            ds = second.consume(len(ts))

            differences += 1
            diff_tokens_first += len(df)
            diff_tokens_second += len(ds)

            print("=" * 80)
            print(f"Difference {differences}")
            print(f"first : [{sf:,}, {sf + len(df):,}) ({len(df):,} tokens)")
            print(f"second: [{ss:,}, {ss + len(ds):,}) ({len(ds):,} tokens)")
            print("first : " + shorten(df, args.show))
            print("second: " + shorten(ds, args.show))
            break

        if a == b:
            first.consume(1)
            second.consume(1)
            equal_tokens += 1
            continue

        sf = first.consumed
        ss = second.consumed

        n = args.lookahead + args.anchor_length
        bf = first.peek(n)
        bs = second.peek(n)

        resync = find_resync(bf, bs, args.anchor_length)

        if resync is None:
            print("=" * 80)
            print(
                f"No common anchor of length {args.anchor_length} within "
                f"the next {args.lookahead:,} numerical constants."
            )
            print(f"first token index : {sf:,}")
            print(f"second token index: {ss:,}")
            print("first preview : " + shorten(bf, args.show))
            print("second preview: " + shorten(bs, args.show))
            print("Try increasing --lookahead.")
            return 3

        i, j, anchor_used = resync
        df = first.consume(i)
        ds = second.consume(j)

        differences += 1
        diff_tokens_first += len(df)
        diff_tokens_second += len(ds)

        print("=" * 80)
        print(f"Difference {differences}")
        print(f"first : [{sf:,}, {sf + len(df):,}) ({len(df):,} tokens)")
        print(f"second: [{ss:,}, {ss + len(ds):,}) ({len(ds):,} tokens)")
        print("first : " + shorten(df, args.show))
        print("second: " + shorten(ds, args.show))

        af = first.peek(anchor_used)
        ass = second.peek(anchor_used)
        if af != ass:
            print("INTERNAL ERROR: anchor verification failed.", file=sys.stderr)
            return 4

        print("resync: " + shorten(af, args.show))

        if args.max_differences and differences >= args.max_differences:
            print(f"Stopped after --max-differences={args.max_differences}.")
            break

    print("=" * 80)
    print("Summary")
    print(f"consumed first : {first.consumed:,}")
    print(f"consumed second: {second.consumed:,}")
    print(f"equal aligned  : {equal_tokens:,}")
    print(f"differences    : {differences:,}")
    print(f"diff tokens 1  : {diff_tokens_first:,}")
    print(f"diff tokens 2  : {diff_tokens_second:,}")

    if differences == 0:
        print("RESULT: Numerical constants are exactly identical.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
