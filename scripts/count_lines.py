#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Count the lines in the repository, by language or by directory.

Prints an ``Altx-cpp`` banner, the version and full commit hash of what was
counted, and a table with one row per language: how many files and lines, and
each language's share of the total. A second share column leaves out
documentation and the files that are not source at all -- the licence, the
coverage badge, the spell list -- since ``DevelopmentPlan.md`` alone would
otherwise dwarf the C++.

**What is counted.** By default, the files git tracks, read from the working
tree, so ``build/``, ``coverage/`` and everything else in ``.gitignore`` stay
out without a second exclusion list to keep in step. ``--untracked`` adds files
that are neither tracked nor ignored; ``--rev`` counts a commit or tag as it was
committed, without checking it out.

**The version** is ``git describe --tags --dirty --always``, the command
``cmake/GitVersion.cmake`` stamps into every build, so the two cannot disagree:
``v0.0.2-32-g6456b0b-dirty`` is 32 commits after the tag ``v0.0.2``, with
uncommitted changes.

**What a line is.** Every newline-terminated line, plus an unterminated last
one; a binary file counts as a file with no lines. ``--breakdown`` sorts lines
into code, comments and blank lines by each language's comment syntax: ``#``,
``//`` and ``/* */``, Vim's ``"``, CMake's ``#[[ ]]`` and Python docstrings. A
line holding code *and* a trailing comment is code. It is a line classifier,
not a parser -- a ``/*`` inside a C++ string literal will fool it.

Usage::

    scripts/count_lines.py                        # banner, version, table
    scripts/count_lines.py --breakdown            # ... code/comments/blank
    scripts/count_lines.py --compare v0.0.2       # the change since a tag
    scripts/count_lines.py --by directory --depth 2
    scripts/count_lines.py --rev v0.0.1 --code-only
    scripts/count_lines.py src tests --largest 5  # only under src/ and tests/
    scripts/count_lines.py --format markdown      # a table for a pull request
    scripts/count_lines.py --format json          # everything, for another tool

Standard library only, so it runs on the host as well as in any of the images.
The ``# /// script`` block above is inline script metadata (PEP 723). It is what
makes ``uv run scripts/count_lines.py`` treat this file as a standalone script;
without it, uv mistakes the repository's ``pyproject.toml`` -- which configures
the linters and declares no project -- for a project without a
``requires-python``, and warns about it on every run.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
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

EXAMPLES = """\
examples:
  %(prog)s --breakdown             code, comment and blank lines per language
  %(prog)s --compare v0.0.2        how much each language grew since a tag
  %(prog)s --by directory --depth 2
  %(prog)s --rev v0.0.1 --code-only
  %(prog)s src tests --largest 5   only under src/ and tests/
  %(prog)s --format markdown       a table to paste into a pull request
"""

# The two categories that are not code: printed last, and left out of the
# "of code" share and of --code-only.
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

BAR_WIDTH = 24  # characters of '#' for a group holding every line

# `git describe` away from a tag: <tag>-<commits since>-g<hash>[-dirty].
DESCRIBED = re.compile(r"^(?P<tag>.+)-(?P<ahead>\d+)-g[0-9a-f]+(?:-dirty)?$")
# `--always` with no tag reachable at all: an abbreviated hash alone.
UNTAGGED = re.compile(r"^[0-9a-f]{4,}(?:-dirty)?$")
# A CMake bracket comment opens with `#[[` or `#[==[`, and closes with `]]` with
# the same number of `=` in between.
CMAKE_BRACKET = re.compile(r"^#\[(=*)\[")
# A line that opens a Python docstring (or any other bare string statement).
PYTHON_STRING = re.compile(r"^[rRuU]?(\"\"\"|''')")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileCount:
    """The lines of one file, and the category it is counted in."""

    path: PurePosixPath
    category: str
    lines: int
    code: int
    comments: int
    blank: int


@dataclass
class Tally:
    """Files and lines summed over one row of the table."""

    files: int = 0
    lines: int = 0
    code: int = 0
    comments: int = 0
    blank: int = 0

    def add(self, count: FileCount) -> None:
        """Add one file.

        Parameters
        ----------
        count : FileCount
            The file to add.
        """
        self.files += 1
        self.lines += count.lines
        self.code += count.code
        self.comments += count.comments
        self.blank += count.blank

    def merge(self, other: Tally) -> None:
        """Add another tally, for a subtotal.

        Parameters
        ----------
        other : Tally
            The tally to add.
        """
        self.files += other.files
        self.lines += other.lines
        self.code += other.code
        self.comments += other.comments
        self.blank += other.blank


@dataclass
class Snapshot:
    """One set of counted files, and what they were taken from."""

    rev: str | None  # as the user wrote it; None for the working tree
    commit: str | None  # None only in a repository with no commits yet
    version: str
    counts: list[FileCount] = field(default_factory=list)


@dataclass
class Row:
    """One row of the summary table, as every output format sees it."""

    name: str
    kind: str  # "group", "code" (the code subtotal) or "total"
    files: int
    lines: int
    code: int
    comments: int
    blank: int
    share: float | None  # of every line counted
    code_share: float | None  # of the code lines; None where not shown
    change: int | None  # lines since --compare; None without it


@dataclass
class Report:
    """Everything the output formats print."""

    current: Snapshot
    baseline: Snapshot | None
    by: str
    specs: list[str]
    untracked: bool
    code_only: bool
    breakdown: bool
    show_code_share: bool
    rows: list[Row]
    files: list[FileCount]
    largest: list[FileCount]


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def run_git(
    root: Path, args: list[str], stdin: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run git inside the repository, capturing its output.

    Parameters
    ----------
    root : Path
        Any directory inside the working tree.
    args : list of str
        The git subcommand and its arguments.
    stdin : bytes, optional
        Fed to git's standard input.

    Returns
    -------
    subprocess.CompletedProcess of bytes
        The finished process, whatever its exit status. Exits when git itself
        is not installed.
    """
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], input=stdin, capture_output=True
        )
    except FileNotFoundError:
        sys.exit("git is not on PATH -- this counts the files git tracks")


def git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    """Run git for output that must exist.

    Parameters
    ----------
    root : Path
        Any directory inside the working tree.
    *args : str
        The git subcommand and its arguments.
    stdin : bytes, optional
        Fed to git's standard input.

    Returns
    -------
    bytes
        Standard output. Exits with git's own message if git failed.
    """
    result = run_git(root, list(args), stdin)
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip()
        sys.exit(f"git {args[0]} failed: {message}")
    return result.stdout


def git_answer(root: Path, *args: str) -> str | None:
    """Run git for a one-line answer that may legitimately not exist.

    Parameters
    ----------
    root : Path
        Any directory inside the working tree.
    *args : str
        The git subcommand and its arguments.

    Returns
    -------
    str or None
        The answer, stripped; None if git failed or printed nothing.
    """
    result = run_git(root, list(args))
    answer = result.stdout.decode(errors="replace").strip()
    return answer if result.returncode == 0 and answer else None


def repo_root() -> Path:
    """Locate the top of the working tree this script belongs to.

    Returns
    -------
    Path
        The repository root. Exits when the script is not inside a checkout.
    """
    here = Path(__file__).resolve().parent
    top = git_answer(here, "rev-parse", "--show-toplevel")
    if top is None:
        sys.exit(f"{here} is not inside a git checkout")
    return Path(top)


def pathspecs(root: Path, paths: list[str]) -> list[str]:
    """Turn paths given on the command line into root-relative pathspecs.

    Every git command here runs at the repository root, while the user typed
    the paths relative to wherever they are standing.

    Parameters
    ----------
    root : Path
        The repository root.
    paths : list of str
        Paths as given, relative to the current directory or absolute.

    Returns
    -------
    list of str
        The same paths relative to `root`. Exits on a path outside it.
    """
    specs = []
    for given in paths:
        absolute = (Path.cwd() / given).resolve()
        try:
            specs.append(absolute.relative_to(root.resolve()).as_posix())
        except ValueError:
            sys.exit(f"{given} is outside the repository at {root}")
    return specs


def describe(root: Path, commit: str | None) -> str:
    """Derive the version, exactly as cmake/GitVersion.cmake does.

    Parameters
    ----------
    root : Path
        The repository root.
    commit : str or None
        A commit to describe, or None for the working tree -- the only case in
        which ``--dirty`` means anything.

    Returns
    -------
    str
        The ``git describe --tags --always`` output, or ``unknown``.
    """
    if commit is None:
        version = git_answer(root, "describe", "--tags", "--dirty", "--always")
    else:
        version = git_answer(root, "describe", "--tags", "--always", commit)
    return version or "unknown"


def version_note(version: str) -> str:
    """Spell out what a ``git describe`` version says.

    Parameters
    ----------
    version : str
        As returned by `describe`.

    Returns
    -------
    str
        For example ``32 commits after tag v0.0.2, plus uncommitted changes``.
    """
    if version == "unknown":
        return "git could not describe it"
    described = DESCRIBED.match(version)
    if described:
        ahead = int(described["ahead"])
        plural = "" if ahead == 1 else "s"
        note = f"{ahead} commit{plural} after tag {described['tag']}"
    elif UNTAGGED.match(version):
        note = "no tag reachable"
    else:
        note = f"at tag {version.removesuffix('-dirty')}"
    return note + (", plus uncommitted changes" if version.endswith("-dirty") else "")


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


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


def comment_style(category: str, path: PurePosixPath) -> str | None:
    """Pick the comment syntax a file is read with.

    Parameters
    ----------
    category : str
        As returned by `classify`.
    path : PurePosixPath
        The file, for telling JSON, which has no comments, from the YAML and
        TOML that share its category.

    Returns
    -------
    str or None
        ``"c"``, ``"python"``, ``"cmake"``, ``"vim"`` or ``"hash"``; None where
        every non-blank line is content.
    """
    if category == "C++":
        return "c"
    if category == "Python":
        return "python"
    if category == "CMake":
        return "cmake"
    if category == "Vim script":
        return "vim"
    if category in {"Bash", "Dockerfile", "GitHub Actions"}:
        return "hash"
    if category == "Configuration" and path.suffix != ".json":
        return "hash"
    return None


def comment_line(line: str, style: str | None) -> tuple[bool, str | None]:
    """Decide whether one stripped, non-blank line is a comment.

    Parameters
    ----------
    line : str
        The line, with surrounding whitespace removed.
    style : str or None
        As returned by `comment_style`.

    Returns
    -------
    tuple of (bool, str or None)
        Whether the line is a comment, and -- when it opens a multi-line
        comment it does not also close -- the text that will close it.
    """
    if style == "c":
        if line.startswith("/*"):
            return True, None if "*/" in line[2:] else "*/"
        return line.startswith("//"), None
    if style == "vim":
        return line.startswith('"'), None
    if style == "cmake":
        bracket = CMAKE_BRACKET.match(line)
        if bracket:
            closer = f"]{bracket.group(1)}]"
            return True, None if closer in line[bracket.end() :] else closer
    if style == "python":
        string = PYTHON_STRING.match(line)
        if string:
            quote = string.group(1)
            return True, None if quote in line[string.end() :] else quote
    if style in {"hash", "cmake", "python"}:
        return line.startswith("#"), None
    return False, None


