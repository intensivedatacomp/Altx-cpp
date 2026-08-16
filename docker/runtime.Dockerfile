ARG DEV_IMAGE=ghcr.io/intensivedatacomp/altx-cpp/dev-cpu:edge
ARG BASE_IMAGE=ghcr.io/intensivedatacomp/altx-cpp/base-cpu:edge

FROM ${DEV_IMAGE} AS builder
WORKDIR /src
COPY . .
RUN cmake --preset cpu-serial-release && cmake --build --preset cpu-serial-release && \
    cmake --preset cpu-omp-release    && cmake --build --preset cpu-omp-release

FROM ${BASE_IMAGE}
LABEL org.opencontainers.image.description="Minimal runtime image for the Altx C++ project: the compiled altx binaries (serial and OpenMP) on top of base-cpu."
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
