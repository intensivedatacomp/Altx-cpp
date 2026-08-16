@page code_quality Code Quality

@brief Every automated check in the repository: what runs, where it runs, how to configure it and
how to silence one when it is wrong.

Formatting, linting, typing and spelling are enforced by `pre-commit`, which runs the checks as a
git hook so that nothing reaches a pull request in a state CI will reject. The framework is written
in Python but is language-agnostic — it drives clang-format, hadolint, shellcheck and actionlint
just as readily, which is why one tool covers a C++ project that also carries Dockerfiles,
workflows and shell scripts.

[TOC]

@section quality_setup Setup

Two commands, once per clone:

```bash
uv tool install pre-commit     # a durable install, on PATH
pre-commit install             # writes .git/hooks/pre-commit and .git/hooks/pre-push
```

After that the checks run on every `git commit`. To check the whole tree without committing:

```bash
pre-commit run --all-files
```

@warning Use `uv tool install`, not `uvx pre-commit install`. `uvx` resolves the tool into an
ephemeral cache directory and `pre-commit install` writes *that* path into the generated hook
verbatim; one `uv cache clean` later, every `git commit` in the repository fails with
`` `pre-commit` not found ``. The hook's only fallback is a `pre-commit` on `PATH`, which `uvx`
does not provide and `uv tool install` does.

@note Inside the development container both steps are already done — see
@ref quality_container.

@section quality_tiers Three tiers, split by what a check needs

The organising principle is **what a check requires in order to run**, because that decides where
it can live without making commits slow or unreliable.

| Tier         | Requirement                    | Checks                                            |
| ------------ | ------------------------------ | ------------------------------------------------- |
| `pre-commit` | nothing but the changed files  | everything in @ref quality_checks                  |
| `pre-push`   | the whole tree, still local    | Doxygen documentation coverage — not yet written   |
| CI           | a **configured build**         | clang-tidy, `-Wdocumentation`, coverage — stage 2+ |

clang-tidy is deliberately *not* a commit hook: it needs `compile_commands.json`, which exists only
after CMake has configured a build directory. A hook that silently skips when that file is missing
gives different results to different developers, which is worse than not having the check at all.

The first two tiers also run in CI — see @ref quality_ci. `pre-commit install` is per-clone and
`--no-verify` bypasses it, so the local hooks are a fast path, not the enforcement point.

The tiers are kept disjoint. `default_stages: [pre-commit]` in `.pre-commit-config.yaml` means a
hook runs at commit time only, unless it explicitly says `stages: [pre-push]`.

@note Three hooks from `pre-commit-hooks` ship `stages: [commit, push, manual]` upstream, which
overrides `default_stages`. `trailing-whitespace`, `end-of-file-fixer` and `check-added-large-files`
therefore carry an explicit `stages: [pre-commit]` in the configuration. Without it they run a
second time on every `git push`, having already passed on the way in.

@section quality_checks What runs

Seventeen hooks, in the order they execute.

@subsection quality_checks_general General

| Hook                       | Enforces                                                       |
| -------------------------- | -------------------------------------------------------------- |
| `trailing-whitespace`      | No trailing whitespace                                          |
| `end-of-file-fixer`        | Every file ends in exactly one newline                          |
| `check-yaml`, `check-json` | The file parses                                                 |
| `check-merge-conflict`     | No conflict markers committed                                   |
| `check-added-large-files`  | Nothing over **500 kB**                                         |
| `no-commit-to-branch`      | No commit directly to `main` or `master`                        |

The size limit matters more here than in a pure-Python project: the plan commits `.h5` fixtures for
the cross-language round-trip test, so this is what stands between the repository and a
multi-gigabyte dataset. 500 kB is deliberately tighter than the Python repository's 5000.

`no-commit-to-branch` is the one check that exists only at commit time — `pre-commit run
--all-files` cannot enforce a branch policy — and therefore the one that makes
`pre-commit install` worth doing rather than relying on a manual run.

@subsection quality_checks_cpp C++ and CMake

| Hook           | Enforces                                              |
| -------------- | ----------------------------------------------------- |
| `clang-format` | Formatting of `.c`, `.cpp`, `.h`, `.hpp`, `.cu`       |
| `gersemi`      | Formatting of `CMakeLists.txt` and `*.cmake`          |

Both rewrite files in place; a hook that reformats reports failure, so re-staging and committing
again is the normal flow.

The style is a **minimal delta on Google**, not a `clang-format --dump-config` output — a full dump
silently pins the project to one version's defaults and produces a large, meaningless diff on every
upgrade. The genuine overrides are `IndentWidth: 4`, `ColumnLimit: 80`, `PointerAlignment: Right`
with `DerivePointerAlignment: false`, `AccessModifierOffset: -4` and `Standard: c++20`.