def count_file(path: PurePosixPath, data: bytes) -> FileCount:
    """Classify one file and count its lines.

    Parameters
    ----------
    path : PurePosixPath
        The file, relative to the repository root.
    data : bytes
        Its whole contents.

    Returns
    -------
    FileCount
        Its category and line counts; all zero for a binary file, meaning one
        with a NUL byte near the start.
    """
    category = classify(path, data.split(b"\n", 1)[0].rstrip())
    if b"\0" in data[:8192]:
        return FileCount(path, category, 0, 0, 0, 0)

    lines = data.decode(errors="replace").split("\n")
    if lines[-1] == "":  # the newline that ends the last line starts no line
        lines.pop()

    style = comment_style(category, path)
    comments = blank = 0
    closer: str | None = None  # set while inside a multi-line comment
    for raw in lines:
        line = raw.strip()
        if closer is not None:
            comments += 1
            if closer in line:
                closer = None
        elif not line:
            blank += 1
        else:
            is_comment, closer = comment_line(line, style)
            comments += is_comment
    code = len(lines) - comments - blank
    return FileCount(path, category, len(lines), code, comments, blank)


def working_tree_counts(
    root: Path, specs: list[str], untracked: bool
) -> list[FileCount]:
    """Count the working-tree files git knows about.

    Parameters
    ----------
    root : Path
        The repository root.
    specs : list of str
        Pathspecs to restrict the count to; empty for everything.
    untracked : bool
        Also count files that are neither tracked nor ignored.

    Returns
    -------
    list of FileCount
        One per regular file, in path order. Symlinks are skipped, as they
        are in `commit_counts`, and so is a tracked file deleted from disk.
    """
    args = ["ls-files", "-z", "--cached"]
    if untracked:
        args += ["--others", "--exclude-standard"]
    listing = git(root, *args, "--", *specs).decode(errors="surrogateescape")
    counts = []
    for name in sorted({name for name in listing.split("\0") if name}):
        path = root / name
        if path.is_file() and not path.is_symlink():
            counts.append(count_file(PurePosixPath(name), path.read_bytes()))
    return counts


