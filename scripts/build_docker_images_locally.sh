#!/usr/bin/env bash
#
# Build the Altx-cpp Docker images locally, in the same order and with the same
# build arguments that CI uses.
#
# The point is that CI is a second consumer of docker/*.Dockerfile rather than
# the only one: anything that breaks here breaks there. The layering is
#
#     base-<flavor>  ->  dev-<flavor>  ->  runtime-<flavor>
#
# where runtime is multi-stage (it compiles inside dev and copies the binaries
# into base), so each image must be built and tagged before the next one can
# refer to it. Locally that means overriding the BASE_IMAGE / DEV_IMAGE build
# arguments, whose defaults point at GHCR.
#
# Usage:
#   scripts/build_docker_images_locally.sh                  # base + dev, smoke tested
#   scripts/build_docker_images_locally.sh --target all     # ... and runtime
#   scripts/build_docker_images_locally.sh --target dev --no-cache
#   scripts/build_docker_images_locally.sh --skip-smoke
#
set -euo pipefail

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null) \
    || REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
DOCKER_DIR="${REPO_ROOT}/docker"

# Local-only namespace. Deliberately not ghcr.io/... so that a local image can
# never be mistaken for, or accidentally pushed as, a published one.
REGISTRY="${ALTX_LOCAL_REGISTRY:-altx-cpp}"
TAG="${ALTX_LOCAL_TAG:-local}"

FLAVOR=cpu
TARGET=dev          # base | dev | runtime | all
RUN_SMOKE=1
BUILD_ARGS=()
DOCKER_FLAGS=()

# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

if [[ -t 1 ]]; then
    C_HEAD=$'\033[1;36m'; C_OK=$'\033[1;32m'; C_WARN=$'\033[1;33m'
    C_ERR=$'\033[1;31m';  C_OFF=$'\033[0m'
else
    C_HEAD=''; C_OK=''; C_WARN=''; C_ERR=''; C_OFF=''
fi

step() { printf '\n%s==> %s%s\n' "$C_HEAD" "$*" "$C_OFF"; }
ok()   { printf '%s  ok%s   %s\n' "$C_OK"   "$C_OFF" "$*"; }
warn() { printf '%s  warn%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
die()  { printf '%s  error%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }

usage() {
    # Print the header comment block: everything after the shebang up to the
    # first line that is not a comment. Structural, so it cannot drift out of
    # sync the way a hardcoded line range does.
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
    cat <<'EOF'

Options:
  --flavor cpu|gpu      Image flavour (default: cpu). gpu is untested until stage 6.
  --target base|dev|runtime|all
                        How far up the chain to build (default: dev).
  --no-cache            Pass --no-cache to every docker build.
  --pull                Re-pull the pinned base image before building.
  --skip-smoke          Build only; do not run the post-build checks.
  --build-arg KEY=VALUE Forwarded to every docker build. Repeatable.
  -h, --help            This text.

Environment:
  ALTX_LOCAL_REGISTRY   Local image namespace (default: altx-cpp)
  ALTX_LOCAL_TAG        Local image tag       (default: local)
EOF
}

# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --flavor)     FLAVOR="${2:?--flavor needs a value}"; shift 2 ;;
        --target)     TARGET="${2:?--target needs a value}"; shift 2 ;;
        --no-cache)   DOCKER_FLAGS+=(--no-cache); shift ;;
        --pull)       DOCKER_FLAGS+=(--pull); shift ;;
        --skip-smoke) RUN_SMOKE=0; shift ;;
        --build-arg)  BUILD_ARGS+=(--build-arg "${2:?--build-arg needs a value}"); shift 2 ;;
        -h|--help)    usage; exit 0 ;;
        *)            die "unknown option: $1 (try --help)" ;;
    esac
done

case "$FLAVOR" in cpu|gpu) ;; *) die "--flavor must be cpu or gpu, got '$FLAVOR'" ;; esac
case "$TARGET" in base|dev|runtime|all) ;; *) die "--target must be base, dev, runtime or all" ;; esac

BASE_IMG="${REGISTRY}/base-${FLAVOR}:${TAG}"
DEV_IMG="${REGISTRY}/dev-${FLAVOR}:${TAG}"
RUNTIME_IMG="${REGISTRY}/runtime-${FLAVOR}:${TAG}"

# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------

step "Preflight"

