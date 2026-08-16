ARG FLAVOR=cpu
ARG BASE_CPU=ubuntu:24.04@sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea
ARG BASE_GPU=rocm/dev-ubuntu-24.04@sha256:439edaa8f0c4be4a3728e528f87b8a2ea1f051f34cf10b27caa4bd94f562eda7

FROM ${BASE_CPU} AS base-cpu
FROM ${BASE_GPU} AS base-gpu
FROM base-${FLAVOR} AS final

ENV DEBIAN_FRONTEND=noninteractive

# `apt-get upgrade` before the install, and hadolint's DL3005 is silenced for
# it rather than obeyed. The rule assumes an upgrade makes a build
# irreproducible; here the opposite is true. The base image is pinned by
# digest, so it is frozen at whatever noble-updates and noble-security held on
# the day that digest was published -- typically months of unapplied fixes in
# packages this image never installs and therefore never refreshes. That is
# exactly where the libsystemd0 / libudev1 / libkrb5 findings came from.
# Reproducibility is not lost, because it was never bought here: DL3008 is
# already silenced for the same reason (Ubuntu keeps one version per package),
# and the real guarantee is that scripts/ci/images.py hashes this file byte for
# byte, so the image identity tracks its contents.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
        libopenblas0-openmp \
        liblapacke \
        libhdf5-103-1t64 \
        libgomp1 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Test if BLAS uses openmp rather than phreads
RUN update-alternatives --set libopenblas.so.0-x86_64-linux-gnu \
        /usr/lib/x86_64-linux-gnu/openblas-openmp/libopenblas.so.0 && \
    test "$(readlink -f /usr/lib/x86_64-linux-gnu/libblas.so.3)" \
       = "/usr/lib/x86_64-linux-gnu/openblas-openmp/libblas.so.3"
