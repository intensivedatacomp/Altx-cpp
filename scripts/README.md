# Scripts

Every script works from any directory inside the checkout, and prints its full usage with `--help`.

### `scripts/count_lines.py` — the size of the code base

```bash
scripts/count_lines.py               # banner, full commit hash, lines per language
scripts/count_lines.py --files       # ... and the category every file was counted in
scripts/count_lines.py --untracked   # include new files not yet added to git
```

Counts the files git tracks, as they are in the working tree, so build output and anything else
in `.gitignore` stays out. The commit line notes when uncommitted changes are part of the count.

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
times over. Needs only git and Python 3.

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
[docs/DevelopmentEnvironment.md](docs/DevelopmentEnvironment.md) for every option.

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
the working tree, so the result here is the one CI computes. Needs PyYAML; without it, run
`uv run --no-project --with pyyaml scripts/ci/images.py plan`.

### `scripts/ci/prune_packages.py` — GHCR retention

```bash
GITHUB_TOKEN=<token> python3 scripts/ci/prune_packages.py              # what would be deleted
GITHUB_TOKEN=<token> python3 scripts/ci/prune_packages.py --delete     # delete it, as CI does
```

Applies the `retention:` policy of `docker/images.yaml` to the GHCR packages. It is a dry run
unless given `--delete`. Listing needs a token with `read:packages`, and deleting needs
`packages: write`. CI runs it once, after every image has been published.