def commit_counts(root: Path, commit: str, specs: list[str]) -> list[FileCount]:
    """Count the files of a commit as committed, without checking it out.

    Parameters
    ----------
    root : Path
        The repository root.
    commit : str
        A full commit hash.
    specs : list of str
        Pathspecs to restrict the count to; empty for everything.

    Returns
    -------
    list of FileCount
        One per blob, in path order. Submodules (commits, not blobs) and
        symlinks (whose blob is only the target path) are skipped.
    """
    listing = git(root, "ls-tree", "-r", "-z", commit, "--", *specs)
    entries: list[tuple[PurePosixPath, bytes]] = []
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        mode, kind, oid = meta.split(b" ")
        if kind == b"blob" and mode != b"120000":
            path = PurePosixPath(name.decode(errors="surrogateescape"))
            entries.append((path, oid))
    if not entries:
        return []

    # One `cat-file --batch` for every blob rather than one process per file.
    # Each object comes back as "<oid> blob <size>\n<contents>\n".
    request = b"\n".join(oid for _, oid in entries) + b"\n"
    blobs = git(root, "cat-file", "--batch", stdin=request)
    counts = []
    offset = 0
    for path, _ in entries:
        header_end = blobs.index(b"\n", offset)
        size = int(blobs[offset:header_end].split()[2])
        start = header_end + 1
        counts.append(count_file(path, blobs[start : start + size]))
        offset = start + size + 1
    return counts


