ARG BASE_IMAGE=ghcr.io/intensivedatacomp/altx-cpp/base-cpu:edge
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
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
RUN uv tool install pre-commit

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
WORKDIR /tmp/precommit
RUN git init -q . && \
    pre-commit install-hooks && \
    rm -rf /tmp/precommit
WORKDIR /workspace

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
