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
#
# APT_SNAPSHOT is what makes that upgrade mean anything, and the weekly
# `force_rebuild` run of .github/workflows/docker-images.yml is what supplies a
# current value for it. The date below is a floor, not a schedule: editing it
# forces a refresh on this *tree* by changing the content hash, which is the
# case the weekly run cannot serve. See docker/dev.Dockerfile, where the
# argument is spelled out at length and where the failure that motivated it
# happened.
#
# 2026-09-13: libc6 and libc-bin were pinned at 2.39-0ubuntu8.8 while the
# archive had 8.9, which is six fixed glibc advisories the images were carrying
# for no reason. They are MEDIUM, so the CRITICAL/HIGH gate never fired -- the
# signal came from the Security tab, where the SARIF upload deliberately
# reports every severity. Noticing that by hand is precisely what the weekly
# run now removes.
ARG APT_SNAPSHOT=2026-09-13
RUN echo "apt snapshot ${APT_SNAPSHOT}" && \
    apt-get update && apt-get upgrade -y --no-install-recommends \
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

# The description shown on the GHCR package page and by `docker inspect`. The
# text lives in docker/images.yaml, next to the image it describes, and arrives
# as a build argument -- from scripts/ci/images.py in CI, and from
# scripts/build_docker_images_locally.sh locally -- because this Dockerfile
# builds more than one image, and a literal here could be right for only one of
# base-cpu and base-gpu. dev.Dockerfile and runtime.Dockerfile do the same.
#
# Last, and in every Dockerfile: an ARG takes part in the cache key of each RUN
# that follows it, so declared any earlier, rewording a description would
# rebuild the whole image instead of one metadata-only step.
#
# No default, deliberately. Labels are inherited, so a Dockerfile that did not
# set one would ship its parent's description; a bare `docker build` getting an
# empty description is less wrong than getting someone else's.
ARG IMAGE_DESCRIPTION
LABEL org.opencontainers.image.description="${IMAGE_DESCRIPTION}"
