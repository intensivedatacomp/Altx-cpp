ARG DEV_IMAGE=ghcr.io/intensivedatacomp/altx-cpp/dev-cpu:edge
ARG BASE_IMAGE=ghcr.io/intensivedatacomp/altx-cpp/base-cpu:edge

FROM ${DEV_IMAGE} AS builder
WORKDIR /src
COPY . .
RUN cmake --preset cpu-serial-release && cmake --build --preset cpu-serial-release && \
    cmake --preset cpu-omp-release    && cmake --build --preset cpu-omp-release

FROM ${BASE_IMAGE}
COPY --from=builder /src/build/cpu-serial-release/apps/altx /usr/local/bin/altx-serial
COPY --from=builder /src/build/cpu-omp-release/apps/altx    /usr/local/bin/altx-omp

RUN ldd /usr/local/bin/altx-omp | grep -q "not found" && exit 1 || true