@warning `DerivePointerAlignment` must stay `false`. Google leaves it `true`, which tells
clang-format to infer pointer alignment *per file* from what is already there — so
`PointerAlignment` is ignored and `int *p` and `int* p` can both persist in different files.

@note `.hip` is not a file type `identify` recognises, so when `src/hip/` appears its files must be
added to the hook's `types_or`/`files` explicitly or they will silently go unformatted.

@subsection quality_checks_infra Dockerfiles, workflows and shell

| Hook         | Covers                                    |
| ------------ | ----------------------------------------- |
| `hadolint`   | `docker/*.Dockerfile`                     |
| `actionlint` | `.github/workflows/`                      |
| `shellcheck` | `scripts/*.sh` and container entrypoints  |

These exist because this project carries far more non-C++ configuration than the Python original —
and those are exactly the files where a mistake stays invisible until CI runs.

@subsection quality_checks_python Python and prose

| Hook          | Enforces                                                            |
| ------------- | ------------------------------------------------------------------- |
| `codespell`   | Spelling, including inside comments and documentation                |
| `ruff-format` | Formatting of `scripts/**.py` (replaces black)                       |
| `ruff-check`  | Lint, import order and NumPy docstrings, `--fix` (replaces pydocstyle)|
| `mypy`        | `--strict` type checking of `scripts/`                               |
| `nbqa-mypy`   | The same, inside Jupyter notebooks                                   |

`scripts/` is not incidental: `scripts/ci/images.py` decides every image tag CI uses, and
`scripts/gen_reference.py` will generate the correctness oracle. Neither is read by a compiler.

@note There is no `nbqa-ruff` or `nbqa-black`. Both `ruff` hooks declare
`types_or: [python, pyi, jupyter]` and ruff reads `.ipynb` natively, so a wrapper would report the
same findings twice. mypy has no notebook support, which is the one real gap `nbqa` fills.

@section quality_config Where the configuration lives

| File                      | Configures                                            |
| ------------------------- | ------------------------------------------------------ |
| `.pre-commit-config.yaml` | Which hooks run, at which pinned version, on which files|
| `pyproject.toml`          | `[tool.ruff]`, `[tool.mypy]`, `[tool.codespell]`        |
| `.clang-format`           | C++ style                                               |
| `.hadolint.yaml`          | Dockerfile rule exceptions                              |

`pyproject.toml` has **no `[build-system]` and no `[project]` table**. This repository is not a
Python package and nothing here is ever built or installed; the file exists only because those
three tools read `[tool.*]` out of it with no other setup. Adding `[project]` would declare a
distributable package that does not exist.

@warning One consequence of that file existing: `uv` reads any root `pyproject.toml` as a project,
so a bare `uv run scripts/ci/images.py` drops a `.venv/` and a `uv.lock` into the working tree.
Pass `--no-project` for ad-hoc runs. CI is unaffected — the workflows invoke these scripts with
`python3`.

@section quality_silencing Silencing a check

In order of preference: narrow the exception as far as it will go, and say why. An unexplained
suppression is indistinguishable from a defect somebody gave up on.

| Check          | One line                        | One file or module                     | Repository-wide            |
| -------------- | ------------------------------- | -------------------------------------- | -------------------------- |
| `ruff`         | `# noqa: D103`                  | `[tool.ruff.lint.per-file-ignores]`    | `[tool.ruff.lint] ignore`  |
| `mypy`         | `# type: ignore[type-arg]`      | `[[tool.mypy.overrides]]`              | `[tool.mypy]`              |
| `codespell`    | `# codespell:ignore`            | —                                      | `ignore-words-list`        |
| `clang-format` | `// clang-format off` … `on`    | —                                      | `.clang-format`            |
| `hadolint`     | `# hadolint ignore=DL3008`      | —                                      | `.hadolint.yaml`           |
| `shellcheck`   | `# shellcheck disable=SC2015`   | —                                      | —                          |

@warning A bare `# type: ignore` is rejected under `strict`; the error code in brackets is
required. When relaxing mypy for a module, switch off the single `strict` sub-flag that is in the
way — `disallow_untyped_defs`, say — rather than `strict` itself.

`.hadolint.yaml` keeps one dividing line, and it is worth preserving: a rule is disabled only when
hadolint is **wrong about this repository** or is arguing with a decision already made. Four are
disabled today — `DL3006` (fires on `FROM base-${FLAVOR}`, a build *stage*, while the real bases
are pinned by digest), `DL3064` (matches the *name* `ARG USERNAME`), `DL3001` (objects to the
headless `vim -c PlugInstall` that installs the editor's plugins), and `DL3008` (pin apt versions —
Ubuntu's archive keeps only the current version of a package, so a pin fails the moment a security
update lands). Everything hadolint is *right* about is fixed in the Dockerfile. A configuration
entry that hides a real defect is worse than not running the linter.

