# Altx-cpp

[![Pre-commit](https://github.com/intensivedatacomp/Altx-cpp/actions/workflows/pre-commit.yml/badge.svg)](https://github.com/intensivedatacomp/Altx-cpp/actions/workflows/pre-commit.yml)
[![Build and test](https://github.com/intensivedatacomp/Altx-cpp/actions/workflows/build-test.yml/badge.svg)](https://github.com/intensivedatacomp/Altx-cpp/actions/workflows/build-test.yml)
![Coverage](.badges/coverage.svg)

## Development

Start an interactive development session with the repository mounted:

```bash
docker run --pull always --rm -it -v "$PWD:/workspace" -w /workspace \
    ghcr.io/intensivedatacomp/altx-cpp/dev-cpu:edge
```

`edge` is the newest build of the development image. `--pull always` is what keeps that true:
without it Docker pulls only when the tag is missing locally, so a stale `edge` from last month
would keep starting silently. Drop it to work offline, or once the pull becomes the slow part.

For the system clipboard in Vim (`"+y`, `"+p`), also hand the container your X11 display:

```bash
xhost +SI:localuser:"$(id -un)"   # on the host, once per login session
docker run --pull always --rm -it -v "$PWD:/workspace" -w /workspace \
    -e DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix \
    ghcr.io/intensivedatacomp/altx-cpp/dev-cpu:edge
```

To build the image yourself instead — which is also how you get one matching your own uid — use
`./scripts/build_docker_images_locally.sh --target dev` and run `altx-cpp/dev-cpu:local`.

See [docs/DevelopmentEnvironment.md](docs/DevelopmentEnvironment.md) for the other configurations
and the editor tooling.

## Building

Inside that container, or on any machine with CMake 3.25, Ninja and a C++20 compiler:

```bash
cmake --preset cpu-omp-debug          # configure
cmake --build --preset cpu-omp-debug  # build, into build/cpu-omp-debug/
ctest --preset cpu-omp-debug          # run the tests
```

A preset is the unit of build, and each one corresponds to a Docker image:
`cpu-serial-debug` and `cpu-omp-debug` (sanitizers on) for development, `cpu-serial-release` and
`cpu-omp-release` for what `runtime-cpu` ships, and `coverage` for gcov. `cmake --list-presets`
prints them with a description each. The MPI and HIP presets arrive with the code that needs them.

The build options are `ALTX_ENABLE_{OPENMP,MPI,HIP,TESTS,COVERAGE}`, `ALTX_SCALAR`
(`double` or `float`), `ALTX_SANITIZERS`, `ALTX_WERROR` and `ALTX_NATIVE_ARCH`; a configure prints
the resolved set. The first configure downloads GoogleTest, so it needs the network unless
`ALTX_ENABLE_TESTS=OFF`.

The version is not written down anywhere: `git describe` is read at build time into a generated
`core/Version.hpp`, and the full commit hash goes into the provenance of every output file.

Coverage, with the HTML report in `coverage/html/` and the badge above regenerated:

```bash
scripts/ci/coverage.sh          # inside dev-cpu, which has lcov and genhtml
```

CI runs the same script, so a number that looks wrong there reproduces here in one command.

## Contributing

Install the git hooks once per clone. Formatting, linting, typing and spelling are then checked on
every `git commit`:

```bash
uv tool install pre-commit && pre-commit install
```

Use `uv tool install` rather than `uvx`: the generated git hook records the interpreter path, and
`uvx` puts it in a cache directory that may be cleaned.

To check the whole tree at any time:

```bash
pre-commit run --all-files
```

Every check, and how to silence one, is documented in [docs/CodeQuality.md](docs/CodeQuality.md).

1. Make sure the test suite and pre-commit hooks pass on your branch before
   opening a pull request.
2. Follow the [NumPy docstring convention](https://numpydoc.readthedocs.io/en/latest/format.html).
3. Keep each pull request focused on a single change.

```bash
git switch -c feature/your-awesome-feature

# ... make changes ...

git add .
git commit -m "Useful commit message"
git push --set-upstream origin feature/your-awesome-feature
xdg-open https://github.com/dcintlab/artificial-dataset/pull/new/feature/your-awesome-feature

# ... merge the branch to main, make sure that the pipeline passes, delete the branch ...

git switch main
git pull
git branch -d feature/your-awesome-feature
git branch -d feature/your-awesome-feature --remote
```

## Scripts

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

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
