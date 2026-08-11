#!/usr/bin/env python3
"""Resolve ``docker/images.yaml`` into tags, build arguments and a CI matrix.

The one idea worth stating up front: **every image reference is a pure function
of the working tree**, so no job ever has to tell another job what an image is
called. A workflow job that needs ``dev-cpu`` computes its tag itself and gets
the same answer as the job that built it. Only *ordering* has to flow through
``needs:``; no data does. That sidesteps the fact that outputs of a matrix job
are shared by every matrix entry and overwrite each other.

The hash of an image covers

* the Dockerfile, byte for byte -- which is also where the pinned base-image
  digests, the apt package list and the pinned uv/vim-plug versions live, so
  they need no separate handling;
* every file matched by ``inputs``;
* the static ``build_args``;
* the hash of every parent, recursively.

Commands
--------
``plan``        the whole resolved matrix as JSON (what the CI prepare job runs)
``ref NAME``    the immutable ``hash-`` reference of one image
``build-args``  ``--build-arg`` flags for one image, ready to paste into docker

Run it anywhere: ``python3 scripts/ci/images.py plan``, or, if PyYAML is not
installed, ``uv run --with pyyaml scripts/ci/images.py plan``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment problem, not a code path
    sys.exit(
        "PyYAML is required.\n"
        "  uv run --with pyyaml scripts/ci/images.py ...\n"
        "  python3 -m pip install pyyaml\n"
        "  apt-get install python3-yaml"
    )

# Bump when the hashing rule changes in a way that must invalidate every
# existing hash- tag. Without it, a change to this file silently keeps every
# image at its old hash and nothing rebuilds.
HASH_SCHEMA = "altx-image-hash-v1"

HASH_LENGTH = 12  # hex characters kept in the tag

# The workflow defines one job per tier, and a job cannot be generated. Adding a
# fourth layer to the chain is therefore a workflow edit as well as a config
# edit, and this is where that is said out loud rather than discovered.
MAX_TIERS = 2


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path(__file__).resolve().parents[2]


def load_config(root: Path) -> dict:
    with (root / "docker" / "images.yaml").open() as handle:
        config = yaml.safe_load(handle)
    if config.get("schema_version") != 1:
        sys.exit(f"unsupported schema_version {config.get('schema_version')!r}")
    return config


def namespace(config: dict) -> str:
    """``ghcr.io/<owner>/<repo>`` -- lowercased, since GHCR rejects uppercase."""
    repository = os.environ.get("GITHUB_REPOSITORY") or config["repository"]
    return f"{config['registry']}/{repository}".lower()


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expand(root: Path, pattern: str) -> list[Path]:
    """Expand one glob into a sorted list of files.

    Fails when a pattern matches nothing. A typo in ``inputs`` would otherwise
    freeze the image's hash forever: the image would never be rebuilt, and the
    only symptom would be CI quietly using a stale image.

    A trailing ``/**`` is rewritten to ``/**/*``: before Python 3.13, ``**``
    matches directories only, so ``docker/vim/**`` would hash nothing at all on
    the system interpreter while working under a newer one.
    """
    if pattern.endswith("/**"):
        pattern += "/*"
    matches = sorted(p for p in root.glob(pattern) if p.is_file())
    if not matches:
        sys.exit(f"pattern '{pattern}' matched no file under {root}")
    return matches


def content_hash(root: Path, config: dict, name: str, cache: dict[str, str]) -> str:
    if name in cache:
        return cache[name]

    spec = config["images"].get(name)
    if spec is None:
        sys.exit(f"unknown image '{name}'")

    digest = hashlib.sha256()
    digest.update(f"{HASH_SCHEMA}\0{name}\0".encode())

    # Parents first, so that a base-image change propagates to every descendant
    # without anything else in the descendant having to change. Recursion is
    # safe because the graph is validated to be acyclic by tiers() below.
    for arg, parent in sorted(spec.get("parents", {}).items()):
        digest.update(f"parent\0{arg}\0{content_hash(root, config, parent, cache)}\0".encode())

    patterns = [spec["dockerfile"], *spec.get("inputs", [])]
    for pattern in patterns:
        for path in expand(root, pattern):
            rel = path.relative_to(root).as_posix()
            digest.update(f"file\0{rel}\0{hash_file(path)}\0".encode())

    for key, value in sorted(spec.get("build_args", {}).items()):
        digest.update(f"arg\0{key}\0{value}\0".encode())

    cache[name] = digest.hexdigest()[:HASH_LENGTH]
    return cache[name]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def tiers(config: dict, names: list[str]) -> list[list[str]]:
    """Group images into build tiers by longest path from a root.

    Tier n depends only on tiers < n, so one CI job per tier, with ``needs:``
    between them, reproduces the layering with no per-image job dependencies --
    which GitHub Actions cannot express inside a matrix anyway.
    """
    depth: dict[str, int] = {}

    def resolve(name: str, seen: tuple[str, ...] = ()) -> int:
        if name in seen:
            sys.exit(f"cycle in parents: {' -> '.join((*seen, name))}")
        if name not in depth:
            parents = config["images"][name].get("parents", {}).values()
            depth[name] = 1 + max((resolve(p, (*seen, name)) for p in parents), default=-1)
        return depth[name]

    for name in names:
        resolve(name)
        for parent in config["images"][name].get("parents", {}).values():
            if not config["images"][parent].get("enabled", False):
                sys.exit(f"'{name}' is enabled but its parent '{parent}' is not")

    return [
        sorted(n for n in names if depth[n] == tier)
        for tier in range(max(depth.values(), default=-1) + 1)
    ]


def resolve(root: Path, config: dict) -> dict:
    prefix = namespace(config)
    enabled = [n for n, s in config["images"].items() if s.get("enabled", False)]
    cache: dict[str, str] = {}

    images = {}
    for name in enabled:
        spec = config["images"][name]
        digest = content_hash(root, config, name, cache)
        repo = f"{prefix}/{name}"

        # Parents are referenced by their own hash tag, never by `edge` or
        # `latest`. A moving tag here is the drift the layered build exists to
        # prevent: it would make a rebuild of a child pick up a base nobody
        # asked for.
        build_args = dict(spec.get("build_args", {}))
        for arg, parent in spec.get("parents", {}).items():
            build_args[arg] = f"{prefix}/{parent}:hash-{content_hash(root, config, parent, cache)}"

        trivy = {**config["defaults"]["trivy"], **spec.get("trivy", {})}

        images[name] = {
            "name": name,
            "repo": repo,
            "hash": digest,
            "tag": f"hash-{digest}",
            "ref": f"{repo}:hash-{digest}",
            "cache_ref": f"{prefix}/buildcache:{name}",
            "dockerfile": spec["dockerfile"],
            "context": spec.get("context", config["defaults"]["context"]),
            "platforms": ",".join(spec.get("platforms", config["defaults"]["platforms"])),
            "cache": spec.get("cache", config["defaults"]["cache"]),
            "moving_tag": spec.get("moving_tag", "edge"),
            "build_args": "\n".join(f"{k}={v}" for k, v in sorted(build_args.items())),
            "trivy_severity": ",".join(trivy["severity"]),
            "trivy_exit_code": "1" if trivy["fail"] else "0",
        }

    return {
        "images": images,
        "tiers": [[images[n] for n in tier] for tier in tiers(config, enabled)],
        "retention": config["retention"],
        "namespace": prefix,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def emit_github_output(plan: dict) -> None:
    """Write the plan to $GITHUB_OUTPUT as one JSON value per tier.

    One value per tier rather than one per image, because a workflow can only
    read outputs it names statically -- and the tier list is the thing a
    ``strategy.matrix`` consumes directly.

    All ``MAX_TIERS`` outputs are always written, empty ones as ``[]``. An
    unset output would arrive in the workflow as the empty string, which is not
    valid JSON for ``fromJson``, so the tier job's guard would have to know the
    difference between "no images here" and "no such tier".
    """
    if len(plan["tiers"]) > MAX_TIERS:
        sys.exit(
            f"the image graph is {len(plan['tiers'])} layers deep but the workflow "
            f"defines {MAX_TIERS} tier jobs -- add one in .github/workflows/"
        )
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    tiers_json = [json.dumps(tier, separators=(",", ":")) for tier in plan["tiers"]]
    tiers_json += ["[]"] * (MAX_TIERS - len(tiers_json))
    with open(path, "a") as handle:
        handle.write(f"images={json.dumps(plan['images'], separators=(',', ':'))}\n")
        handle.write(f"retention={json.dumps(plan['retention'], separators=(',', ':'))}\n")
        for index, tier in enumerate(tiers_json):
            handle.write(f"tier{index}={tier}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan", help="print the resolved matrix as JSON")
    plan_parser.add_argument("--github-output", action="store_true",
                             help="also append the plan to $GITHUB_OUTPUT")

    ref_parser = sub.add_parser("ref", help="print one image's immutable reference")
    ref_parser.add_argument("image")

    args_parser = sub.add_parser("build-args", help="print docker --build-arg flags")
    args_parser.add_argument("image")

    args = parser.parse_args()
    root = repo_root()
    plan = resolve(root, load_config(root))

    if args.command == "plan":
        print(json.dumps(plan, indent=2))
        if args.github_output:
            emit_github_output(plan)
        return

    image = plan["images"].get(args.image)
    if image is None:
        sys.exit(f"'{args.image}' is not an enabled image "
                 f"(enabled: {', '.join(sorted(plan['images']))})")

    if args.command == "ref":
        print(image["ref"])
    elif args.command == "build-args":
        print(" ".join(f"--build-arg {line}"
                       for line in image["build_args"].splitlines() if line))


if __name__ == "__main__":
    main()
