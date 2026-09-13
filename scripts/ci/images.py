#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml"]
# ///
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
* the ``description``, which becomes the image's
  ``org.opencontainers.image.description`` label -- a label is image content;
* the hash of every parent, recursively.

Two destinations, not one
-------------------------
Every image resolves to **two** GHCR packages, because the two kinds of tag have
different audiences and different lifetimes:

``ghcr.io/<owner>/altx-cpp/<name>``
    Human-facing only: ``edge``, ``latest``, ``vX.Y.Z``. Someone opening the
    packages page sees a handful of meaningful tags per image.

``ghcr.io/<owner>/altx-cpp/buildcache``
    Machine-facing only: ``<name>-hash-<digest>``, ``<name>-sha-<commit>`` and
    the buildx registry cache ``<name>-cache``. One package for every image,
    which is why each tag carries the image name as a prefix -- tags are unique
    per package, so ``hash-abc123`` alone could not tell ``base-cpu`` from
    ``dev-cpu``.

That split is also what makes retention simple: everything in ``buildcache`` is
disposable by construction, and nothing outside it is ever deleted.

Commands
--------
``plan``        the whole resolved matrix as JSON (what the CI prepare job runs)
``ref NAME``    the immutable ``hash-`` reference of one image
``build-args``  ``--build-arg`` flags for one image, ready to paste into docker
``description NAME``  one image's description, disabled images included

Run it anywhere: ``python3 scripts/ci/images.py plan``, or, if PyYAML is not
installed, ``uv run scripts/ci/images.py plan``. uv reads the ``# /// script``
block above (PEP 723), installs PyYAML into a cached environment, and treats
this file as a standalone script -- rather than reading the repository's
``pyproject.toml``, which configures the linters and declares nothing
installable, as a project, and materialising a ``.venv`` nobody asked for.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - environment problem, not a code path
    sys.exit(
        "PyYAML is required.\n"
        "  uv run scripts/ci/images.py ...\n"
        "  python3 -m pip install pyyaml\n"
        "  apt-get install python3-yaml"
    )

# Bump when the hashing rule changes in a way that must invalidate every
# existing hash- tag. Without it, a change to this file silently keeps every
# image at its old hash and nothing rebuilds.
#
# Also bumped when the build action starts writing something into every
# manifest that the existing images lack: an image whose hash exists is retagged,
# never rebuilt, so it would otherwise never receive it. v2 was that, for the
# description annotation in .github/actions/build-image/action.yml.
HASH_SCHEMA = "altx-image-hash-v2"

# GHCR's limit on org.opencontainers.image.description. A longer one is not
# rejected at push time; it is simply not shown.
DESCRIPTION_MAX = 512

HASH_LENGTH = 12  # hex characters kept in the tag

# The workflow defines one job per tier, and a job cannot be generated. Adding a
# fourth layer to the chain is therefore a workflow edit as well as a config
# edit, and this is where that is said out loud rather than discovered.
#
# 3 since runtime-cpu was enabled: base -> dev -> runtime. `emit_github_output`
# checks the resolved depth against this and says which file to edit.
MAX_TIERS = 3

# The build argument every Dockerfile turns into its description label. It is a
# build argument rather than a literal LABEL because each Dockerfile builds
# several images; see the end of docker/base.Dockerfile.
DESCRIPTION_ARG = "IMAGE_DESCRIPTION"

