#!/usr/bin/env python3
"""
compare_lp_numbers.py

Compare the numerical tokens occurring in two LP files while preserving their
order of occurrence.

The comparison is purely textual:
    "1"
    "1.0"
    "+1"
    "1e0"
are treated as different strings.

Approach
--------
1. Extract all numerical tokens from both files in order.
2. Compare them sequentially.
3. When a mismatch is encountered, search ahead in both sequences for a
   sufficiently long identical run ("anchor").
4. Everything between the mismatch and that common anchor is reported as one
   differing interval.
5. Continue after the anchor.

This is useful for comparing an original LP file with a transformed LP file
where constraints/variables may have been removed, while most numerical data
should remain unchanged.

By default, numbers occurring inside names are ALSO extracted. For example,

    flow(B_electricity_0_17)

contributes the tokens

    0
    17

This follows the literal "extract all numbers" approach.

Usage
-----
python compare_lp_numbers.py original.lp transformed.lp

Useful options:

python compare_lp_numbers.py original.lp transformed.lp \
    --anchor-length 10 \
    --lookahead 100000 \
    --show 20

Arguments
---------
--anchor-length:
    Number of consecutive identical numerical tokens required to declare that
    the two sequences have resynchronized.

--lookahead:
    Maximum number of tokens searched ahead from a mismatch in each file.
    Use 0 for unlimited lookahead.

--show:
    Maximum number of tokens printed from each side of a differing interval.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterator, Sequence


# Match signed decimal/scientific-notation numbers.
#
# Examples:
#   0
#   +1
#   -1
#   1.0
#   .5
#   -0.2498
#   1e-7
#   +1.23E+09
#
# The lookarounds prevent a number from starting/ending in the middle of
# another ordinary alphanumeric numeric token, while still allowing numbers
# embedded in names such as "..._0_17".
NUMBER_RE = re.compile(
    r"""
    [+-]?
    (?:
        (?:\d+\.\d*|\.\d+|\d+)
        (?:[eE][+-]?\d+)?
    )
    """,
    re.VERBOSE,
)


def extract_numbers(path: Path) -> list[str]:
    """
    Extract all numerical tokens from a file in order of occurrence.

    The numerical strings are returned exactly as they occurred in the file.
    No conversion to float is performed.
    """
    numbers: list[str] = []

    with path.open("r", encoding="utf-8", errors="strict") as f:
        for line in f:
            numbers.extend(match.group(0) for match in NUMBER_RE.finditer(line))

    return numbers


def find_resynchronization(
    a: Sequence[str],
    b: Sequence[str],
    start_a: int,
    start_b: int,
    anchor_length: int,
    lookahead: int,
) -> tuple[int, int] | None:
    """
    Find positions at which a and b contain the same anchor of consecutive
    tokens.

    Searches after a mismatch beginning at start_a/start_b.

    Among all anchors found inside the search windows, prefer the one requiring
    the smallest total skip:

        (candidate_a - start_a) + (candidate_b - start_b)

    This tends to find the nearest plausible resynchronization point.
    """
    remaining_a = len(a) - start_a
    remaining_b = len(b) - start_b

    if remaining_a < anchor_length or remaining_b < anchor_length:
        return None

    if lookahead == 0:
        end_a = len(a) - anchor_length + 1
        end_b = len(b) - anchor_length + 1
    else:
        end_a = min(
            len(a) - anchor_length + 1,
            start_a + lookahead + 1,
        )
        end_b = min(
            len(b) - anchor_length + 1,
            start_b + lookahead + 1,
        )

    # Map every anchor in B's search window to its occurrence positions.
    #
    # Normally the first occurrence is what we want. However, LP files contain
    # many repeated numbers, so retain all occurrences to choose the nearest
    # pair globally.
    anchors_b: dict[tuple[str, ...], list[int]] = {}

    for pos_b in range(start_b, end_b):
        anchor = tuple(b[pos_b : pos_b + anchor_length])
        anchors_b.setdefault(anchor, []).append(pos_b)

    best: tuple[int, int] | None = None
    best_distance: int | None = None

    for pos_a in range(start_a, end_a):
        anchor = tuple(a[pos_a : pos_a + anchor_length])

        positions_b = anchors_b.get(anchor)
        if positions_b is None:
            continue

        # B positions are increasing. Since start_b is fixed, the earliest
        # occurrence gives the smallest skip on the B side.
        pos_b = positions_b[0]

        distance = (pos_a - start_a) + (pos_b - start_b)

        if best_distance is None or distance < best_distance:
            best = (pos_a, pos_b)
            best_distance = distance

            # Cannot do better than zero skipped tokens.
            if distance == 0:
                break

    return best


def shorten(tokens: Sequence[str], limit: int) -> str:
    """Pretty-print a possibly long token interval."""
    if not tokens:
        return "<empty>"

    if len(tokens) <= limit:
        return " ".join(tokens)

    head_count = max(1, limit // 2)
    tail_count = max(1, limit - head_count)

    head = " ".join(tokens[:head_count])
    tail = " ".join(tokens[-tail_count:])

    return f"{head}  ...  {tail}"


def compare_sequences(
    a: Sequence[str],
    b: Sequence[str],
    anchor_length: int,
    lookahead: int,
) -> list[tuple[int, int, int, int]]:
    """
    Compare the two numerical-token sequences.

    Returns differing intervals as tuples:

        (a_start, a_end, b_start, b_end)

    where the intervals are half-open:
        a[a_start:a_end]
        b[b_start:b_end]
    """
    differences: list[tuple[int, int, int, int]] = []

    i = 0
    j = 0

    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue

        diff_start_a = i
        diff_start_b = j

        resync = find_resynchronization(
            a=a,
            b=b,
            start_a=i,
            start_b=j,
            anchor_length=anchor_length,
            lookahead=lookahead,
        )

        if resync is None:
            # No reliable common continuation found.
            differences.append(
                (diff_start_a, len(a), diff_start_b, len(b))
            )
            i = len(a)
            j = len(b)
            break

        resync_a, resync_b = resync

        differences.append(
            (diff_start_a, resync_a, diff_start_b, resync_b)
        )

        i = resync_a
        j = resync_b

    # One sequence may have trailing tokens after the other ends.
    if i < len(a) or j < len(b):
        differences.append((i, len(a), j, len(b)))

    return differences


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare numerical strings in two LP files while allowing "
            "differing intervals and subsequent resynchronization."
        )
    )

    parser.add_argument(
        "first_lp",
        type=Path,
        help="First LP file.",
    )
    parser.add_argument(
        "second_lp",
        type=Path,
        help="Second LP file.",
    )
    parser.add_argument(
        "--anchor-length",
        type=int,
        default=10,
        help=(
            "Number of consecutive identical tokens required for "
            "resynchronization (default: 10)."
        ),
    )
    parser.add_argument(
        "--lookahead",
        type=int,
        default=100000,
        help=(
            "Maximum number of tokens to search ahead after a mismatch "
            "(default: 100000; 0 means unlimited)."
        ),
    )
    parser.add_argument(
        "--show",
        type=int,
        default=30,
        help=(
            "Maximum number of tokens displayed for each differing side "
            "(default: 30)."
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.anchor_length <= 0:
        print("ERROR: --anchor-length must be positive.", file=sys.stderr)
        return 2

    if args.lookahead < 0:
        print("ERROR: --lookahead must be nonnegative.", file=sys.stderr)
        return 2

    if args.show <= 0:
        print("ERROR: --show must be positive.", file=sys.stderr)
        return 2

    for path in (args.first_lp, args.second_lp):
        if not path.is_file():
            print(f"ERROR: File does not exist: {path}", file=sys.stderr)
            return 2

    print(f"Reading: {args.first_lp}")
    first = extract_numbers(args.first_lp)

    print(f"Reading: {args.second_lp}")
    second = extract_numbers(args.second_lp)

    print()
    print("Numerical token counts:")
    print(f"  first : {len(first):,}")
    print(f"  second: {len(second):,}")
    print()

    if first == second:
        print("RESULT: The numerical-token sequences are exactly identical.")
        return 0

    differences = compare_sequences(
        first,
        second,
        anchor_length=args.anchor_length,
        lookahead=args.lookahead,
    )

    print(f"Found {len(differences):,} differing interval(s).")
    print()

    for number, (a_start, a_end, b_start, b_end) in enumerate(
        differences,
        start=1,
    ):
        tokens_a = first[a_start:a_end]
        tokens_b = second[b_start:b_end]

        print("=" * 80)
        print(f"Difference {number}")
        print()
        print(
            f"first : token indices [{a_start}, {a_end}) "
            f"({len(tokens_a):,} tokens)"
        )
        print(
            f"second: token indices [{b_start}, {b_end}) "
            f"({len(tokens_b):,} tokens)"
        )
        print()
        print("first:")
        print(f"  {shorten(tokens_a, args.show)}")
        print()
        print("second:")
        print(f"  {shorten(tokens_b, args.show)}")
        print()

        # Show the common continuation after the differing interval, where
        # possible. This makes the resynchronization explicit.
        if a_end < len(first) and b_end < len(second):
            common_end = min(
                a_end + args.anchor_length,
                len(first),
                b_end + args.anchor_length,
            )

            n_common = common_end - a_end

            if (
                n_common > 0
                and first[a_end : a_end + n_common]
                == second[b_end : b_end + n_common]
            ):
                print(
                    f"Resynchronizes with the next {n_common} identical "
                    f"tokens:"
                )
                print(
                    "  "
                    + " ".join(first[a_end : a_end + n_common])
                )
                print()

    print("=" * 80)
    print()
    print("Summary:")
    print(f"  numerical tokens in first : {len(first):,}")
    print(f"  numerical tokens in second: {len(second):,}")
    print(f"  differing intervals       : {len(differences):,}")

    skipped_first = sum(a_end - a_start for a_start, a_end, _, _ in differences)
    skipped_second = sum(
        b_end - b_start for _, _, b_start, b_end in differences
    )

    print(f"  tokens inside differences, first : {skipped_first:,}")
    print(f"  tokens inside differences, second: {skipped_second:,}")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())