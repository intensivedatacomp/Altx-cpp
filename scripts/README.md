# Scripts

Every script works from any directory inside the checkout, and prints its full usage with `--help`.

The Python scripts declare their own requirements inline, in a PEP 723 `# /// script` block, so
`uv run scripts/<name>.py` works with nothing installed. The block also stops uv from treating the
repository's linter-only `pyproject.toml` as a project, which is what otherwise causes the warning
`No requires-python value found in the workspace`.

### `scripts/count_lines.py` — the size of the code base

```bash
scripts/count_lines.py                           # banner, version, commit, lines per language
scripts/count_lines.py --breakdown               # ... split into code, comment and blank lines
scripts/count_lines.py --compare v0.0.2          # how many lines each language gained since a tag
scripts/count_lines.py --by directory --depth 2  # a row per directory instead of per language
scripts/count_lines.py --rev v0.0.1 --code-only  # an old commit, without checking it out
scripts/count_lines.py src tests --largest 5     # only under src/ and tests/, plus the 5 longest files
scripts/count_lines.py --format markdown         # a table to paste into a pull request
scripts/count_lines.py --format json             # every number, for another tool
```

The header shows the version exactly as the build stamps it. It uses
`git describe --tags --dirty --always`, the command in `cmake/GitVersion.cmake`, so
`v0.0.2-32-g6456b0b-dirty` means 32 commits after the tag `v0.0.2`, with uncommitted changes. The
full commit hash is printed below it.

| Option | Effect |
| --- | --- |
| `PATH ...` | Count only the files under these paths |
| `--rev REV` | Count a commit, tag or branch as committed, instead of the working tree |
| `--untracked` | Also count working-tree files that are neither tracked nor ignored |
| `--code-only` | Leave out Documentation and Other |
| `--by language\|directory`, `--depth N` | One row per language (the default), or per directory, keeping N directory levels |
| `--breakdown` | Split every row into code, comment and blank lines |
| `--compare REV` | Add a column with each row's change in lines since `REV` |
| `--sort lines\|files\|name` | Row order; the default is most lines first |
| `--largest N`, `--files` | Also list the N longest files, or every file, with its category |
| `--format table\|markdown\|json`, `--no-banner` | Output format; only `table` prints the banner |

By default it counts the files git tracks, as they are in the working tree, so build output and
anything else in `.gitignore` stays out. The version line says when uncommitted changes are part
of the count.

| Category | What lands there |
| --- | --- |
| C++ | `.cpp`, `.hpp`, `.h`, `.hip` …, and `configure_file` templates such as `Version.hpp.in` |
| CMake | `CMakeLists.txt`, `*.cmake` |
| Python, Bash | by extension, or by the `#!` line of an extensionless script |
| Dockerfile | `*.Dockerfile`, `Dockerfile` |
| Vim script | `vimrc`, `*.vim` |
| GitHub Actions | YAML under `.github/` — workflows and the composite action |
| Configuration | the remaining YAML, JSON and TOML, and tool dotfiles (`.clang-format`, `.gitignore`, …) |
| Documentation | Markdown |
| Other | everything else: the licence, the coverage badge, the shared spell list |

*Of all* is a category's share of every line. *Of code* leaves out Documentation and Other, which
together make up over 40 % of the lines, and `DevelopmentPlan.md` alone outweighs the C++ many
times over.

`--breakdown` sorts lines by each language's comment syntax: `#`, `//` and `/* */`, Vim's `"`,
CMake's `#[[ ]]` and Python docstrings. A line with code followed by a comment counts as code.
This is a line classifier, not a parser, so for example a `/*` inside a C++ string literal will
fool it. The script needs only git and Python 3.12 or newer.

### `scripts/build_docker_images_locally.sh` — the images, built the way CI builds them

```bash
./scripts/build_docker_images_locally.sh                    # base-cpu + dev-cpu, smoke tested
./scripts/build_docker_images_locally.sh --target all       # ... and runtime-cpu
./scripts/build_docker_images_locally.sh --target dev --no-cache
```

Builds the `base -> dev -> runtime` chain in order, with the same build arguments as CI, as
`altx-cpp/<image>:local`. The smoke tests are the reason to use it over a bare `docker build`.
They check the things that fail silently: BLAS resolving to the OpenMP build of OpenBLAS, clangd
finding the library headers, Vim's `+clipboard`, the description labels, and the pre-commit hooks
running offline. Needs Docker with buildx. See
[docs/DevelopmentEnvironment.md](../docs/DevelopmentEnvironment.md) for every option.

### `scripts/ci/coverage.sh` and `scripts/ci/coverage_badge.py` — coverage

```bash
scripts/ci/coverage.sh                   # configure, build and test the coverage preset, then report
scripts/ci/coverage.sh --skip-build      # report on an existing build/coverage
scripts/ci/coverage.sh --fail-under 80   # exit non-zero below 80 % line coverage
```

`coverage.sh` writes the lcov tracefile, the HTML report in `coverage/html/` and the badge. Run it
inside `dev-cpu`, which has lcov. It calls `coverage_badge.py`, which turns a tracefile into a
percentage and an SVG without any network access:
`scripts/ci/coverage_badge.py --info coverage/coverage.info --output .badges/coverage.svg`.

### `scripts/ci/images.py` — the image matrix

```bash
python3 scripts/ci/images.py plan                    # every enabled image: hashes, tags, build arguments
python3 scripts/ci/images.py ref dev-cpu             # the immutable hash- reference CI pins
python3 scripts/ci/images.py build-args dev-cpu      # --build-arg flags, shell-quoted
python3 scripts/ci/images.py description dev-gpu     # the label text, disabled images included
```

Resolves `docker/images.yaml` into everything CI needs. Every image reference is a pure function of
the working tree, so the result here is the one CI computes. Needs PyYAML. Without it, run
`uv run scripts/ci/images.py plan`, which installs PyYAML from the script's inline metadata.

### `scripts/ci/prune_packages.py` — GHCR retention

```bash
GITHUB_TOKEN=<token> python3 scripts/ci/prune_packages.py              # what would be deleted
GITHUB_TOKEN=<token> python3 scripts/ci/prune_packages.py --delete     # delete it, as CI does
```

Applies the `retention:` policy of `docker/images.yaml` to the GHCR packages. It is a dry run
unless given `--delete`. Listing needs a token with `read:packages`, and deleting needs
`packages: write`. CI runs it once, after every image has been published.
