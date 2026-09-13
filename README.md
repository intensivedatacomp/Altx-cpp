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

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
