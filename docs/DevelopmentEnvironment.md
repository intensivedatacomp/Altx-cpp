@page development_environment Development Environment

@brief The Docker images, the ways of running them, and the editor tooling they carry.

All development happens inside a container, so that every developer and every CI job use the same
compiler, the same libraries and the same editor configuration. This page covers the
configurations beyond the plain interactive session shown in the README, and how to use what is
inside the image.

[TOC]

@section devenv_images The images

| Image             | Contains                                                     | Status                |
| ----------------- | ------------------------------------------------------------ | --------------------- |
| `base-cpu`        | OpenBLAS (openmp build), LAPACKE, serial HDF5, libgomp        | built                 |
| `dev-cpu`         | `base-cpu` + toolchain, Vim, clangd, gdb, Doxygen, uv         | built                 |
| `runtime-cpu`     | `base-cpu` + `altx-serial`, `altx-omp`                        | needs `CMakeLists.txt`|
| `runtime-cpu-mpi` | + OpenMPI, parallel HDF5                                       | development stage 5   |
| `dev-gpu`, `runtime-gpu` | + ROCm                                                  | development stage 6   |

`runtime-*` images are produced by compiling inside `dev-*` and copying the binaries into `base-*`,
so "builds in the dev container" and "builds in CI" are the same statement by construction.

@section devenv_building Building the images

```bash
./scripts/build_docker_images_locally.sh              # base + dev, smoke tested
./scripts/build_docker_images_locally.sh --target all # ... and runtime
```

| Option                  | Effect                                                          |
| ----------------------- | --------------------------------------------------------------- |
| `--target base\|dev\|runtime\|all` | How far up the chain to build. Default `dev`.          |
| `--flavor cpu\|gpu`     | Image flavour. Default `cpu`.                                     |
| `--no-cache`            | Rebuild every layer.                                              |
| `--pull`                | Re-pull the digest-pinned base image.                             |
| `--skip-smoke`          | Build only, skip the post-build checks.                           |
| `--build-arg KEY=VALUE` | Forwarded to every `docker build`. Repeatable.                    |

Two environment variables change the local image names: `ALTX_LOCAL_REGISTRY` (default
`altx-cpp`) and `ALTX_LOCAL_TAG` (default `local`). They exist so a local build can never be
confused with, or pushed as, a published image.

The smoke tests are the reason to prefer the script over a bare `docker build`. They assert the
things that fail silently: that BLAS resolves to the **openmp** build of OpenBLAS rather than the
pthread one, that a translation unit including all four libraries compiles and links, and that
`clangd` resolves the same four headers. A build that succeeds but fails a smoke test is the
normal way this image breaks.

@note `docker-buildx` must be installed. Without it Docker falls back to the legacy builder,
which resolves every `FROM` in `base.Dockerfile` — including the GPU stage — and so pulls several
gigabytes of ROCm during a CPU build.

@section devenv_running Useful ways to run the container

**Interactive session** — the everyday command, from the repository root:

```bash
docker run --rm -it -v "$PWD:/workspace" -w /workspace altx-cpp/dev-cpu:local
```

**A single command**, without an interactive shell. Useful for scripting and for CI:

```bash
docker run --rm -v "$PWD:/workspace" -w /workspace altx-cpp/dev-cpu:local \
    -c "cmake --preset cpu-omp-release && cmake --build --preset cpu-omp-release"
```

The entry point is `bash`, so arguments are passed to it — hence the `-c`.

**Matching your host account.** The image is built for `uid:gid 1000:1000`. If `id -u` reports
something else, files you create in the mounted repository will have the wrong owner. Rebuild with
matching ids:

```bash
./scripts/build_docker_images_locally.sh --target dev \
    --build-arg "UID=$(id -u)" --build-arg "GID=$(id -g)"
```

**Datasets outside the repository** are best mounted read-only, so a mistake in an experiment
cannot damage the input:

```bash
docker run --rm -it -v "$PWD:/workspace" -v /data/timeseries:/data:ro \
    -w /workspace altx-cpp/dev-cpu:local
```

**Controlling threads.** OpenMP parallelises over windows and instances while BLAS runs
single-threaded beneath it. When benchmarking, set the thread count explicitly rather than letting
the container see every core on the machine:

```bash
docker run --rm -it --cpus 8 -e OMP_NUM_THREADS=8 \
    -v "$PWD:/workspace" -w /workspace altx-cpp/dev-cpu:local
```

**Keeping the shell history** between sessions, which `--rm` otherwise discards:

```bash
touch ~/.altx_bash_history
docker run --rm -it -v "$PWD:/workspace" \
    -v "$HOME/.altx_bash_history:/home/non_root/.bash_history" \
    -w /workspace altx-cpp/dev-cpu:local
```

A host file works here because it keeps your uid, which the container user shares. Two
alternatives that look simpler both fail:

@warning Do not mount a volume over `/home/non_root` itself. A volume over the whole home
directory is initialised from the image once and then never updated, so it would pin the Vim
configuration, the installed plugins and the clangd config to whatever the image contained on the
first run, and later image rebuilds would appear to have no effect.

@warning A *named* volume at a path that does not exist in the image is created owned by `root`,
so `non_root` cannot write to it and the history is silently lost. Bind-mount a host file, or add
the directory to the Dockerfile with the right owner first.

@section devenv_editor Editor tooling

Autocomplete and diagnostics come from **clangd**, driven by `compile_commands.json`, which CMake
emits when configured with `CMAKE_EXPORT_COMPILE_COMMANDS=ON`. Because clangd then sees the real
include paths and defines of the real build, OpenMP, HDF5, BLAS and LAPACK all work with no
per-library editor configuration.