To skip a hook for one commit — rarely the right answer, and never for a formatter:

```bash
SKIP=mypy git commit -m "..."
git commit --no-verify -m "..."   # skips every hook
```

@section quality_container Running the checks in the container

`dev-cpu` installs `pre-commit` and bakes `~/.cache/pre-commit` into a layer at build time, so a
fresh container runs the full suite **offline**, immediately:

```bash
docker run --rm -it -v "$PWD:/workspace" -w /workspace \
    ghcr.io/intensivedatacomp/altx-cpp/dev-cpu:edge -c "pre-commit run --all-files"
```

This is the recommended way to run them. clang-format's output differs between major versions, so a
developer running a different one from CI fights an endless reformatting loop; the image ships
`clang-format-18` and the hook is pinned to `v18.1.3`, and inside the container the two are
identical by construction.

The cost is size — roughly 800 MB of hook environments, most of it the Go toolchain `actionlint`
needs — paid once in the image rather than over the network in every fresh container.

@note `.pre-commit-config.yaml` is listed under `inputs` for `dev-cpu` and `dev-gpu` in
`docker/images.yaml`. Because the cache is baked in, it is part of the image's content hash:
bumping a hook `rev` rebuilds the image. Without that entry the baked environments would quietly
stop matching the configuration.

@section quality_ci In CI

`.github/workflows/pre-commit.yml` runs the same hooks over the whole tree on **every push, on
every branch**, and on every pull request. Two steps, one per local tier: the commit-stage hooks,
then the push-stage ones. A cold run takes about a minute; `~/.cache/pre-commit` is cached, keyed on
`.pre-commit-config.yaml`, so bumping one hook's `rev` rebuilds only that environment.

Pushing to a branch that already has a pull request open runs the job twice, which is accepted
rather than deduplicated: `pull_request` runs against the merge of head into base — what will
actually land — while `push` runs against the branch as written. Superseded runs of the *same*
event are cancelled, so a rapid series of pushes leaves one standing.

`no-commit-to-branch` is skipped there, via `SKIP=no-commit-to-branch`. It is a statement about the
branch a developer is working on, and the one place it would fire in CI is the push event *on*
`main` — after the pull request carrying the change has already been reviewed and merged. On a
feature branch it passes anyway, and on a pull request `actions/checkout` leaves a detached HEAD,
so there is no branch for it to object to.

@note The job does **not** run inside `dev-cpu`, and does not need to. Every hook supplies its own
tool at a pinned version: `clang-format` is a `language: python` hook that installs the pinned
`clang-format` wheel and never touches the `clang-format-18` in the image. A plain runner therefore
formats byte for byte the same as the container. Running the linters inside the image would also
make them wait on the image build, and deadlock on a pull request that changes a Dockerfile.

`--show-diff-on-failure` is passed, so when a formatter fails the log contains the patch to apply
rather than only the news that something was wrong.

@section quality_upgrading Upgrading the hooks

```bash
pre-commit autoupdate
```

@warning `autoupdate` will bump `mirrors-clang-format` past 18, and it must not. That `rev` and the
`clang-format-18` installed by `docker/dev.Dockerfile` are a matched pair — change one and you must
change the other, or the container and the host will disagree about formatting forever. The same
applies to the `mypy` version pinned inside the `nbqa-mypy` hook's `additional_dependencies`, which
tracks the `mirrors-mypy` rev.

@section quality_trouble Troubleshooting

| Symptom                                                | Cause                                                                     |
| ------------------------------------------------------ | ------------------------------------------------------------------------- |
| `` `pre-commit` not found `` on every commit           | Hooks installed via `uvx`; its cache path was cleaned. Reinstall with `uv tool install` |
| A hook fails, then passes on re-run with no edit       | Normal: a formatter rewrote files. Re-stage and commit again               |
| `Your pre-commit configuration is unstaged`            | `.pre-commit-config.yaml` is modified but not staged                       |
| `.pre-commit-hooks.yaml is not a file`                 | A hook `rev` points at a repository that no longer carries the manifest — `gersemi` moved its to `gersemi-pre-commit` at 0.27.1 |
| `mypy` reports nothing on a file you just broke        | It is scoped to `^scripts/.*\.py$`                                         |
| The whole suite is slow on first run                   | Hook environments are being built. Use the container, where they are baked |
| Formatting differs between your machine and CI         | Different clang-format major version — run the checks in `dev-cpu`         |