command -v docker >/dev/null 2>&1 || die "docker is not on PATH"
docker info >/dev/null 2>&1 || die "cannot reach the Docker daemon (is it running, and are you in the 'docker' group?)"
ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"

# BuildKit is strongly preferred, and not only because the legacy builder is
# deprecated in Docker 29: base.Dockerfile picks its base with
# `FROM base-${FLAVOR}`, and the legacy builder resolves *every* FROM in the
# file, so a cpu build still has to parse the gpu base reference. BuildKit
# skips stages that nothing depends on. `# syntax=` directives need it too.
if docker buildx version >/dev/null 2>&1; then
    export DOCKER_BUILDKIT=1
    ok "buildx present -- using BuildKit"
else
    export DOCKER_BUILDKIT=0
    warn "buildx not installed; falling back to the deprecated legacy builder."
    warn "  every FROM must then be a valid reference, including unused ones."
    warn "  install it with:  sudo apt-get install docker-buildx"
fi

# The Dockerfiles do not exist yet at development stage 1. Fail with the file
# name rather than with docker's less specific error.
need_file() {
    [[ -f "$1" ]] || die "missing $1 -- has it been written yet? (see DevelopmentPlan.md, 'Build structure')"
}

need_file "${DOCKER_DIR}/base.Dockerfile"
if [[ "$TARGET" != base ]]; then
    need_file "${DOCKER_DIR}/dev.Dockerfile"
fi
if [[ "$TARGET" == runtime || "$TARGET" == all ]]; then
    need_file "${DOCKER_DIR}/runtime.Dockerfile"
fi
ok "Dockerfiles present"

# Build context is the repository root for all three images: runtime needs the
# sources, and dev needs docker/vim/. One rule is easier to keep true than two.
[[ -f "${REPO_ROOT}/.dockerignore" ]] \
    || warn "no .dockerignore at the repository root -- the whole tree is sent as build context"

# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

# Match the invoking user so bind-mounted files are not root-owned.
COMMON_ARGS=(
    --build-arg "UID=$(id -u)"
    --build-arg "GID=$(id -g)"
)

build() {
    local dockerfile="$1" image="$2"; shift 2
    step "Building ${image}"
    docker build \
        -f "${DOCKER_DIR}/${dockerfile}" \
        -t "${image}" \
        "${DOCKER_FLAGS[@]}" \
        "${COMMON_ARGS[@]}" \
        "${BUILD_ARGS[@]}" \
        "$@" \
        "${REPO_ROOT}"
    # `docker images` reports the unpacked on-disk size. `docker image inspect
    # -f {{.Size}}` disagrees with it substantially under Docker 29 and
    # understates what the image actually costs, so prefer the former.
    ok "${image}  ($(docker images --format '{{.Size}}' --filter "reference=${image}" | head -1))"
}

build base.Dockerfile "${BASE_IMG}" --build-arg "FLAVOR=${FLAVOR}"

if [[ "$TARGET" != base ]]; then
    build dev.Dockerfile "${DEV_IMG}" --build-arg "BASE_IMAGE=${BASE_IMG}"
fi

if [[ "$TARGET" == runtime || "$TARGET" == all ]]; then
    if [[ -f "${REPO_ROOT}/CMakeLists.txt" ]]; then
        build runtime.Dockerfile "${RUNTIME_IMG}" \
            --build-arg "DEV_IMAGE=${DEV_IMG}" \
            --build-arg "BASE_IMAGE=${BASE_IMG}"
    else
        warn "no CMakeLists.txt yet -- skipping ${RUNTIME_IMG} (nothing to compile or copy)"
        TARGET=dev
    fi
fi

# --------------------------------------------------------------------------
# Smoke tests
# --------------------------------------------------------------------------
#
# These check the things that fail silently: an image that builds fine but
# resolves BLAS to the wrong OpenBLAS variant, or ships a clangd that cannot
# find the headers the editor is supposed to complete.

in_image() { docker run --rm --entrypoint /bin/bash "$1" -lc "$2"; }

