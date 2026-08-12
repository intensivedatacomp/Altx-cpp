#!/usr/bin/env python3
"""Delete the GHCR package versions that the image builds have superseded.

**Single writer.** This runs once, at the end of ``docker-images.yml``, and
never inside the image matrix. Parallel deleters race: two jobs each deciding
"this is the sixth newest, drop it" from different snapshots is exactly how a
retention rule removes the version something else has just started using.

**Dry run unless told otherwise.** Nothing is deleted without ``--delete``, so
running this by hand to see what CI would do is safe.

What may be deleted, and what may not
------------------------------------
The rules differ per package, because the packages hold different things (see
``docker/images.yaml``):

``altx-cpp/<image>``  -- the human-facing packages
    **Untagged versions only.** Every tag here is one a person may have typed
    into a Dockerfile or a job description, so nothing tagged is ever removed
    automatically. The untagged versions are previous ``edge`` manifests, left
    behind when the tag moved to a newer one; they are not a cache, since
    ``cache-from``/``cache-to`` name only the buildcache package, and the same
    manifest is still reachable in buildcache under its ``-hash-`` tag.

``altx-cpp/buildcache`` -- the machine-facing package
    Untagged versions, plus ``-hash-``/``-sha-`` versions beyond the ``keep``
    window, oldest first, counted **per image** rather than per package -- the
    tags share a package but not a lifetime. Protected regardless:

    * the hash tag of every enabled image *as resolved from the working tree*,
      which is what a checkout of this commit builds against;
    * ``<image>-cache``, the live buildx cache of an image that still exists;
    * anything matching ``retention.protect``.

Two properties the rules rely on
--------------------------------
A package *version* is a manifest, not a tag, and one manifest can carry
several tags: inside buildcache, ``dev-cpu-hash-<digest>`` and
``dev-cpu-sha-<commit>`` are the same version whenever that commit is the one
that last changed the image. Deletion is therefore decided per version, and a
version survives if **any** of its tags is protected.

An untagged version can also be one that is a few seconds old, between its
manifest push and its tag being written by a concurrent build. Untagged
deletions therefore wait out ``retention.grace_minutes``.

The one thing that would make "untagged is garbage" false
---------------------------------------------------------
**A multi-platform image.** Its tag names an index, and each per-platform
manifest under that index is a package version of its own with no tag: deleting
those leaves a tag pointing at children that no longer exist, which is a broken
image rather than a reclaimed one. Attestation manifests behave the same way,
which is why the build passes ``provenance: false``.

Every image is single-platform today, so this cannot arise -- but ``platforms``
is per-image in ``images.yaml`` and adding ``linux/arm64`` is a one-line change
that would silently turn this script into a wrecking ball. Untagged deletion is
therefore **skipped, loudly, for any image that declares more than one
platform**. Making it work there means resolving each index's children and
protecting those digests; the guard is here so that day starts with a warning
instead of an outage.

Usage
-----
``python3 scripts/ci/prune_packages.py``            what would be deleted
``python3 scripts/ci/prune_packages.py --delete``   delete it (what CI runs)
``python3 scripts/ci/prune_packages.py --grace-minutes 0``

Needs ``GITHUB_TOKEN`` (or ``GH_TOKEN``) with ``packages: write`` on packages
linked to this repository.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import images  # noqa: E402  -- same directory, and it owns every name used here

API = "https://api.github.com"


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------


class GitHub:
    """The few package endpoints this needs, with the org/user split handled.

    That split is the trap worth spelling out: ``/orgs/{owner}/packages/…`` is
    for organisation-owned packages and returns 404 for a personal account,
    where listing is ``/users/{owner}/packages/…`` and deleting is
    ``/user/packages/…`` -- the authenticated user's own packages, with no
    owner in the path at all. A cleanup job written against the wrong pair
    fails silently, deleting nothing while reporting success.
    """

    def __init__(self, token: str, owner: str) -> None:
        self.token = token
        self.owner = owner
        self._is_org: bool | None = None

    def _request(self, method: str, path: str) -> tuple[int, object]:
        request = urllib.request.Request(f"{API}{path}", method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "altx-cpp-prune")
        try:
            with urllib.request.urlopen(request) as response:
                body = response.read()
                return response.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as error:
            body = error.read()
            try:
                return error.code, json.loads(body) if body else None
            except json.JSONDecodeError:
                return error.code, {"message": body.decode("utf-8", "replace")}
        except urllib.error.URLError as error:
            sys.exit(f"cannot reach {API}: {error.reason}")

    @property
    def is_org(self) -> bool:
        if self._is_org is None:
            status, _ = self._request("GET", f"/orgs/{self.owner}")
            self._is_org = status == 200
        return self._is_org

    def versions(self, package: str) -> list[dict] | None:
        """Every version of one container package, or None if it does not exist."""
        quoted = urllib.parse.quote(package, safe="")
        owner_path = f"/orgs/{self.owner}" if self.is_org else f"/users/{self.owner}"
        collected: list[dict] = []
        page = 1
        while True:
            status, payload = self._request(
                "GET",
                f"{owner_path}/packages/container/{quoted}/versions"
                f"?per_page=100&page={page}",
            )
            if status == 404 and page == 1:
                return None
            if status != 200:
                sys.exit(f"listing {package} failed with HTTP {status}: {payload}")
            assert isinstance(payload, list)
            collected.extend(payload)
            if len(payload) < 100:
                return collected
            page += 1

    def delete(self, package: str, version_id: int) -> tuple[int, object]:
        quoted = urllib.parse.quote(package, safe="")
        # Note the asymmetry: /orgs/{owner}/… to delete an organisation's
        # package, but /user/… -- singular, ownerless -- for a personal one.
        owner_path = f"/orgs/{self.owner}" if self.is_org else "/user"
        return self._request(
            "DELETE", f"{owner_path}/packages/container/{quoted}/versions/{version_id}"
        )


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def tags_of(version: dict) -> list[str]:
    return version.get("metadata", {}).get("container", {}).get("tags", []) or []


def created_at(version: dict) -> datetime:
    stamp = version.get("created_at") or version.get("updated_at")
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def image_of(tags: list[str], names: list[str]) -> str | None:
    """Which image a buildcache version belongs to, from its ``<name>-…`` tags.

    The keep window is per image: they share the package but not a lifetime, so
    five pushes to dev-cpu must not evict base-cpu's history. Longest name
    first, so that an image called ``dev-cpu-mpi`` is not read as ``dev-cpu``.
    """
    for name in sorted(names, key=len, reverse=True):
        if any(tag.startswith(f"{name}-") for tag in tags):
            return name
    return None


def plan_decisions(
    versions: list[dict],
    *,
    protected_tags: set[str],
    protect_patterns: list[re.Pattern],
    image_names: list[str],
    keep: int,
    tagged_deletable: bool,
    delete_untagged: bool,
    grace: timedelta,
    now: datetime,
) -> list[tuple[dict, bool, str]]:
    """Return ``(version, delete?, reason)`` for every version, newest first."""
    decisions: list[tuple[dict, bool, str]] = []
    # Per image, the tagged candidates in newest-first order; the keep window is
    # applied to that order rather than to the package as a whole.
    seen: dict[str | None, int] = {}

    for version in sorted(versions, key=created_at, reverse=True):
        tags = tags_of(version)

        if not tags:
            age = now - created_at(version)
            if not delete_untagged:
                decisions.append((version, False, "untagged, but untagged deletion is off here"))
            elif age < grace:
                decisions.append((version, False, f"untagged but only {format_age(age)} old"))
            else:
                decisions.append((version, True, f"untagged, {format_age(age)} old"))
            continue

        protected = [t for t in tags if t in protected_tags]
        protected += [t for t in tags if any(p.search(t) for p in protect_patterns)]
        if protected:
            decisions.append((version, False, f"protected: {protected[0]}"))
            continue

        if not tagged_deletable:
            # A human-facing package. Anything tagged here was published for
            # someone to type, and is never removed without a person asking.
            decisions.append((version, False, f"tagged: {tags[0]}"))
            continue

        image = image_of(tags, image_names)
        rank = seen[image] = seen.get(image, 0) + 1
        label = image or "unrecognised"
        if rank <= keep:
            decisions.append((version, False, f"{label}: within the newest {keep}"))
        else:
            decisions.append((version, True, f"{label}: superseded, #{rank} newest"))

    return decisions


def format_age(age: timedelta) -> str:
    minutes = age.total_seconds() / 60
    if minutes < 120:
        return f"{minutes:.0f}m"
    if minutes < 48 * 60:
        return f"{minutes / 60:.0f}h"
    return f"{minutes / 1440:.0f}d"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def summary_line(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as handle:
            handle.write(text + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true",
                        help="actually delete; without it nothing is removed")
    parser.add_argument("--grace-minutes", type=float, default=None,
                        help="override retention.grace_minutes from images.yaml")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set; it needs packages: write")

    root = images.repo_root()
    config = images.load_config(root)
    plan = images.resolve(root, config)
    retention = plan["retention"]

    namespace = plan["namespace"]                 # ghcr.io/<owner>/<repo>
    owner = namespace.split("/")[1]
    # The package name is everything after the owner: `altx-cpp/dev-cpu`, which
    # the API takes percent-encoded.
    def package_name(repo: str) -> str:
        return repo.split("/", 2)[2]

    image_names = sorted(plan["images"])
    cache_package = package_name(plan["cache_repo"])

    # The hash tag of every enabled image, and every live cache tag: what a
    # checkout of this commit builds against, and therefore what must survive
    # this run no matter how old it is.
    protected_tags = set()
    for record in plan["images"].values():
        protected_tags.add(record["tag"])
        protected_tags.add(record["cache_ref"].split(":", 1)[1])

    protect_patterns = [re.compile(p) for p in retention["protect"]]
    keep = int(retention["keep"])
    grace = timedelta(minutes=args.grace_minutes if args.grace_minutes is not None
                      else float(retention.get("grace_minutes", 10)))
    now = datetime.now(timezone.utc)

    # An image built for several platforms publishes an index whose per-platform
    # children are untagged versions in their own right, so "untagged is
    # garbage" stops being true and deleting them breaks the tag. See the module
    # docstring: refuse rather than guess.
    multi_platform = sorted(
        name for name, record in plan["images"].items() if "," in record["platforms"]
    )
    untagged_ok = bool(retention.get("delete_untagged", True))

    api = GitHub(token, owner)
    # package -> the image it belongs to, or None for the shared buildcache.
    packages = [(package_name(r["repo"]), name) for name, r in plan["images"].items()]
    packages.append((cache_package, None))

    summary_line(f"### Registry prune ({'deleting' if args.delete else 'dry run'})\n")
    total_deleted = failures = 0

    for package, image in packages:
        versions = api.versions(package)
        if versions is None:
            summary_line(f"- `{package}` -- no such package yet, skipped")
            continue

        # buildcache holds every image's versions, so one multi-platform image
        # is enough to disqualify the whole package.
        unsafe = multi_platform if image is None else [image] * (image in multi_platform)
        if unsafe and untagged_ok:
            summary_line(
                f"- `{package}` -- untagged deletion SKIPPED: "
                f"{', '.join(unsafe)} is multi-platform, so untagged versions are "
                f"the per-platform manifests of a tagged index"
            )
            print("::warning title=Untagged versions kept::"
                  f"{package}: multi-platform image ({', '.join(unsafe)}). Deleting "
                  "untagged versions would break the index. Teach "
                  "prune_packages.py to resolve index children before enabling it.")

        decisions = plan_decisions(
            versions,
            protected_tags=protected_tags,
            protect_patterns=protect_patterns,
            image_names=image_names,
            keep=keep,
            # Only the shared machine-facing package expires tagged versions.
            tagged_deletable=(package == cache_package),
            delete_untagged=untagged_ok and not unsafe,
            grace=grace,
            now=now,
        )

        doomed = [(v, why) for v, delete, why in decisions if delete]
        summary_line(
            f"- `{package}` -- {len(versions)} versions, "
            f"{len(doomed)} to delete"
        )
        for version, why in doomed:
            digest = version["name"][:19]
            tags = ", ".join(tags_of(version)) or "<untagged>"
            print(f"    delete {digest}…  {tags}  ({why})")
            if not args.delete:
                continue
            status, payload = api.delete(package, version["id"])
            if status in (204, 200):
                total_deleted += 1
            else:
                failures += 1
                print(f"::warning title=Prune failed::{package} {digest}: "
                      f"HTTP {status} {payload}")

    if args.delete:
        summary_line(f"\nDeleted {total_deleted} versions, {failures} failed.")
        # A failure here is a permissions problem often enough that it must not
        # pass quietly, but it is also never a reason to distrust the images
        # that were just built and verified -- so it fails this job alone.
        if failures:
            sys.exit(1)
    else:
        summary_line("\nDry run: nothing was deleted. Pass --delete to apply.")


if __name__ == "__main__":
    main()
