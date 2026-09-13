#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Require a braced block after every OpenMP directive that takes a statement.

``InsertBraces: true`` in .clang-format braces the body of every ``if``,
``else``, ``for``, ``while`` and ``do``. It cannot brace an OpenMP structured
block: to clang-format a ``#pragma`` is an opaque preprocessor line, and the
statement under it is formatted as though the pragma were not there. So::

    #pragma omp critical
    total += local;

survives formatting untouched, and clang-tidy has no check for it either. This
script is that check. Written out, the rule is the one ``InsertBraces`` applies
to ``if``: the extent of a region is visible from its braces, never inferred
from "the next statement".

Directives fall into three kinds, and only the first is checked:

* **block** directives -- ``parallel``, ``critical``, ``single``, ``task`` and
  the rest of `BLOCK_DIRECTIVES` -- apply to the next statement, which must be
  a ``{`` block;
* **loop** directives -- any name containing a word from `LOOP_WORDS`, such as
  ``parallel for`` -- must be followed by the ``for`` statement itself, where a
  brace is a compile error. The loop *body* is ``InsertBraces``'s to brace;
* **everything else** is left alone. ``atomic`` takes an expression statement,
  where a brace is also a compile error; ``barrier``, ``taskwait``,
  ``declare ...``, ``target update`` and ``ordered depend(...)`` take no
  statement at all. A directive this script does not recognise is not checked,
  so a newer OpenMP spelling cannot produce a false positive -- only a missed
  check.

This reads lines, not a syntax tree. Comments and string literals are blanked
before anything is matched, so a pragma quoted in a comment is not a pragma, but
a directive assembled by a macro is invisible to it.

Usage::

    scripts/check_omp_braces.py src/core/LawExtractor.cpp ...

pre-commit passes the changed C, C++ and CUDA files. To exempt one directive,
put ``// omp-braces: ignore`` on its line, followed by the reason.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Directives whose associated statement is a structured block. `ordered` is here
# for its block form; the `depend`/`doacross` form takes no statement and is
# excluded in `needs_block`.
BLOCK_DIRECTIVES = frozenset(
    {
        "assume",
        "critical",
        "masked",
        "master",
        "ordered",
        "parallel",
        "scope",
        "section",
        "sections",
        "single",
        "target",
        "task",
        "taskgroup",
        "teams",
    }
)

# A combined name containing any of these is loop-associated: `parallel for`,
# `target teams distribute`, `masked taskloop simd`. The one exception is
# `ordered simd`, a block, which `needs_block` handles before this set is read.
LOOP_WORDS = frozenset(
    {"distribute", "for", "loop", "simd", "taskloop", "tile", "unroll"}
)

# `target` takes a block; `target enter data`, `target exit data` and
# `target update` take nothing.
STANDALONE_WORDS = frozenset({"enter", "exit", "update"})

# Every word that can continue a directive name rather than start its clauses.
# `data` is listed only so that `target data` reads as one name.
DIRECTIVE_WORDS = BLOCK_DIRECTIVES | LOOP_WORDS | STANDALONE_WORDS | {"data"}

PRAGMA = re.compile(r"^\s*#\s*pragma\s+omp\b(?P<rest>.*)$")
WORD = re.compile(r"[A-Za-z_]\w*|\S")
STANDALONE_ORDERED = re.compile(r"\b(?:depend|doacross)\s*\(")
IGNORE_MARKER = "omp-braces: ignore"


def strip_comments(lines: list[str]) -> list[str]:
    """Blank out comments, and the contents of string and character literals.

    Parameters
    ----------
    lines : list of str
        A source file, one entry per physical line.

    Returns
    -------
    list of str
        The same lines with every comment removed and every literal emptied, so
        that ``"/*"`` in a string cannot open a comment and ``// #pragma omp``
        cannot be matched. Line numbering is unchanged. Raw string literals are
        not recognised.
    """
    out = []
    in_block = False
    for line in lines:
        kept: list[str] = []
        quote = ""  # a literal never spans lines without a backslash
        i = 0
        while i < len(line):
            if in_block:
                if line.startswith("*/", i):
                    in_block = False
                    kept.append(" ")
                    i += 2
                else:
                    i += 1
            elif quote:
                if line[i] == "\\":
                    i += 2
                elif line[i] == quote:
                    kept.append(quote)
                    quote = ""
                    i += 1
                else:
                    i += 1
            elif line.startswith("//", i):
                break
            elif line.startswith("/*", i):
                in_block = True
                i += 2
            else:
                if line[i] in "\"'":
                    quote = line[i]
                kept.append(line[i])
                i += 1
        out.append("".join(kept))
    return out


def directive_name(text: str) -> list[str]:
    """Split the directive name off the front of a pragma's text.

    Parameters
    ----------
    text : str
        Everything after ``#pragma omp``, continuations already joined.

    Returns
    -------
    list of str
        The words of the name, such as ``["parallel", "for"]`` for
        ``parallel for num_threads(4)``. The name ends at the first word that is
        not in `DIRECTIVE_WORDS` and after any word followed by ``(``, so
        ``critical(update)`` is ``["critical"]``. Empty for an unknown directive.
    """
    words = []
    for match in WORD.finditer(text):
        word = match.group()
        if word not in DIRECTIVE_WORDS:
            break
        words.append(word)
        if text[match.end() :].lstrip().startswith("("):
            break
    return words


def needs_block(words: list[str], text: str) -> bool:
    """Decide whether a directive's statement must be a braced block.

    Parameters
    ----------
    words : list of str
        The directive name, from `directive_name`.
    text : str
        The pragma's full text, clauses included.

    Returns
    -------
    bool
        True for a block directive. False for loop-associated, standalone and
        unrecognised directives, and for ``atomic``.
    """
    if not words or words[0] not in BLOCK_DIRECTIVES:
        return False
    if words[0] == "ordered":
        return not STANDALONE_ORDERED.search(text)
    if any(word in LOOP_WORDS for word in words):
        return False
    return not (words[0] == "target" and any(w in STANDALONE_WORDS for w in words))


def check(path: Path) -> list[str]:
    """Find every block directive in a file that is not followed by a brace.

    Parameters
    ----------
    path : Path
        A C, C++ or CUDA source file.

    Returns
    -------
    list of str
        One ``path:line: message`` per violation, in file order.
    """
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    code = strip_comments(raw)
    problems = []
    i = 0
    while i < len(code):
        match = PRAGMA.match(code[i])
        if not match:
            i += 1
            continue
        first = i
        text = match.group("rest")
        ignored = IGNORE_MARKER in raw[i]
        while text.rstrip().endswith("\\") and i + 1 < len(code):
            i += 1
            text = text.rstrip()[:-1] + " " + code[i]
            ignored = ignored or IGNORE_MARKER in raw[i]
        i += 1

        words = directive_name(text)
        if ignored or not needs_block(words, text):
            continue
        following = next((line for line in code[i:] if line.strip()), "")
        if not following.lstrip().startswith("{"):
            problems.append(
                f"{path}:{first + 1}: '#pragma omp {' '.join(words)}' must be "
                "followed by a { block, not a single statement"
            )
    return problems


def main() -> int:
    """Check the files named on the command line.

    Returns
    -------
    int
        0 when every file passes, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0] if __doc__ else None
    )
    parser.add_argument("files", nargs="*", type=Path, help="C, C++ or CUDA sources")
    args = parser.parse_args()

    problems = [problem for path in args.files for problem in check(path)]
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