def take_snapshot(
    root: Path, rev: str | None, specs: list[str], untracked: bool
) -> Snapshot:
    """Count either the working tree or one revision.

    Parameters
    ----------
    root : Path
        The repository root.
    rev : str or None
        A commit, tag or branch; None for the working tree.
    specs : list of str
        Pathspecs to restrict the count to; empty for everything.
    untracked : bool
        With the working tree, also count untracked files.

    Returns
    -------
    Snapshot
        The counts, with the commit and version they describe. Exits when
        `rev` names no commit.
    """
    if rev is None:
        head = git_answer(root, "rev-parse", "--verify", "--quiet", "HEAD")
        counts = working_tree_counts(root, specs, untracked)
        return Snapshot(None, head, describe(root, None), counts)
    commit = git_answer(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if commit is None:
        sys.exit(f"'{rev}' does not name a commit")
    return Snapshot(
        rev, commit, describe(root, commit), commit_counts(root, commit, specs)
    )


# ---------------------------------------------------------------------------
# Summarising
# ---------------------------------------------------------------------------


def group_name(count: FileCount, by: str, depth: int) -> str:
    """Name the table row a file is summed into.

    Parameters
    ----------
    count : FileCount
        The file.
    by : str
        ``"language"`` or ``"directory"``.
    depth : int
        With ``"directory"``, how many leading directories to keep.

    Returns
    -------
    str
        The category, or a directory such as ``scripts/ci/``; files at the
        top of the repository are ``(top level)``.
    """
    if by == "language":
        return count.category
    directories = count.path.parts[:-1][:depth]
    return "/".join(directories) + "/" if directories else "(top level)"


def tally(counts: list[FileCount], by: str, depth: int) -> dict[str, Tally]:
    """Sum files into table rows.

    Parameters
    ----------
    counts : list of FileCount
        The files.
    by : str
        ``"language"`` or ``"directory"``.
    depth : int
        With ``"directory"``, how many leading directories to keep.

    Returns
    -------
    dict of str to Tally
        One tally per row name; names with no files are absent.
    """
    tallies: dict[str, Tally] = {}
    for count in counts:
        tallies.setdefault(group_name(count, by, depth), Tally()).add(count)
    return tallies


def order_names(
    names: set[str], tallies: dict[str, Tally], by: str, sort: str
) -> list[str]:
    """Put the table rows in order.

    Parameters
    ----------
    names : set of str
        Every row, including rows present only in the ``--compare`` baseline.
    tallies : dict of str to Tally
        The current counts, which the order is taken from.
    by : str
        ``"language"`` keeps Documentation and Other last whatever the sort.
    sort : str
        ``"lines"`` or ``"files"`` (largest first), or ``"name"``.

    Returns
    -------
    list of str
        The row names, in print order.
    """
    empty = Tally()
    if sort == "name":
        ordered = sorted(names, key=str.lower)
    elif sort == "files":
        ordered = sorted(names, key=lambda n: (-tallies.get(n, empty).files, n))
    else:
        ordered = sorted(names, key=lambda n: (-tallies.get(n, empty).lines, n))
    if by != "language":
        return ordered
    return [n for n in ordered if n not in NOT_CODE] + [
        n for n in NOT_CODE if n in names
    ]


def summed(tallies: dict[str, Tally], names: list[str]) -> Tally:
    """Add up some rows.

    Parameters
    ----------
    tallies : dict of str to Tally
        The rows.
    names : list of str
        Which of them to add; names absent from `tallies` count as empty.

    Returns
    -------
    Tally
        A new tally holding the sum.
    """
    total = Tally()
    for name in names:
        if name in tallies:
            total.merge(tallies[name])
    return total


def make_row(
    name: str,
    kind: str,
    current: Tally,
    total: int,
    code_total: int | None,
    baseline: Tally | None,
) -> Row:
    """Build one table row from its tally.

    Parameters
    ----------
    name : str
        What the row is called.
    kind : str
        ``"group"``, ``"code"`` or ``"total"``.
    current : Tally
        The counts for the row.
    total : int
        Every line counted, for the share.
    code_total : int or None
        Every code line counted, for the code share; None to leave it out.
    baseline : Tally or None
        The same row in the ``--compare`` baseline; None without one.

    Returns
    -------
    Row
        The row.
    """
    return Row(
        name=name,
        kind=kind,
        files=current.files,
        lines=current.lines,
        code=current.code,
        comments=current.comments,
        blank=current.blank,
        share=current.lines / total if total else None,
        code_share=current.lines / code_total if code_total else None,
        change=None if baseline is None else current.lines - baseline.lines,
    )


def build_rows(
    tallies: dict[str, Tally],
    baseline: dict[str, Tally] | None,
    names: list[str],
    show_code_share: bool,
) -> list[Row]:
    """Build the whole summary table: the rows, the code subtotal, the total.

    Parameters
    ----------
    tallies : dict of str to Tally
        The current counts per row.
    baseline : dict of str to Tally or None
        The ``--compare`` counts per row; None without one.
    names : list of str
        The rows, in print order.
    show_code_share : bool
        Whether to compute the "of code" share and the code subtotal.

    Returns
    -------
    list of Row
        The group rows, then the subtotal if any, then the total.
    """
    empty = Tally()
    total = summed(tallies, names)
    code_names = [n for n in names if n not in NOT_CODE] if show_code_share else []
    code = summed(tallies, code_names)

    rows = []
    for name in names:
        in_code = name in code_names
        rows.append(
            make_row(
                name,
                "group",
                tallies.get(name, empty),
                total.lines,
                code.lines if in_code else None,
                None if baseline is None else baseline.get(name, empty),
            )
        )
    if show_code_share:
        base_code = None if baseline is None else summed(baseline, code_names)
        rows.append(
            make_row("All code", "code", code, total.lines, code.lines, base_code)
        )
    base_total = None if baseline is None else summed(baseline, names)
    rows.append(make_row("Total", "total", total, total.lines, None, base_total))
    return rows


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def number(value: int) -> str:
    """Format a count with thousands separators."""
    return f"{value:,}"


def percent(value: float | None) -> str:
    """Format a share as a percentage, or nothing for no share."""
    return "" if value is None else f"{100 * value:.1f}%"


def signed(value: int | None) -> str:
    """Format a change with its sign, or nothing for no change column."""
    return "" if value is None else f"{value:+,}"


def describe_source(report: Report) -> str:
    """Say in words what was counted.

    Parameters
    ----------
    report : Report
        The report.

    Returns
    -------
    str
        For example ``the working tree, tracked files only, under src``.
    """
    if report.current.rev is None:
        which = " and untracked ones" if report.untracked else " only"
        text = f"the working tree, tracked files{which}"
    else:
        text = f"{report.current.rev}, as committed"
    if report.specs:
        text += ", under " + ", ".join(report.specs)
    if report.code_only:
        text += ", code only"
    return text


def summary_table(report: Report) -> tuple[list[str], list[list[str]]]:
    """Turn the summary rows into header and body cells.

    Parameters
    ----------
    report : Report
        The report.

    Returns
    -------
    tuple of (list of str, list of list of str)
        The column headers, and one list of cells per row.
    """
    headers = ["Language" if report.by == "language" else "Directory"]
    headers += ["Files", "Lines"]
    if report.breakdown:
        headers += ["Code", "Comments", "Blank"]
    headers.append("Of all")
    if report.show_code_share:
        headers.append("Of code")
    if report.baseline is not None:
        headers.append("Change")

    body = []
    for row in report.rows:
        cells = [row.name, number(row.files), number(row.lines)]
        if report.breakdown:
            cells += [number(row.code), number(row.comments), number(row.blank)]
        cells.append(percent(row.share))
        if report.show_code_share:
            cells.append(percent(row.code_share))
        if report.baseline is not None:
            cells.append(signed(row.change))
        body.append(cells)
    return headers, body


def file_table(
    report: Report, counts: list[FileCount]
) -> tuple[list[str], list[list[str]]]:
    """Turn a list of files into header and body cells.

    Parameters
    ----------
    report : Report
        The report, for whether to show the breakdown.
    counts : list of FileCount
        The files, in print order.

    Returns
    -------
    tuple of (list of str, list of list of str)
        The column headers, and one list of cells per file; the path is last.
    """
    headers = ["Language", "Lines"]
    if report.breakdown:
        headers += ["Code", "Comments", "Blank"]
    headers.append("Path")
    body = []
    for count in counts:
        cells = [count.category, number(count.lines)]
        if report.breakdown:
            cells += [number(count.code), number(count.comments), number(count.blank)]
        cells.append(str(count.path))
        body.append(cells)
    return headers, body


def pad(cells: list[str], widths: list[int], left: set[int]) -> str:
    """Join cells into one aligned line.

    Parameters
    ----------
    cells : list of str
        The cells.
    widths : list of int
        The width of each column.
    left : set of int
        The columns aligned left; the rest, being numbers, align right.

    Returns
    -------
    str
        The line.
    """
    return "  ".join(
        cell.ljust(width) if index in left else cell.rjust(width)
        for index, (cell, width) in enumerate(zip(cells, widths, strict=True))
    )


def aligned(
    headers: list[str],
    body: list[list[str]],
    left: set[int],
    rule_before: int | None = None,
    bars: list[str] | None = None,
) -> list[str]:
    """Lay cells out as a plain-text table.

    Parameters
    ----------
    headers : list of str
        The column headers.
    body : list of list of str
        One list of cells per row.
    left : set of int
        The left-aligned columns.
    rule_before : int, optional
        Draw a rule above this row, to set the totals apart.
    bars : list of str, optional
        Text appended to each row, past the last column.

    Returns
    -------
    list of str
        The table, one string per line.
    """
    widths = [
        max([len(header), *(len(cells[index]) for cells in body)])
        for index, header in enumerate(headers)
    ]
    rule = "-" * (sum(widths) + 2 * (len(widths) - 1))
    lines = [pad(headers, widths, left), rule]
    for index, cells in enumerate(body):
        if index == rule_before:
            lines.append(rule)
        line = pad(cells, widths, left)
        if bars and bars[index]:
            line += "  " + bars[index]
        lines.append(line)
    return lines


def markdown_row(cells: list[str]) -> str:
    """Join cells into one Markdown table row, escaping any pipe."""
    return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"


def markdown(
    headers: list[str],
    body: list[list[str]],
    left: set[int],
    bold_from: int | None = None,
) -> list[str]:
    """Lay cells out as a GitHub-flavoured Markdown table.

    Parameters
    ----------
    headers : list of str
        The column headers.
    body : list of list of str
        One list of cells per row.
    left : set of int
        The left-aligned columns.
    bold_from : int, optional
        Set this row and every one after it in bold, for the totals.

    Returns
    -------
    list of str
        The table, one string per line.
    """
    rule = ["---" if index in left else "---:" for index in range(len(headers))]
    lines = [markdown_row(headers), markdown_row(rule)]
    for index, cells in enumerate(body):
        if bold_from is not None and index >= bold_from:
            cells = [f"**{cell}**" if cell else cell for cell in cells]
        lines.append(markdown_row(cells))
    return lines


def file_sections(report: Report) -> list[tuple[str, list[FileCount]]]:
    """List the optional per-file sections that were asked for."""
    sections = [("Largest files", report.largest), ("Files", report.files)]
    return [(title, counts) for title, counts in sections if counts]


def render_text(report: Report, banner: bool) -> list[str]:
    """Render the report for a terminal.

    Parameters
    ----------
    report : Report
        The report.
    banner : bool
        Start with the ``Altx-cpp`` banner.

    Returns
    -------
    list of str
        The output, one string per line.
    """
    current, baseline = report.current, report.baseline
    lines = [BANNER.strip("\n"), ""] if banner else []
    lines += [
        f"version   {current.version}  ({version_note(current.version)})",
        f"commit    {current.commit or '(no commits yet)'}",
        f"counted   {describe_source(report)}",
    ]
    if baseline is not None:
        lines.append(
            f"compared  {baseline.rev}: {baseline.version} at {baseline.commit}"
        )
    lines.append("")

    headers, body = summary_table(report)
    first_total = next(i for i, row in enumerate(report.rows) if row.kind != "group")
    bars = [
        "#" * round(BAR_WIDTH * row.share) if row.kind == "group" and row.share else ""
        for row in report.rows
    ]
    lines += aligned(headers, body, {0}, first_total, bars)

    for title, counts in file_sections(report):
        headers, body = file_table(report, counts)
        lines += ["", title, *aligned(headers, body, {0, len(headers) - 1})]
    return lines


def render_markdown(report: Report) -> list[str]:
    """Render the report as Markdown, to paste into a pull request or an issue.

    Parameters
    ----------
    report : Report
        The report.

    Returns
    -------
    list of str
        The output, one string per line.
    """
    current, baseline = report.current, report.baseline
    lines = [
        f"**Altx-cpp** `{current.version}` ({version_note(current.version)})",
        "",
        f"- commit: `{current.commit or '(no commits yet)'}`",
        f"- counted: {describe_source(report)}",
    ]
    if baseline is not None:
        lines.append(
            f"- compared with `{baseline.rev}`: "
            f"`{baseline.version}` at `{baseline.commit}`"
        )
    lines.append("")

    headers, body = summary_table(report)
    first_total = next(i for i, row in enumerate(report.rows) if row.kind != "group")
    lines += markdown(headers, body, {0}, first_total)

    for title, counts in file_sections(report):
        headers, body = file_table(report, counts)
        for cells in body:
            cells[-1] = f"`{cells[-1]}`"
        lines += [
            "",
            f"### {title}",
            "",
            *markdown(headers, body, {0, len(headers) - 1}),
        ]
    return lines


def snapshot_json(snapshot: Snapshot) -> dict[str, object]:
    """Describe a snapshot's identity for the JSON output."""
    return {
        "revision": snapshot.rev,
        "commit": snapshot.commit,
        "version": snapshot.version,
        "dirty": snapshot.version.endswith("-dirty"),
    }


def file_json(count: FileCount) -> dict[str, object]:
    """Describe one file for the JSON output."""
    return {**asdict(count), "path": str(count.path)}


def render_json(report: Report) -> str:
    """Render the report as JSON, with every number the other formats show.

    Parameters
    ----------
    report : Report
        The report.

    Returns
    -------
    str
        The JSON document. Shares are fractions, not percentages, and every
        row carries the breakdown whether or not ``--breakdown`` was given.
    """
    document = {
        "project": "Altx-cpp",
        "counted": {
            **snapshot_json(report.current),
            "untracked": report.untracked,
            "code_only": report.code_only,
            "paths": report.specs,
        },
        "compared": None if report.baseline is None else snapshot_json(report.baseline),
        "group_by": report.by,
        "rows": [asdict(row) for row in report.rows],
        "largest": [file_json(count) for count in report.largest],
        "files": [file_json(count) for count in report.files],
    }
    return json.dumps(document, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def positive(text: str) -> int:
    """Parse a command-line count that must be at least 1."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{value} is less than 1")
    return value


def parse_args() -> argparse.Namespace:
    """Read and cross-check the command line.

    Returns
    -------
    argparse.Namespace
        The arguments. Exits with a usage message on a contradictory pair.
    """
    parser = argparse.ArgumentParser(
        description="Count the lines in the repository, by language or by directory.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="count only files under these paths (default: the whole repository)",
    )

    what = parser.add_argument_group("what to count")
    what.add_argument(
        "--rev",
        metavar="REV",
        help="count a commit, tag or branch as committed, not the working tree",
    )
    what.add_argument(
        "--untracked",
        action="store_true",
        help="also count files that are neither tracked nor ignored",
    )
    what.add_argument(
        "--code-only",
        action="store_true",
        help="leave out Documentation and Other",
    )

    how = parser.add_argument_group("how to summarise")
    how.add_argument(
        "--by",
        choices=("language", "directory"),
        default="language",
        help="one row per language (default) or per directory",
    )
    how.add_argument(
        "--depth",
        type=positive,
        metavar="N",
        help="with --by directory: directory levels to keep (default: 1)",
    )
    how.add_argument(
        "--breakdown",
        action="store_true",
        help="split lines into code, comments and blank lines",
    )
    how.add_argument(
        "--compare",
        metavar="REV",
        help="add a column with each row's change in lines since REV",
    )
    how.add_argument(
        "--sort",
        choices=("lines", "files", "name"),
        default="lines",
        help="row order (default: most lines first)",
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "--format",
        choices=("table", "markdown", "json"),
        default="table",
        help="plain text (default), a Markdown table, or JSON",
    )
    output.add_argument(
        "--no-banner",
        action="store_true",
        help="leave out the Altx-cpp banner (only the table format has one)",
    )
    output.add_argument(
        "--largest",
        type=positive,
        metavar="N",
        help="also list the N longest files",
    )
    output.add_argument(
        "--files",
        action="store_true",
        help="also list every file with its category and line count",
    )

    args = parser.parse_args()
    if args.untracked and args.rev:
        parser.error("--untracked applies to the working tree, --rev to a commit")
    if args.depth is not None and args.by != "directory":
        parser.error("--depth needs --by directory")
    return args


def main() -> None:
    """Count, summarise and print."""
    args = parse_args()
    root = repo_root()
    specs = pathspecs(root, args.paths)
    depth = args.depth or 1

    current = take_snapshot(root, args.rev, specs, args.untracked)
    baseline = None
    if args.compare:
        baseline = take_snapshot(root, args.compare, specs, untracked=False)

    def selected(counts: list[FileCount]) -> list[FileCount]:
        return [c for c in counts if not args.code_only or c.category not in NOT_CODE]

    counts = selected(current.counts)
    tallies = tally(counts, args.by, depth)
    base_tallies = None
    if baseline is not None:
        base_tallies = tally(selected(baseline.counts), args.by, depth)
    names = order_names(
        set(tallies) | set(base_tallies or {}), tallies, args.by, args.sort
    )
    # Per language only: a directory mixes code and documentation, so a share of
    # code per directory would be a share of something the row does not count.
    show_code_share = args.by == "language" and not args.code_only

    report = Report(
        current=current,
        baseline=baseline,
        by=args.by,
        specs=specs,
        untracked=args.untracked,
        code_only=args.code_only,
        breakdown=args.breakdown,
        show_code_share=show_code_share,
        rows=build_rows(tallies, base_tallies, names, show_code_share),
        files=sorted(counts, key=lambda c: (c.category, str(c.path)))
        if args.files
        else [],
        largest=sorted(counts, key=lambda c: (-c.lines, str(c.path)))[: args.largest]
        if args.largest
        else [],
    )

    if args.format == "json":
        print(render_json(report))
        return
    if args.format == "markdown":
        lines = render_markdown(report)
    else:
        lines = render_text(report, banner=not args.no_banner)
    # An empty bar or share column would otherwise leave trailing blanks, which
    # show up the moment the output is pasted anywhere that marks them.
    print("\n".join(line.rstrip() for line in lines))


if __name__ == "__main__":
    main()