# ``images.yaml`` is free-form nested YAML and ``resolve`` builds an equally
# free-form record out of it, so the value type really is Any. Naming the two
# shapes keeps that admission in one place instead of at every signature, and
# gives the keys somewhere to be documented.
Config = dict[str, Any]
Plan = dict[str, Any]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    """Locate the top of the working tree.

    Falls back to walking up from this file when git is unavailable -- inside a
    container that has the sources but not the ``.git`` directory, for instance.

    Returns
    -------
    Path
        The directory holding ``docker/images.yaml``.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path(__file__).resolve().parents[2]


def load_config(root: Path) -> Config:
    """Read and version-check ``docker/images.yaml``.

    Parameters
    ----------
    root : Path
        The top of the working tree, as returned by `repo_root`.

    Returns
    -------
    Config
        The parsed document, guaranteed to declare ``schema_version: 1``.
    """
    with (root / "docker" / "images.yaml").open() as handle:
        config: Config = yaml.safe_load(handle)
    if config.get("schema_version") != 1:
        sys.exit(f"unsupported schema_version {config.get('schema_version')!r}")
    return config


def namespace(config: Config) -> str:
    """Build the ``ghcr.io/<owner>/<repo>`` prefix every package sits under.

    Parameters
    ----------
    config : Config
        The parsed ``images.yaml``.

    Returns
    -------
    str
        The prefix, lowercased -- GHCR rejects uppercase.
    """
    repository = os.environ.get("GITHUB_REPOSITORY") or config["repository"]
    return f"{config['registry']}/{repository}".lower()


def cache_repository(config: Config) -> str:
    """Name the one package that holds every immutable tag and buildx cache.

    Parameters
    ----------
    config : Config
        The parsed ``images.yaml``.

    Returns
    -------
    str
        The fully qualified buildcache package.
    """
    return f"{namespace(config)}/{config.get('buildcache_package', 'buildcache')}"


def description(config: Config, name: str) -> str:
    """Read the one-line description an image is labelled with.

    Required for every image, so that no published package is left without a
    description -- or, worse, with the one it inherited from its parent.

    Parameters
    ----------
    config : Config
        The parsed ``images.yaml``.
    name : str
        The image, enabled or not.

    Returns
    -------
    str
        The description, stripped. Exits if it is missing, empty or spans more
        than one line.
    """
    spec = config["images"].get(name)
    if spec is None:
        sys.exit(f"unknown image '{name}'")
    text = spec.get("description")
    if not isinstance(text, str) or not text.strip():
        sys.exit(f"'{name}' has no description in docker/images.yaml")
    # Build arguments travel one KEY=VALUE per line -- in the plan and in the
    # build action's `build-args` -- so a second line would arrive as a
    # malformed argument of its own rather than as part of this one.
    if "\n" in text.strip():
        sys.exit(f"the description of '{name}' spans several lines; write it as `>-`")
    if len(text.strip()) > DESCRIPTION_MAX:
        sys.exit(
            f"the description of '{name}' is {len(text.strip())} characters; "
            f"GHCR shows at most {DESCRIPTION_MAX}"
        )
    return text.strip()


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def hash_file(path: Path) -> str:
    """Hash one file, in chunks, so a large input costs no extra memory.

    Parameters
    ----------
    path : Path
        The file to read.

    Returns
    -------
    str
        Its full SHA-256 digest, hex encoded.
    """
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


def content_hash(root: Path, config: Config, name: str, cache: dict[str, str]) -> str:
    """Hash everything one image is built from, parents included.

    The digest covers the Dockerfile byte for byte, every file matched by
    ``inputs``, the static ``build_args``, and -- recursively -- the digest of
    every parent, so a base-image change propagates to every descendant.

    Parameters
    ----------
    root : Path
        The top of the working tree.
    config : Config
        The parsed ``images.yaml``.
    name : str
        The image to hash.
    cache : dict of str to str
        Memo of already-computed digests, mutated in place. Shared across a
        whole resolution so a common parent is hashed once.

    Returns
    -------
    str
        The digest, truncated to `HASH_LENGTH` characters for use in a tag.
    """
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
        digest.update(
            f"parent\0{arg}\0{content_hash(root, config, parent, cache)}\0".encode()
        )

    patterns = [spec["dockerfile"], *spec.get("inputs", [])]
    for pattern in patterns:
        for path in expand(root, pattern):
            rel = path.relative_to(root).as_posix()
            digest.update(f"file\0{rel}\0{hash_file(path)}\0".encode())

    for key, value in sorted(spec.get("build_args", {}).items()):
        digest.update(f"arg\0{key}\0{value}\0".encode())

    # Without this a reworded description would never reach a published image:
    # the hash would be unchanged, so the build would be skipped.
    digest.update(f"description\0{description(config, name)}\0".encode())

    cache[name] = digest.hexdigest()[:HASH_LENGTH]
    return cache[name]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def tiers(config: Config, names: list[str]) -> list[list[str]]:
    """Group images into build tiers by longest path from a root.

    Tier n depends only on tiers < n, so one CI job per tier, with ``needs:``
    between them, reproduces the layering with no per-image job dependencies --
    which GitHub Actions cannot express inside a matrix anyway.

    Parameters
    ----------
    config : Config
        The parsed ``images.yaml``.
    names : list of str
        The enabled images to place.

    Returns
    -------
    list of list of str
        One sorted list of image names per tier, shallowest first. Exits if the
        parent graph has a cycle or an enabled image has a disabled parent.
    """
    depth: dict[str, int] = {}

    def resolve(name: str, seen: tuple[str, ...] = ()) -> int:
        if name in seen:
            sys.exit(f"cycle in parents: {' -> '.join((*seen, name))}")
        if name not in depth:
            parents = config["images"][name].get("parents", {}).values()
            depth[name] = 1 + max(
                (resolve(p, (*seen, name)) for p in parents), default=-1
            )
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


def resolve(root: Path, config: Config) -> Plan:
    """Turn the configuration into every reference CI will need.

    Parameters
    ----------
    root : Path
        The top of the working tree.
    config : Config
        The parsed ``images.yaml``.

    Returns
    -------
    Plan
        A mapping with ``images`` (per-image records keyed by name), ``tiers``
        (those records grouped for the workflow's per-tier jobs), ``retention``,
        ``namespace`` and ``cache_repo``.
    """
    prefix = namespace(config)
    cache_repo = cache_repository(config)
    enabled = [n for n, s in config["images"].items() if s.get("enabled", False)]
    cache: dict[str, str] = {}

    images = {}
    for name in enabled:
        spec = config["images"][name]
        digest = content_hash(root, config, name, cache)

        # Parents are referenced by their own hash tag, never by `edge` or
        # `latest`. A moving tag here is the drift the layered build exists to
        # prevent: it would make a rebuild of a child pick up a base nobody
        # asked for. The hash tag lives in the buildcache package, so this is
        # also the reason a `FROM` in a Dockerfile must never be the only way
        # the parent is named -- CI always passes it explicitly.
        build_args = dict(spec.get("build_args", {}))
        if DESCRIPTION_ARG in build_args:
            sys.exit(
                f"'{name}' sets {DESCRIPTION_ARG} under build_args; "
                "use the `description` key instead"
            )
        build_args[DESCRIPTION_ARG] = description(config, name)
        for arg, parent in spec.get("parents", {}).items():
            parent_hash = content_hash(root, config, parent, cache)
            build_args[arg] = f"{cache_repo}:{parent}-hash-{parent_hash}"

        trivy = {**config["defaults"]["trivy"], **spec.get("trivy", {})}

        images[name] = {
            "name": name,
            # Human-readable tags only: edge, latest, vX.Y.Z.
            "repo": f"{prefix}/{name}",
            # Machine-readable tags only, all images sharing one package --
            # hence the `<name>-` prefix on every tag written here.
            "cache_repo": cache_repo,
            "hash": digest,
            "tag": f"{name}-hash-{digest}",
            "ref": f"{cache_repo}:{name}-hash-{digest}",
            "sha_tag_prefix": f"{name}-sha-",
            "cache_ref": f"{cache_repo}:{name}-cache",
            "dockerfile": spec["dockerfile"],
            "context": spec.get("context", config["defaults"]["context"]),
            "platforms": ",".join(
                spec.get("platforms", config["defaults"]["platforms"])
            ),
            "cache": spec.get("cache", config["defaults"]["cache"]),
            "moving_tag": spec.get("moving_tag", "edge"),
            # Also inside build_args, as IMAGE_DESCRIPTION; separately here for
            # the manifest annotation, which the build action writes itself.
            "description": description(config, name),
            "build_args": "\n".join(f"{k}={v}" for k, v in sorted(build_args.items())),
            "trivy_severity": ",".join(trivy["severity"]),
            "trivy_exit_code": "1" if trivy["fail"] else "0",
        }

    return {
        "images": images,
        "tiers": [[images[n] for n in tier] for tier in tiers(config, enabled)],
        "retention": config["retention"],
        "namespace": prefix,
        "cache_repo": cache_repo,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def emit_github_output(plan: Plan) -> None:
    """Write the plan to $GITHUB_OUTPUT as one JSON value per tier.

    One value per tier rather than one per image, because a workflow can only
    read outputs it names statically -- and the tier list is the thing a
    ``strategy.matrix`` consumes directly.

    All ``MAX_TIERS`` outputs are always written, empty ones as ``[]``. An
    unset output would arrive in the workflow as the empty string, which is not
    valid JSON for ``fromJson``, so the tier job's guard would have to know the
    difference between "no images here" and "no such tier".

    Parameters
    ----------
    plan : Plan
        The resolved plan, as returned by `resolve`.
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
        handle.write(
            f"retention={json.dumps(plan['retention'], separators=(',', ':'))}\n"
        )
        for index, tier in enumerate(tiers_json):
            handle.write(f"tier{index}={tier}\n")


def main() -> None:
    """Run one of ``plan``, ``ref`` or ``build-args`` from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan", help="print the resolved matrix as JSON")
    plan_parser.add_argument(
        "--github-output",
        action="store_true",
        help="also append the plan to $GITHUB_OUTPUT",
    )

    ref_parser = sub.add_parser("ref", help="print one image's immutable reference")
    ref_parser.add_argument("image")

    args_parser = sub.add_parser("build-args", help="print docker --build-arg flags")
    args_parser.add_argument("image")

    desc_parser = sub.add_parser("description", help="print one image's description")
    desc_parser.add_argument("image")

    args = parser.parse_args()
    root = repo_root()
    config = load_config(root)

    # Before resolving: this reads one key, and serves disabled images too --
    # scripts/build_docker_images_locally.sh --flavor gpu builds those.
    if args.command == "description":
        print(description(config, args.image))
        return

    plan = resolve(root, config)

    if args.command == "plan":
        print(json.dumps(plan, indent=2))
        if args.github_output:
            emit_github_output(plan)
        return

    image = plan["images"].get(args.image)
    if image is None:
        sys.exit(
            f"'{args.image}' is not an enabled image "
            f"(enabled: {', '.join(sorted(plan['images']))})"
        )

    if args.command == "ref":
        print(image["ref"])
    elif args.command == "build-args":
        # Quoted: the description is a sentence, and unquoted its words would
        # each become an argument to docker.
        print(
            " ".join(
                f"--build-arg {shlex.quote(line)}"
                for line in image["build_args"].splitlines()
                if line
            )
        )


if __name__ == "__main__":
    main()
