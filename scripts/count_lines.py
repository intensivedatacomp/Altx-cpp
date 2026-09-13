#!/usr/bin/env python3
"""Count the lines in the repository, grouped by language.

Prints an ``Altx-cpp`` banner, the full hash of the commit being counted, and a
table with one row per language: how many files, how many lines, and what share
of the total that is. A second share column leaves out documentation and the
files that are not source at all -- the licence, the coverage badge, the spell
list -- since ``DevelopmentPlan.md`` alone would otherwise dwarf the C++.

**What is counted.** The files git tracks, read from the working tree, so
``build/``, ``coverage/`` and everything else in ``.gitignore`` stay out without
a second exclusion list to keep in step. Uncommitted edits to tracked files are
counted, and the commit line says so when there are any. ``--untracked`` adds
new files that are not ignored but not yet added either.

**What a line is.** Every newline-terminated line, blank or not, plus a final
line without a newline. Comments are not separated out: the files here are
commented heavily and on purpose, and a count that discounted that would
misdescribe them. A binary file counts as a file with no lines.

Usage::

    scripts/count_lines.py               # banner, commit, table
    scripts/count_lines.py --files       # ... and every file with its category
    scripts/count_lines.py --untracked   # include files not yet added to git

Standard library only, so it runs on the host as well as in any of the images.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# pyfiglet's "big" font, rendered once and pasted in, so the script needs no
# dependency to print it.
BANNER = r"""
          _ _
    /\   | | |
   /  \  | | |___  ________ ___ _ __  _ __
  / /\ \ | | __\ \/ /______/ __| '_ \| '_ \
 / ____ \| | |_ >  <      | (__| |_) | |_) |
/_/    \_\_|\__/_/\_\      \___| .__/| .__/
                               | |   | |
                               |_|   |_|
"""

# The two categories that are not code: printed last, and left out of the
# "of code" share.
NOT_CODE = ("Documentation", "Other")

# `.hip` is HIP, AMD's CUDA dialect of C++ -- no file has it yet, but the GPU
# backend will, and it is C++ in every sense that matters for a count.
CPP_SUFFIXES = frozenset(
    {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".h", ".ipp", ".tpp", ".hip"}
)
CONFIG_SUFFIXES = frozenset({".yml", ".yaml", ".json", ".toml", ".ini", ".cfg"})
# Configuration files recognisable by name alone, most of them extensionless.
CONFIG_NAMES = frozenset(
    {
        ".clang-format",
        ".clang-tidy",
        ".clangd",
        ".dockerignore",
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
        ".gitmodules",
        ".trivyignore",
    }
)
DOC_SUFFIXES = frozenset({".md", ".rst", ".dox"})
VIM_NAMES = frozenset({"vimrc", ".vimrc", "gvimrc", ".gvimrc"})

BAR_WIDTH = 24  # characters of '#' for a category holding every line


@dataclass
class Tally:
    """Files and lines counted for one category."""

    files: int = 0
    lines: int = 0


def classify(path: PurePosixPath, head: bytes) -> str:
    """Name the category a file is counted in.

    Parameters
    ----------
    path : PurePosixPath
        The file, relative to the repository root.
    head : bytes
        Its first line, which recognises a script by its ``#!`` line when the
        name alone does not.

    Returns
    -------
    str
        The category; ``"Other"`` when nothing matched.
    """
    name, suffix = path.name, path.suffix
    # A configure_file template is counted as what it generates, so
    # src/core/Version.hpp.in is C++.
    if suffix == ".in":
        return classify(path.with_suffix(""), head)
    shebang = head.decode(errors="replace") if head.startswith(b"#!") else ""

    if name == "Dockerfile" or suffix in {".Dockerfile", ".dockerfile"}:
        return "Dockerfile"
    if name == "CMakeLists.txt" or suffix == ".cmake":
        return "CMake"
    if suffix in CPP_SUFFIXES:
        return "C++"
    if suffix == ".py" or "python" in shebang:
        return "Python"
    if suffix in {".sh", ".bash"} or "bash" in shebang or shebang.endswith("/sh"):
        return "Bash"
    if name in VIM_NAMES or suffix == ".vim":
        return "Vim script"
    # Workflows and composite actions are YAML too, but they are CI's code
    # rather than a tool's settings, and big enough to be worth seeing apart.
    if path.parts[0] == ".github" and suffix in {".yml", ".yaml"}:
        return "GitHub Actions"
    if suffix in CONFIG_SUFFIXES or name in CONFIG_NAMES:
        return "Configuration"
    if suffix in DOC_SUFFIXES:
        return "Documentation"
    return "Other"


def count_lines(data: bytes) -> int:
    """Count the lines in a file's contents.

    Parameters
    ----------
    data : bytes
        The whole file.

    Returns
    -------
    int
        Newline-terminated lines, plus one for an unterminated last line, or 0
        for a binary file -- one with a NUL byte near the start.
    """
    if b"\0" in data[:8192]:
        return 0
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git inside the repository.

    Parameters
    ----------
    root : Path
        Any directory inside the working tree.
    *args : str
        The git subcommand and its arguments.

    Returns
    -------
    subprocess.CompletedProcess of str
        The finished process, whatever its exit status. Exits when git itself
        is not installed.
    """
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True
        )
    except FileNotFoundError:
        sys.exit("git is not on PATH -- this counts the files git tracks")


