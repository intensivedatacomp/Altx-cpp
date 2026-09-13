#!/usr/bin/env bash
#
# Configure, build and run the `coverage` preset, then turn the result into a
# tracefile, an HTML report and a badge.
#
# CI calls this rather than spelling the steps out in a workflow, for the same
# reason scripts/build_docker_images_locally.sh exists: the lcov invocation is
# fiddly enough that knowing it only from a YAML file means nobody can
# reproduce a coverage number locally.
#
#   scripts/ci/coverage.sh                 # into build/coverage, report in coverage/
#   scripts/ci/coverage.sh --skip-build    # reuse an existing build directory
#
# Run it inside dev-cpu, which has lcov and genhtml.
#
# Usage:
#   scripts/ci/coverage.sh [--skip-build] [--output-dir DIR] [--fail-under PCT]
#
set -euo pipefail

# Not `A || B && C`: that parses as `(A || B) && C`, so the fallback's `pwd`
# would run even when git answered, and REPO_ROOT would hold two lines.
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) \
    || REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PRESET=coverage
BUILD_DIR="${REPO_ROOT}/build/${PRESET}"
OUTPUT_DIR="${REPO_ROOT}/coverage"
SKIP_BUILD=0
FAIL_UNDER=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-build)  SKIP_BUILD=1; shift ;;
        --output-dir)  OUTPUT_DIR="${2:?--output-dir needs a value}"; shift 2 ;;
        --fail-under)  FAIL_UNDER="${2:?--fail-under needs a value}"; shift 2 ;;
        -h|--help)     sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *)             echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

command -v lcov >/dev/null    || { echo "lcov is not on PATH -- run this inside dev-cpu" >&2; exit 1; }
command -v genhtml >/dev/null || { echo "genhtml is not on PATH -- run this inside dev-cpu" >&2; exit 1; }

cd "${REPO_ROOT}"

if (( ! SKIP_BUILD )); then
    cmake --preset "${PRESET}"
    cmake --build --preset "${PRESET}"
    ctest --preset "${PRESET}"
fi

[[ -d "${BUILD_DIR}" ]] || { echo "no build directory at ${BUILD_DIR}" >&2; exit 1; }

mkdir -p "${OUTPUT_DIR}"
INFO="${OUTPUT_DIR}/coverage.info"

# lcov 2.0 promotes several conditions that 1.x warned about into errors, and
# each of the three below is a property of this project rather than a defect:
#
#   mismatch   gcov's idea of where a function ends disagrees with the source
#              for anything the preprocessor expanded -- every TEST() macro.
#   unused     a --remove pattern that matches nothing, which is what the
#              _deps pattern does whenever GoogleTest was resolved from a
#              pre-populated FetchContent cache rather than built here.
#   empty      no records at all, which `--capture --initial` legitimately
#              produces before anything has run.
IGNORE=mismatch,unused,empty

# Two captures, because a translation unit that is never executed writes no
# .gcda at all and would simply be missing from the report -- the least covered
# code disappearing from the coverage number. `--initial` reads the .gcno files
# instead, giving every instrumented line a zero, and the merge below turns
# that into an honest denominator.
lcov --quiet --ignore-errors "${IGNORE}" --capture --initial \
    --directory "${BUILD_DIR}" --base-directory "${REPO_ROOT}" --no-external \
    --output-file "${OUTPUT_DIR}/baseline.info"

lcov --quiet --ignore-errors "${IGNORE}" --capture \
    --directory "${BUILD_DIR}" --base-directory "${REPO_ROOT}" --no-external \
    --output-file "${OUTPUT_DIR}/tests.info"

lcov --quiet --ignore-errors "${IGNORE}" \
    -a "${OUTPUT_DIR}/baseline.info" -a "${OUTPUT_DIR}/tests.info" \
    --output-file "${INFO}"

# The test code itself is not the code under test, and GoogleTest is not ours.
# `--no-external` above already dropped /usr and the toolchain headers.
lcov --quiet --ignore-errors "${IGNORE}" --remove "${INFO}" \
    "${REPO_ROOT}/tests/*" \
    "${REPO_ROOT}/build/*" \
    --output-file "${INFO}"

rm -f "${OUTPUT_DIR}/baseline.info" "${OUTPUT_DIR}/tests.info"

# `unmapped` and `inconsistent`: genhtml 2.0 refuses a report where a line
# record has no source line to attach to, which the same macro expansion that
# causes `mismatch` produces.
genhtml --quiet --ignore-errors "${IGNORE},unmapped,inconsistent" \
    --legend --demangle-cpp \
    --output-directory "${OUTPUT_DIR}/html" "${INFO}"

PERCENTAGE=$(python3 "${REPO_ROOT}/scripts/ci/coverage_badge.py" \
    --info "${INFO}" \
    --output "${OUTPUT_DIR}/coverage.svg" \
    --fail-under "${FAIL_UNDER}")

lcov --quiet --ignore-errors "${IGNORE}" --summary "${INFO}" || true

echo
echo "line coverage: ${PERCENTAGE}%"
echo "  tracefile:   ${INFO}"
echo "  html:        ${OUTPUT_DIR}/html/index.html"
echo "  badge:       ${OUTPUT_DIR}/coverage.svg"

# For a workflow step that wants the number without parsing this output.
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "percentage=${PERCENTAGE}" >> "${GITHUB_OUTPUT}"
fi
