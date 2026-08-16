ARG BASE_IMAGE=ghcr.io/intensivedatacomp/altx-cpp/base-cpu:edge
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

# `apt-get upgrade` for the same reason as in base.Dockerfile, and repeated
# rather than inherited: the two images have independent content hashes, so a
# rebuild of this one does not imply a rebuild of its parent. Without the line
# here, a dev image rebuilt six months after base-cpu last changed would ship
# base-cpu's six-month-old system packages.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ninja-build \
    git \
    gdb \
    libopenblas-openmp-dev \
    liblapacke-dev \
    libhdf5-dev \
    doxygen \
    graphviz \
    lcov \
    curl \
    wget \
    ca-certificates \
    bash-completion \
    && rm -rf /var/lib/apt/lists/*

# libomp-18-dev is not optional: clangd is clang, and clang does not search
# GCC's include directory, where the image's only omp.h otherwise lives. Without
# it every <omp.h> and every omp_* symbol is an error in the editor, while the
# GCC build succeeds -- the most confusing failure mode available.
RUN apt-get update && apt-get install -y --no-install-recommends \
    vim-nox \
    clangd-18 \
    clang-format-18 \
    clang-tidy-18 \
    libomp-18-dev \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/clangd clangd /usr/bin/clangd-18 100 && \
    update-alternatives --install /usr/bin/clang-format clang-format /usr/bin/clang-format-18 100 && \
    update-alternatives --install /usr/bin/clang-tidy clang-tidy /usr/bin/clang-tidy-18 100


# ---------------------------
# uv config
# ---------------------------
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv

# UV_LINK_MODE=copy is what makes `uv cache clean` safe further down: the
# default hardlinks installed files back to ~/.cache/uv, so deleting the cache
# would gut the installation it was meant to speed up.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

ARG UID=1000
ARG GID=1000
ARG USERNAME=non_root

# HOME must be a declared ENV, not merely the shell's idea of it. Docker expands
# only ARG/ENV in a COPY destination, so with HOME undeclared `COPY ... ${HOME}/`
# silently resolves to the filesystem root -- while ${HOME} in a RUN still works,
# because there the shell does the expanding.
ENV HOME=/home/${USERNAME}

# Vim initialises 'shell' from $SHELL. Bash sets SHELL as a shell variable but
# does not export it, so a child process such as vim never sees it and falls
# back to /bin/sh -- dash, which has no line editing and no tab completion.
# That is what makes `:vert term` feel broken. Exporting it here fixes :term,
# and anything else that spawns $SHELL.
ENV SHELL=/bin/bash

# ubuntu:24.04 already ships a user at uid/gid 1000. Removing it also removes
# its primary group, so the group has to be recreated before useradd can use it.
#
# `useradd -l` skips the lastlog/faillog entries, which are sparse files indexed
# by UID: at the default UID of 1000 they cost nothing, but a build overriding
# UID with a large value -- an LDAP account on a cluster, say -- would write a
# multi-gigabyte sparse file into the image layer, where it is not sparse.
RUN userdel -r ubuntu 2>/dev/null || true; \
    groupadd -g $GID $USERNAME && \
    useradd -l -m -u $UID -g $GID -s /bin/bash $USERNAME && \
    mkdir -p /workspace && chown $UID:$GID /workspace

# Everything below belongs to the user, not to root: the git aliases, the vim
# plugins and the clangd configuration are all read from $HOME at run time.
USER $USERNAME

# ----------------------------
# Git config
# ----------------------------
RUN git config --global --add safe.directory /workspace && \
    git config --global core.editor "vim" && \
    git config --global alias.lga "log --all --graph --decorate --oneline" && \
    git config --global alias.lg "log --graph --decorate --oneline" && \
    git config --global alias.lgac "log --all --graph --decorate --pretty=format:'%C(yellow)%h%Creset %C(green)%cn%Creset %C(auto)%d%Creset %C(white)%s%Creset'" && \
    git config --global alias.lgc "log --graph --decorate --pretty=format:'%C(yellow)%h%Creset %C(green)%cn%Creset %C(auto)%d%Creset %C(white)%s%Creset'"

# TODO: Add Intel VTune profiles + Score-P

# ---------------------------
# pre-commit
# ---------------------------
# The one reason this image carries Python at all. `uv tool install` rather than
# `uvx`: uvx resolves into an ephemeral cache directory, so the git hook that
# `pre-commit install` writes points at a path `uv cache clean` may delete, and
# every `git commit` afterwards fails with "`pre-commit` not found".
#
# `uv cache clean` in the same RUN, not a later one: a file deleted in a later
# layer still occupies the earlier one, so the size is only reclaimed when the
# creation and the deletion share a layer. (Trivy is the exception -- it scans
# the squashed filesystem -- but this is worth having for both reasons.)
RUN uv tool install pre-commit && uv cache clean

# uv puts tool shims here. Also what the generated git hook falls back to.
ENV PATH="${HOME}/.local/bin:${PATH}"

# Bake ~/.cache/pre-commit into a layer.
#
# pre-commit builds an isolated environment per hook on first use: a dozen
# virtualenvs, a Go toolchain for actionlint, and binary downloads for hadolint
# and shellcheck. Without this, that cost is paid inside every fresh container,
# over the network, before the first commit -- and paid again the next time one
# is started. `install-hooks` needs only the configuration, not the sources, so
# a throwaway repository is enough to populate the cache; the environments are
# keyed by hook repository and rev, so the real checkout reuses them.
#
# This is why .pre-commit-config.yaml is listed under `inputs` for dev-cpu in
# docker/images.yaml: it is baked in, so changing a `rev` has to rebuild the
# image. Without that entry the cache silently ages out of agreement with the
# configuration, which is the failure this layer exists to avoid.
#
# `install-hooks` reads the configuration from the working directory and insists
# on being inside a git repository, hence WORKDIR plus a throwaway `git init`
# rather than a bare `--config` flag. WORKDIR is restored immediately: the RUN
# deletes the directory it is standing in, and a later instruction inheriting a
# working directory that no longer exists fails the build.
COPY --chown=${UID}:${GID} .pre-commit-config.yaml /tmp/precommit/.pre-commit-config.yaml
#
# The pruning at the end of that RUN is not housekeeping. It is the whole
# reason this image passes its Trivy gate. `install-hooks` leaves behind the
# machinery it used to build the environments, and that machinery -- not the
# tools anyone runs -- is what carries the findings:
#
#   * pre-commit's `language: golang` builds `go install ./...`, so the
#     actionlint repository yields five binaries, of which the hook entry point
#     is one. `generate-webhook-events` is a code generator that nothing in this
#     image will ever run, and it is the only one of the five linked against
#     golang.org/x/net -- five HIGH findings from a binary with no purpose here.
#     It brought a 270 MB Go toolchain with it, which is likewise finished the
#     moment the binaries exist.
#   * every hook virtualenv is seeded with pip, and pip vendors its own
#     dependency tree (msgpack, setuptools/pkg_resources, cachecontrol, ...).
#     Nine copies of pip is nine copies of every advisory against those, for an
#     installer that has already done its job: pre-commit keys an environment
#     directory on the hook repository, its rev and its additional_dependencies,
#     so changing any of them builds a *new* environment with a fresh pip rather
#     than reusing this one.
#
# So: everything the RUN deletes is build-time-only by construction. What
# survives is the hook entry points and their libraries, which is what a
# container actually runs. Verified by scripts/build_docker_images_locally.sh,
# which runs the full hook suite against this repository in the finished image
# -- if a prune here ever takes something a hook needs, that smoke test is
# where it surfaces.
#
# It has to happen inside this RUN. A file deleted in a later layer is still
# present in the earlier one and still counts against the image size; only the
# Trivy result would improve.
#
# `! -name actionlint` names the one golang hook this repository has. A second
# one means adding its entry point here, and the smoke test is what says so.
# The closing assertion is the cheap version of that check, and it is written
# with `find -print -quit` rather than `find | head -1` for the reason
# runtime.Dockerfile records against DL4006: a pipe throws away find's own exit
# status, so a broken find would look like a passing test.
WORKDIR /tmp/precommit
RUN git init -q . && \
    pre-commit install-hooks && \
    rm -rf /tmp/precommit && \
    find "${HOME}/.cache/pre-commit" -path '*/golangenv-*/bin/*' \
        -type f ! -name actionlint -delete && \
    find "${HOME}/.cache/pre-commit" -type d -path '*/golangenv-*/.go' \
        -prune -exec rm -rf {} + && \
    find "${HOME}/.cache/pre-commit" -type d -path '*/site-packages/*' \
        \( -name pip -o -name 'pip-*.dist-info' \
           -o -name pkg_resources -o -name setuptools \
           -o -name 'setuptools-*.dist-info' \) -prune -exec rm -rf {} + && \
    find "${HOME}/.cache/pre-commit" -path '*/py_env-*/bin/pip*' -delete && \
    rm -rf "${HOME}/.cache/go-build" "${HOME}/.cache/pip" \
           "${HOME}/.cache/virtualenv" && \
    [ -n "$(find "${HOME}/.cache/pre-commit" \
        -path '*/golangenv-*/bin/actionlint' -type f -perm -u+x -print -quit)" ]