def repo_root() -> Path:
    """Locate the top of the working tree this script belongs to.

    Returns
    -------
    Path
        The repository root. Exits when the script is not inside a checkout.
    """
    result = git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        sys.exit(f"not inside a git checkout: {result.stderr.strip()}")
    return Path(result.stdout.strip())


def describe_commit(root: Path) -> str:
    """Name the commit the count describes.

    Parameters
    ----------
    root : Path
        The repository root.

    Returns
    -------
    str
        ``commit <full hash>``, noting uncommitted changes to tracked files,
        since those are what is actually read.
    """
    head = git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    if head.returncode != 0:
        return "commit (none yet)"
    dirty = git(root, "status", "--porcelain", "--untracked-files=no").stdout
    note = "  + uncommitted changes, which are counted" if dirty.strip() else ""
    return f"commit {head.stdout.strip()}{note}"


def list_files(root: Path, untracked: bool) -> list[PurePosixPath]:
    """List the files to count.

    Parameters
    ----------
    root : Path
        The repository root.
    untracked : bool
        Also include files that are neither tracked nor ignored.

    Returns
    -------
    list of PurePosixPath
        Paths relative to `root`, sorted. A tracked file deleted from the
        working tree is still listed; the caller skips what does not exist.
    """
    args = ["ls-files", "-z", "--cached"]
    if untracked:
        args += ["--others", "--exclude-standard"]
    result = git(root, *args)
    if result.returncode != 0:
        sys.exit(f"git ls-files failed: {result.stderr.strip()}")
    return sorted({PurePosixPath(name) for name in result.stdout.split("\0") if name})


def percent(part: int, whole: int) -> str:
    """Format a share as a percentage with one decimal.

    Parameters
    ----------
    part : int
        The share.
    whole : int
        What it is a share of.

    Returns
    -------
    str
        For example ``"12.5%"``, or ``"-"`` when `whole` is zero.
    """
    return f"{100 * part / whole:.1f}%" if whole else "-"


def table(tallies: dict[str, Tally]) -> list[str]:
    """Lay the tallies out as the printed table.

    Parameters
    ----------
    tallies : dict of str to Tally
        Files and lines per category; categories with no files are absent.

    Returns
    -------
    list of str
        The table, one string per line: languages by size, then the two
        categories that are not code, then the code and grand totals.
    """
    total = sum(t.lines for t in tallies.values())
    code = [c for c in tallies if c not in NOT_CODE]
    code_lines = sum(tallies[c].lines for c in code)
    code_files = sum(tallies[c].files for c in code)
    order = sorted(code, key=lambda c: (-tallies[c].lines, c))
    order += [c for c in NOT_CODE if c in tallies]

    def row(label: str, files: int, lines: int, share: str, code_share: str) -> str:
        return f"{label:<16} {files:>5} {lines:>7} {share:>7} {code_share:>8}"

    header = f"{'Language':<16} {'Files':>5} {'Lines':>7} {'Of all':>7} {'Of code':>8}"
    rule = "-" * len(header)
    lines = [header, rule]
    for category in order:
        tally = tallies[category]
        code_share = "" if category in NOT_CODE else percent(tally.lines, code_lines)
        bar = "#" * round(BAR_WIDTH * tally.lines / total) if total else ""
        lines.append(
            row(
                category,
                tally.files,
                tally.lines,
                percent(tally.lines, total),
                code_share,
            )
            + f"  {bar}"
        )
    lines.append(rule)
    lines.append(
        row("Code", code_files, code_lines, percent(code_lines, total), "100.0%")
    )
    files = sum(t.files for t in tallies.values())
    lines.append(row("Total", files, total, "100.0%", ""))
    # An empty bar or share column would otherwise leave trailing blanks, which
    # show up the moment the output is pasted anywhere that marks them.
    return [line.rstrip() for line in lines]


def main() -> None:
    """Count, then print the banner, the commit and the table."""
    parser = argparse.ArgumentParser(
        description="Count the lines in the repository, grouped by language."
    )
    parser.add_argument(
        "--files",
        action="store_true",
        help="also list every file with its category and line count",
    )
    parser.add_argument(
        "--untracked",
        action="store_true",
        help="include files that are neither tracked nor ignored",
    )
    args = parser.parse_args()

    root = repo_root()
    tallies: dict[str, Tally] = {}
    listing: list[tuple[str, int, PurePosixPath]] = []
    for relative in list_files(root, args.untracked):
        path = root / relative
        if not path.is_file():
            continue
        data = path.read_bytes()
        category = classify(relative, data.split(b"\n", 1)[0].rstrip())
        lines = count_lines(data)
        tally = tallies.setdefault(category, Tally())
        tally.files += 1
        tally.lines += lines
        listing.append((category, lines, relative))

    print(BANNER.strip("\n"))
    print()
    print(describe_commit(root))
    print()
    print("\n".join(table(tallies)))

    if args.files:
        print()
        for category, lines, relative in sorted(listing):
            print(f"{category:<16} {lines:>7}  {relative}")


if __name__ == "__main__":
    main()
