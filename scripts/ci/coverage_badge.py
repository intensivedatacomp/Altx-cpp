#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Turn an lcov tracefile into a percentage and a badge, with no network.

The Python implementation uses ``coverage-badge``, which reads coverage.py's
data and has no lcov counterpart; shields.io would work but makes the README of
a private repository depend on an external service resolving a URL nobody here
controls. Both reasons point the same way: compute the number from the
tracefile and write the SVG, so the badge is a committed file like the Python
repository's and renders from the repository itself.

**The number is taken from the tracefile, not from ``lcov --summary``.** Line
coverage is the sum of the ``LH`` records over the sum of the ``LF`` records,
which is the same arithmetic lcov does, expressed against a format that is
specified rather than against output meant for a terminal -- and lcov 2.0's
``--list`` and ``--summary`` already disagree on how to present it.

Usage::

    scripts/ci/coverage_badge.py --info coverage.info --output .badges/coverage.svg

The percentage is printed to stdout, so a workflow can capture it for a job
summary without parsing the SVG back.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Thresholds are the conventional ones, and the colours are shields.io's, so a
# badge generated here is not visually distinguishable from the Python
# repository's next to it in a list of projects.
THRESHOLDS: tuple[tuple[float, str], ...] = (
    (95.0, "#4c1"),  # brightgreen
    (90.0, "#97ca00"),  # green
    (75.0, "#a4a61d"),  # yellowgreen
    (60.0, "#dfb317"),  # yellow
    (40.0, "#fe7d37"),  # orange
    (0.0, "#e05d44"),  # red
)

# Enough to lay out the right-hand box without measuring glyphs: the badge font
# is 11px DejaVu Sans, whose digits and '%' are all this wide.
CHARACTER_WIDTH = 7
LABEL = "coverage"
LABEL_WIDTH = 62  # the rendered width of LABEL at 11px, measured once


def line_coverage(info: Path) -> tuple[int, int]:
    """Sum the hit and instrumented line counts of an lcov tracefile.

    Parameters
    ----------
    info : Path
        A tracefile, as written by ``lcov --capture``. Records that lcov
        filtered out are simply absent, so the caller's ``--remove`` arguments
        decide what counts.

    Returns
    -------
    tuple of int
        ``(hit, found)`` -- lines executed at least once, and lines
        instrumented.
    """
    hit = 0
    found = 0
    for line in info.read_text(encoding="utf-8").splitlines():
        if line.startswith("LH:"):
            hit += int(line[3:])
        elif line.startswith("LF:"):
            found += int(line[3:])
    return hit, found


def colour(percentage: float) -> str:
    """Pick the badge colour for a coverage percentage.

    Parameters
    ----------
    percentage : float
        Line coverage, from 0 to 100.

    Returns
    -------
    str
        A hex colour from `THRESHOLDS`.
    """
    for threshold, value in THRESHOLDS:
        if percentage >= threshold:
            return value
    return THRESHOLDS[-1][1]  # pragma: no cover - the 0.0 row always matches


def badge(percentage: float) -> str:
    """Render the badge.

    Written out rather than fetched from shields.io: the file is committed, so
    the README of a repository that may be private renders without an external
    request, and a CI run that cannot reach the network still produces one.

    Parameters
    ----------
    percentage : float
        Line coverage, from 0 to 100.

    Returns
    -------
    str
        A complete SVG document.
    """
    text = f"{percentage:.1f}%"
    value_width = len(text) * CHARACTER_WIDTH + 10
    total = LABEL_WIDTH + value_width

    # Coordinates are in tenths, as shields.io's own template does, so the text
    # can be centred on a half pixel without fractions in the markup.
    label_centre = LABEL_WIDTH * 5
    value_centre = (LABEL_WIDTH + value_width / 2) * 10

    return f"""<svg xmlns="http://www.w3.org/2000/svg" \
xmlns:xlink="http://www.w3.org/1999/xlink" width="{total}" height="20" \
role="img" aria-label="{LABEL}: {text}">
  <title>{LABEL}: {text}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{LABEL_WIDTH}" height="20" fill="#555"/>
    <rect x="{LABEL_WIDTH}" width="{value_width}" height="20" \
fill="{colour(percentage)}"/>
    <rect width="{total}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" \
font-family="Verdana,Geneva,DejaVu Sans,sans-serif" \
text-rendering="geometricPrecision" font-size="110">
    <text x="{label_centre}" y="150" fill="#010101" fill-opacity=".3" \
transform="scale(.1)" textLength="{(LABEL_WIDTH - 10) * 10}">{LABEL}</text>
    <text x="{label_centre}" y="140" transform="scale(.1)" \
textLength="{(LABEL_WIDTH - 10) * 10}">{LABEL}</text>
    <text x="{value_centre}" y="150" fill="#010101" fill-opacity=".3" \
transform="scale(.1)">{text}</text>
    <text x="{value_centre}" y="140" transform="scale(.1)">{text}</text>
  </g>
</svg>
"""


def main() -> None:
    """Read the tracefile named on the command line and write the badge."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--info",
        type=Path,
        required=True,
        help="lcov tracefile to read",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="where to write the SVG; omit to print the percentage only",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=0.0,
        help="exit non-zero below this percentage (default: never)",
    )
    args = parser.parse_args()

    if not args.info.is_file():
        sys.exit(f"no such tracefile: {args.info}")

    hit, found = line_coverage(args.info)
    if found == 0:
        # Not a zero percentage: zero instrumented lines means the capture
        # found nothing, which is a broken coverage run reporting as a terrible
        # one. The two must not look alike on a badge.
        sys.exit(f"{args.info} instruments no lines at all -- did the tests run?")

    percentage = 100.0 * hit / found
    print(f"{percentage:.1f}")

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(badge(percentage), encoding="utf-8")

    if percentage < args.fail_under:
        sys.exit(
            f"line coverage {percentage:.1f}% is below the required "
            f"{args.fail_under:.1f}%"
        )


if __name__ == "__main__":
    main()