WORKDIR /workspace

# ---------------------------
# Python for people, not for pre-commit
# ---------------------------
# `python` and `python3` resolve to a uv-managed CPython 3.14 -- the version
# scripts/gen_reference.py and the docker-builder image already use, so a helper
# script behaves the same in both places. Ubuntu 24.04's own python3.12 stays
# where it is under /usr/bin; nothing is replaced, `${HOME}/.local/bin` simply
# comes first on PATH. `python` in particular does not otherwise exist here at
# all: Ubuntu ships no unversioned alias, and the 3.12 present at all is an
# accident of vim-nox depending on it.
#
# **This step must stay after the pre-commit layer.** `uv tool install` resolves
# the *default* interpreter, so installing 3.14 first would build pre-commit's
# tool environment on 3.14, and its hook environments with it -- turning the
# baked `py_env-python3.12` directories into cache misses that a fresh container
# would try to rebuild over the network. Some pinned hooks would not survive
# that: mypy 1.10.0 predates 3.14 and has no wheel for it.
#
# What makes this safe rather than merely ordered: pre-commit resolves its
# default interpreter from its own `sys.executable`, not from `python3` on PATH,
# so the shims below are invisible to it. Verified -- the hook environments stay
# `py_env-python3.12` and no hook reinstalls. smoke_precommit in
# scripts/build_docker_images_locally.sh runs the suite with `--network none`,
# which is what turns a regression here into a failed build instead of a slow
# first commit.
#
# The `pip` removal is the same finding as the pre-commit prune, arriving by a
# different route: a python-build-standalone interpreter ships pip *extracted*
# into site-packages, and trivy reads pip's vendored dependency list from there
# -- msgpack and setuptools, neither installed on its own account. Nothing here
# needs it. `uv pip install` is the documented way to install a package in this
# image and does not use pip at all, and `python -m venv` still works because
# ensurepip's bundled wheel is left alone. That wheel is also the way back:
# `python -m ensurepip` restores pip for anyone who genuinely wants it, which is
# why the wheel stays and only the extracted copy goes. Trivy does not look
# inside a .whl, so the wheel costs nothing.
ARG PYTHON_VERSION=3.14
RUN uv python install "${PYTHON_VERSION}" --default && \
    uv cache clean && \
    rm -rf "$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"/pip \
           "$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"/pip-*.dist-info && \
    test "$(python --version)" = "$(python3 --version)" && \
    python3 -c "import sys; assert sys.version_info[:2] == tuple(int(p) for p in '${PYTHON_VERSION}'.split('.')), sys.version"

# ---------------------------
# Vim config
# ---------------------------
RUN curl -fLo ${HOME}/.vim/autoload/plug.vim --create-dirs \
        https://raw.githubusercontent.com/junegunn/vim-plug/88e31471818e9a29a8a20a0ee61360cfd7bdc1cd/plug.vim
COPY --chown=${UID}:${GID} docker/vim/vimrc ${HOME}/.vimrc
RUN vim -es -u ${HOME}/.vimrc -i NONE -c "PlugInstall! --sync" -c qa

COPY --chown=${UID}:${GID} docker/vim/clangd-config.yaml $HOME/.config/clangd/config.yaml

# Set workdir
WORKDIR /workspace

# Set initial command
ENTRYPOINT ["/bin/bash"]