smoke_base() {
    step "Smoke: ${BASE_IMG}"

    # Plan, 'Threading: parallelism at one level only': the pthread build of
    # OpenBLAS degrades or deadlocks when called from an OpenMP region, and it
    # is what the libopenblas-dev meta-package installs by default. Assert the
    # alternative resolves to the openmp build instead.
    local resolved
    resolved=$(in_image "${BASE_IMG}" \
        'readlink -f /usr/lib/x86_64-linux-gnu/libblas.so.3' | tr -d '\r')
    if [[ "$resolved" == *openblas-openmp* ]]; then
        ok "BLAS -> ${resolved}"
    else
        die "BLAS resolves to '${resolved}', expected the openblas-openmp build"
    fi

    for lib in libopenblas libhdf5 libgomp liblapacke; do
        if in_image "${BASE_IMG}" "ldconfig -p | grep -q ${lib}"; then
            ok "${lib} present"
        else
            die "${lib} missing from the runtime image"
        fi
    done
}

smoke_dev() {
    step "Smoke: ${DEV_IMG}"

    for tool in g++ cmake ninja gdb git vim clangd clang-format doxygen uv; do
        local version
        if version=$(in_image "${DEV_IMG}" "command -v ${tool} >/dev/null && ${tool} --version 2>&1 | head -1"); then
            # Not `A && B || C`: if `ok` ever returns non-zero, C would run too.
            if [[ -n "$version" ]]; then
                ok "${tool}: ${version}"
            else
                die "${tool} not found"
            fi
        else
            die "${tool} not found in ${DEV_IMG}"
        fi
    done

    # The real test: a translation unit that includes and links all four CPU
    # libraries. Verified to compile and run on Ubuntu 24.04 with exactly these
    # flags. Of the four, only HDF5 needs an explicit -I; OpenBLAS and LAPACKE
    # sit on the default include path and omp.h comes with GCC behind -fopenmp.
    step "Smoke: compiling the four-library probe in ${DEV_IMG}"
    in_image "${DEV_IMG}" '
        set -e
        cat > /tmp/probe.cpp <<"PROBE"
#include <cblas.h>
#include <lapacke.h>
#include <hdf5.h>
#include <omp.h>
#include <cstdio>

int main() {
    double a[4] = {1, 0, 0, 1}, b[4] = {1, 2, 3, 4}, c[4] = {0, 0, 0, 0};
    cblas_dgemm(CblasRowMajor, CblasNoTrans, CblasNoTrans, 2, 2, 2, 1.0, a, 2, b, 2, 0.0, c, 2);
    double s[4] = {2, 1, 1, 2}, w[2];
    LAPACKE_dsyev(LAPACK_ROW_MAJOR, "V"[0], "U"[0], 2, s, 2, w);
    hid_t f = H5Fcreate("/tmp/probe.h5", H5F_ACC_TRUNC, H5P_DEFAULT, H5P_DEFAULT);
    H5Fclose(f);
    printf("gemm=%.1f eig=%.1f threads=%d\n", c[3], w[0], omp_get_max_threads());
    return 0;
}
PROBE
        g++ -std=c++20 -fopenmp -I/usr/include/hdf5/serial /tmp/probe.cpp -o /tmp/probe \
            -L/usr/lib/x86_64-linux-gnu/hdf5/serial -lhdf5 -llapacke -lopenblas
        /tmp/probe
    ' | while read -r line; do ok "probe: ${line}"; done

    # And the same headers through clangd, which is what the editor actually
    # runs. Without ~/.config/clangd/config.yaml supplying
    # -I/usr/include/hdf5/serial, and without --query-driver pointing clangd's
    # clang at GCC's libstdc++, this is where it shows up. Each in_image call is
    # a fresh container, so the probe is written again here.
    step "Smoke: clangd resolves the four headers in ${DEV_IMG}"

    local check_out
    check_out=$(in_image "${DEV_IMG}" '
        printf "#include <cblas.h>\n#include <lapacke.h>\n#include <hdf5.h>\n#include <omp.h>\nint main() { return omp_get_max_threads(); }\n" > /tmp/lsp.cpp
        clangd --check=/tmp/lsp.cpp --query-driver=/usr/bin/g++* 2>&1
    ' || true)

    # Count tagged diagnostics, not clangd's own summary. The summary counts
    # every log line at error level, which on Ubuntu 24.04 includes a benign
    #   IncludeCleaner: Failed to get an entry for resolved path
    # that <omp.h> triggers and that no Diagnostics: setting suppresses. Real
    # problems look like
    #   E[..] [pp_file_not_found] Line 1: 'hdf5.h' file not found
    local diagnostics
    diagnostics=$(printf '%s\n' "$check_out" | grep -E '^E\[[^]]*\] \[[a-z_]+\] Line ' || true)

    if [[ -z "$check_out" || "$check_out" != *"All checks completed"* ]]; then
        warn "'clangd --check' produced no recognisable summary; inspect manually:"
        printf '%s\n' "$check_out" | tail -20
    elif [[ -n "$diagnostics" ]]; then
        printf '%s\n' "$diagnostics"
        die "clangd could not parse the probe -- check ~/.config/clangd/config.yaml and --query-driver"
    else
        ok "clangd: all four headers resolve, no diagnostics"
    fi
}

# The pre-commit cache is baked into dev-cpu and then pruned in the same layer,
# because most of what `install-hooks` downloads -- a Go toolchain, a `pip` per
# virtualenv -- is build-time machinery that would otherwise sit in the image
# carrying advisories against it. That prune is the one edit in these
# Dockerfiles that can break a hook without breaking the build, so run the
# whole suite in the finished image and let it say so here rather than in
# someone's first commit.
#
# The repository is copied in rather than bind-mounted read-only: several hooks
# rewrite files (trailing-whitespace, ruff format), and `run --all-files` needs
# a git repository it may touch. A failing hook is not the same as a broken
# image, so a non-zero exit is reported and not fatal -- what matters is that
# every hook *ran*.
smoke_precommit() {
    step "Smoke: pre-commit hooks run offline in ${DEV_IMG}"

    local out status=0
    out=$(docker run --rm --network none \
            -v "${REPO_ROOT}:/repo:ro" --entrypoint /bin/bash "${DEV_IMG}" -lc '
        set -e
        cp -r /repo /tmp/w && cd /tmp/w
        git config --global user.email smoke@localhost
        git config --global user.name  smoke
        [ -d .git ] || git init -q .
        git add -A >/dev/null 2>&1 || true
        pre-commit run --all-files
    ' 2>&1) || status=$?

    # `--network none` is the actual assertion. An environment the prune damaged
    # makes pre-commit try to rebuild it, which without a network fails loudly
    # instead of silently repairing itself and hiding the defect.
    local ran
    ran=$(printf '%s\n' "$out" | grep -cE '(Passed|Failed|Skipped)$' || true)

    if printf '%s\n' "$out" | grep -qiE 'Installing environment|InstallError|executable .* not found'; then
        printf '%s\n' "$out" | tail -30
        die "a hook environment did not survive the cache prune in docker/dev.Dockerfile"
    elif (( ran == 0 )); then
        printf '%s\n' "$out" | tail -30
        die "no hooks ran -- pre-commit could not start inside ${DEV_IMG}"
    elif (( status != 0 )); then
        ok "${ran} hooks ran offline"
        warn "some hooks reported findings (exit ${status}); that is a repository issue, not an image one:"
        printf '%s\n' "$out" | grep -E 'Failed$' || true
    else
        ok "${ran} hooks ran offline, all passed"
    fi
}

smoke_runtime() {
    step "Smoke: ${RUNTIME_IMG}"

    for binary in altx-serial altx-omp; do
        in_image "${RUNTIME_IMG}" "command -v ${binary} >/dev/null" \
            || die "${binary} missing from ${RUNTIME_IMG}"
        # A -dev-only library that leaked into the link shows up here, not at
        # build time -- which is the failure the base/dev/runtime split exists
        # to catch.
        if in_image "${RUNTIME_IMG}" "ldd \$(command -v ${binary}) | grep -q 'not found'"; then
            in_image "${RUNTIME_IMG}" "ldd \$(command -v ${binary}) | grep 'not found'"
            die "${binary} has unresolved shared libraries"
        fi
        ok "${binary}: $(in_image "${RUNTIME_IMG}" "${binary} --version 2>&1 | head -1")"
    done
}

if (( RUN_SMOKE )); then
    smoke_base
    if [[ "$TARGET" != base ]]; then
        smoke_dev
        smoke_precommit
    fi
    if [[ "$TARGET" == runtime || "$TARGET" == all ]]; then
        smoke_runtime
    fi
else
    warn "smoke tests skipped (--skip-smoke)"
fi

# --------------------------------------------------------------------------

step "Done"
docker images --filter "reference=${REGISTRY}/*:${TAG}" \
    --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}'

cat <<EOF

Open a development shell with the repository mounted:

  docker run --rm -it -v "${REPO_ROOT}:/workspace" -w /workspace ${DEV_IMG}
EOF
