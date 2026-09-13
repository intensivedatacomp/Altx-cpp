ARG DEV_IMAGE=ghcr.io/intensivedatacomp/altx-cpp/dev-cpu:edge
ARG BASE_IMAGE=ghcr.io/intensivedatacomp/altx-cpp/base-cpu:edge

FROM ${DEV_IMAGE} AS builder
WORKDIR /src
COPY . .

# `COPY` writes the tree as root, while the dev image runs as non_root, and git
# refuses a repository it does not own -- "detected dubious ownership". Without
# this line the build still succeeds: cmake/GitVersion.cmake falls back to an
# `unknown` version, and the released binaries carry `unknown` in the
# provenance of every result file they write. That is precisely the silent
# mislabelling the derived-version rule exists to prevent, which is also why
# .dockerignore deliberately keeps .git in the context.
#
# `--global` rather than `--add safe.directory '*'`: the exception is for this
# one path, in a throwaway build stage.
RUN git config --global --add safe.directory /src

# `-DALTX_ENABLE_TESTS=OFF` on both: the tests are CI's job -- they run in
# dev-cpu, across the whole preset matrix, in .github/workflows/build-test.yml
# -- and nothing they produce reaches this image. Leaving them on would make
# every release image build download and compile GoogleTest, so the image would
# also stop building the day the network is unavailable, for output it discards.
RUN cmake --preset cpu-serial-release -DALTX_ENABLE_TESTS=OFF && \
    cmake --build --preset cpu-serial-release && \
    cmake --preset cpu-omp-release -DALTX_ENABLE_TESTS=OFF && \
    cmake --build --preset cpu-omp-release

FROM ${BASE_IMAGE}
COPY --from=builder /src/build/cpu-serial-release/apps/altx /usr/local/bin/altx-serial
COPY --from=builder /src/build/cpu-omp-release/apps/altx    /usr/local/bin/altx-omp

# Fail the build if the runtime image is missing a shared library the binary
# needs -- the whole point of a thin runtime stage is that this is easy to get
# wrong.
#
# Written to a file rather than piped into grep, which is what hadolint's DL4006
# was pointing at, and the pipe was hiding a real hole: `ldd ... | grep -q`
# discards ldd's own exit status, so if ldd itself failed -- missing binary,
# not a dynamic executable -- grep saw empty input, found nothing, and the
# `|| true` reported success. The case the check exists to catch was the case it
# passed. `-o pipefail` alone would not have fixed that, since the `&& exit 1 ||
# true` swallows a non-zero pipeline either way.
RUN ldd /usr/local/bin/altx-omp > /tmp/ldd.txt \
    && ! grep -q "not found" /tmp/ldd.txt \
    && rm /tmp/ldd.txt
