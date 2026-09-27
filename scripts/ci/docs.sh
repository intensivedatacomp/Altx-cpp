#!/usr/bin/env bash
#
# Build the Doxygen documentation, and fail on any Doxygen warning.
#
# This is the whole of the documentation-coverage check: docs/Doxyfile.in sets
# WARN_AS_ERROR = FAIL_ON_WARNINGS with EXTRACT_ALL = NO, so an undocumented
# function or a @param naming an argument that does not exist is a non-zero exit
# rather than a line of output nobody reads.
#
# Both the `doxygen` pre-push hook and .github/workflows/docs.yml call this, for
# the reason scripts/ci/coverage.sh exists: a check that only CI knows how to run
# is one nobody can reproduce locally. The hook and the workflow therefore pass
# and fail together, and `git push` finds out what the pull request would have.
#
#   scripts/ci/docs.sh                    # configure and build; site under build/doxygen/
#   scripts/ci/docs.sh --skip-configure   # reuse an existing build directory
#   scripts/ci/docs.sh --preset cpu-omp-release   # build the docs of another preset
#
# The `doxygen` preset has a build directory of its own, so this never rewrites the
# cache of a directory you are compiling in, and it configures with the tests off
# so that nothing here needs GoogleTest.
#
# Run it inside dev-cpu, which has doxygen and graphviz.
#
# Usage:
#   scripts/ci/docs.sh [--preset NAME] [--skip-configure]
#
set -euo pipefail

# Not `A || B && C`: that parses as `(A || B) && C`, so the fallback's `pwd`
# would run even when git answered, and REPO_ROOT would hold two lines.
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) \
    || REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PRESET=doxygen
SKIP_CONFIGURE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --preset)         PRESET="${2:?--preset needs a value}"; shift 2 ;;
        --skip-configure) SKIP_CONFIGURE=1; shift ;;
        -h|--help)        sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *)                echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

# The hint matters more here than for lcov: this runs from a git hook, where the
# default failure is `doxygen: command not found` in the middle of a push and no
# indication that the check was meant to run in a container at all.
command -v doxygen >/dev/null || {
    echo "doxygen is not on PATH -- run this inside dev-cpu, or install doxygen and graphviz" >&2
    exit 1
}
command -v dot >/dev/null || {
    echo "dot is not on PATH -- graphviz is missing; run this inside dev-cpu" >&2
    exit 1
}

cd "${REPO_ROOT}"

BUILD_DIR="${REPO_ROOT}/build/${PRESET}"

if (( SKIP_CONFIGURE )); then
    [[ -d "${BUILD_DIR}" ]] || {
        echo "no build directory at ${BUILD_DIR}; drop --skip-configure" >&2
        exit 1
    }
else
    # `-DALTX_ENABLE_DOCS=ON` as well as the preset, so that `--preset` can name
    # a code preset -- where docs are off by default -- and still build them.
    cmake --preset "${PRESET}" -DALTX_ENABLE_DOCS=ON
fi

cmake --build --preset "${PRESET}" --target docs

echo
echo "documentation: ${BUILD_DIR}/docs/html/index.html"