Two pieces make that work inside the container, and both are invisible until they fail:

- clangd is clang, but the build is GCC. `--query-driver=/usr/bin/g++*,/usr/bin/gcc*` lets clangd
  ask GCC where its `libstdc++` lives. Without it every line is an error about
  `bits/c++config.h`.
- Before CMake has ever been configured there is no compile database at all. The image therefore
  ships `~/.config/clangd/config.yaml`, which adds `-std=c++20`, `-fopenmp` and
  `-I/usr/include/hdf5/serial`. Of the four libraries only serial HDF5 needs an explicit include
  path; OpenBLAS and LAPACKE sit on the default path, and `omp.h` comes from `libomp-18-dev`.

Once there are several build directories, select one explicitly with a `.clangd` file at the
repository root, rather than relying on whichever `cmake --preset` ran last:

```yaml
CompileFlags:
  CompilationDatabase: build/cpu-omp-debug
```

clangd writes its index to `.cache/clangd` in the workspace; `.gitignore` already covers it.

@section devenv_vim Vim

@subsection devenv_vim_lsp Autocomplete and code navigation

`vim-lsp` provides the LSP client and `asyncomplete.vim` the as-you-type popup. Completion appears
while typing; `<C-n>` and `<C-p>` move through the candidates, `<C-y>` accepts one and `<C-e>`
dismisses the popup.

The plugins define around forty commands. These are the ones worth knowing:

| Command                  | What it does                                              |
| ------------------------ | ---------------------------------------------------------- |
| `:LspDefinition`         | Jump to the definition under the cursor                     |
| `:LspPeekDefinition`     | Show it in a preview window without leaving the buffer      |
| `:LspDeclaration`        | Jump to the declaration                                     |
| `:LspReferences`         | List all references                                         |
| `:LspHover`              | Type and documentation of the symbol under the cursor       |
| `:LspSignatureHelp`      | Parameter hints for the call being typed                    |
| `:LspRename`             | Rename a symbol across the project                          |
| `:LspDocumentSymbol`     | Outline of the current file                                 |
| `:LspWorkspaceSymbol`    | Search symbols across the project                           |
| `:LspDocumentDiagnostics`| All diagnostics for the file in a location list             |
| `:LspNextError`, `:LspPreviousError` | Move between errors                             |
| `:LspCodeAction`         | Apply a clangd fix-it                                       |
| `:LspStatus`             | Whether the server is running — the first thing to check    |

@note No key mappings are bound to these by default; they are invoked by name. If you use them
often, adding mappings such as `nnoremap gd :LspDefinition<CR>` and `nnoremap K :LspHover<CR>` to
`docker/vim/vimrc` is worthwhile.

@note `completeopt` is at Vim's default `menu,preview`. `asyncomplete` behaves better with
`set completeopt=menuone,noinsert,noselect`, which shows the popup even when there is a single
match and stops Vim inserting a candidate before you have chosen one.

@subsection devenv_vim_fugitive Git

`vim-fugitive` provides Git integration. `:Git` with no arguments opens an interactive status
window, where `s` stages, `u` unstages and `cc` starts a commit. `:Git <anything>` runs an
arbitrary Git command. `:Gdiffsplit` diffs the current file against the index, `:Gvdiffsplit` does
it in a vertical split, and `:Git blame` opens an aligned blame pane.

Merge conflicts are what this is really for. On a conflicted file, `:Gdiffsplit!` opens the three
versions side by side, and the vimrc binds:

- `ml` — take the version from **our** side (`:diffget //2`)
- `mr` — take the version from **their** side (`:diffget //3`)

The `DiffAdd`, `DiffDelete`, `DiffChange` and `DiffText` highlight groups are set in the vimrc, so
diffs stay readable.

@subsection devenv_vim_term The terminal

`:term` opens a shell in a split, `:vert term` in a vertical one. `<C-w>` window commands move
between it and the code, and `<Esc><Esc>` leaves terminal-insert mode so those work.

The shell is `bash` with `bash-completion` installed, so tab completion covers Git subcommands,
CMake options and file paths. This needs `SHELL` to be exported in the environment — Vim reads
`'shell'` from it, and bash sets the variable without exporting it, so a container missing
`ENV SHELL=/bin/bash` would silently run `/bin/sh` (dash) here, with no completion at all.

@subsection devenv_vim_general General settings

Four-space indentation with `expandtab`; incremental, highlighted search that is case-insensitive
unless the pattern contains an uppercase letter (`:noh` clears the highlight); `wildmenu` for
command-line completion; and true colour where the terminal supports it.

@section devenv_trouble Troubleshooting

| Symptom                                              | Cause                                                        |
| ---------------------------------------------------- | ------------------------------------------------------------ |
| Every line red, `bits/c++config.h` not found          | clangd started without `--query-driver`                       |
| `omp.h` not found, but `g++` compiles fine            | `libomp-18-dev` missing: clang does not search GCC's headers  |
| Completion works in one file, not another             | That file has no `compile_commands.json` entry — reconfigure  |
| No completion in `:term`                              | `$SHELL` not exported, so the terminal is running dash        |
| Files in the repository owned by the wrong user       | Image built with a `UID`/`GID` that is not yours              |
| A CPU build pulling gigabytes of ROCm                 | `docker-buildx` not installed; legacy builder resolves all stages |

`:LspStatus` answers most editor questions directly, and `clangd --check=<file>` outside Vim shows
exactly which flags clangd used and which includes it failed to resolve.
