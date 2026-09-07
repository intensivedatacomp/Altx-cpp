@page development_environment Development Environment

@brief The Docker images, the ways of running them, and the editor tooling they carry.

All development happens inside a container, so that every developer and every CI job use the same
compiler, the same libraries and the same editor configuration. This page covers the
configurations beyond the plain interactive session shown in the README, and how to use what is
inside the image.

The automated checks the image carries — `pre-commit` and everything it drives — are documented
separately in @ref code_quality.

[TOC]

@section devenv_images The images

| Image             | Contains                                                     | Status                |
| ----------------- | ------------------------------------------------------------ | --------------------- |
| `base-cpu`        | OpenBLAS (openmp build), LAPACKE, serial HDF5, libgomp        | built                 |
| `dev-cpu`         | `base-cpu` + toolchain, Vim, clangd, gdb, Doxygen, uv, pre-commit | built             |
| `runtime-cpu`     | `base-cpu` + `altx-serial`, `altx-omp`                        | needs `CMakeLists.txt`|
| `runtime-cpu-mpi` | + OpenMPI, parallel HDF5                                       | development stage 5   |
| `dev-gpu`, `runtime-gpu` | + ROCm                                                  | development stage 6   |

`runtime-*` images are produced by compiling inside `dev-*` and copying the binaries into `base-*`,
so "builds in the dev container" and "builds in CI" are the same statement by construction.

@section devenv_published Published images

CI publishes to GHCR under `ghcr.io/intensivedatacomp/altx-cpp/`, split into two kinds of package.
The one to pull from is the per-image package, which carries only tags meant to be typed:

```bash
docker pull ghcr.io/intensivedatacomp/altx-cpp/dev-cpu:edge     # newest build of main
docker pull ghcr.io/intensivedatacomp/altx-cpp/dev-cpu:v1.2.3   # a release
docker pull ghcr.io/intensivedatacomp/altx-cpp/dev-cpu:latest   # newest release
```

The second package, `altx-cpp/buildcache`, holds the machine-facing tags for **every** image —
`dev-cpu-hash-<digest>` (the content-addressed tag CI pins), `dev-cpu-sha-<commit>` (one per
build, for reproducing a specific run) and `dev-cpu-cache` (the buildx layer cache). They live
together so the per-image packages stay readable, and each is prefixed with the image name because
tags are unique within a package.

Reach into `buildcache` only to reproduce a particular CI run. `scripts/ci/images.py` prints the
reference for the current working tree, which is the same one CI computes:

```bash
python3 scripts/ci/images.py ref dev-cpu       # ghcr.io/…/buildcache:dev-cpu-hash-7d6016832ae9
python3 scripts/ci/images.py plan              # the whole resolved matrix
```

Images are built on every push to `main`, on `v*` tags, on pull requests, and on any branch whose
name contains `docker` — image work is the case where waiting for a pull request to find out that
a Dockerfile broke is the most expensive. A build is skipped entirely when an image with the same
content hash already exists, so most of those runs cost only the hash computation.

`edge` is refreshed by a push to `main` **and** by a push to a `*docker*` branch, so the image
being worked on is pullable by name while the work is happening. It can therefore point at
unmerged work; if you need to know exactly what you have, read
`org.opencontainers.image.revision` from the image:

```bash
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
    ghcr.io/intensivedatacomp/altx-cpp/dev-cpu:edge
```

`latest` and `vX.Y.Z` are written only by a `v*` tag, and a pull request build writes no moving
tag at all.

After a successful build, a `prune` job deletes what that build superseded — untagged leftovers in
the per-image packages, and hash/sha versions past the keep window in `buildcache`. Nothing tagged
in a per-image package is ever deleted automatically. The policy is the `retention:` block of
`docker/images.yaml`, and you can see what it would do without touching anything:

```bash
GITHUB_TOKEN=<a token with read:packages> python3 scripts/ci/prune_packages.py
```

It reports only; deleting needs an explicit `--delete`.

Every image is scanned with Trivy and the findings are uploaded to the repository's Security tab,
one category per image. **Every image fails the build on a HIGH or CRITICAL finding**, development
images included, and the scan runs with `ignore-unfixed: true` so only findings someone can act on
count. Suppressions go in `.trivyignore` at the repository root, one CVE per line with a reason and
an expiry.

What lands in the Security tab is *not* the same set: that upload deliberately carries every
severity, including LOW and UNKNOWN, so the tab stays the unfiltered view. Only the separate gating
step applies the HIGH/CRITICAL filter. An alert there is therefore not necessarily something that
will fail a build — check its severity before treating it as one.

@subsection devenv_published_apt Refreshing system packages

When the gate reports a **fixed** vulnerability in an apt package, the fix is one line: bump
`ARG APT_SNAPSHOT` to today's date in `docker/dev.Dockerfile`, and in `docker/base.Dockerfile` too
if the package is in the base image.

Both Dockerfiles run `apt-get upgrade`, but an upgrade is only ever as fresh as the layer it lives
in — and that layer's cache key is its own instruction text plus the parent image, neither of which
changes when a security update lands in the Ubuntu archive. buildx therefore reuses the layer, and
the image keeps the package set it had on the day that layer was *first* built. Since the
instructions that usually change are further down (`docker/vim/vimrc` is copied in near the bottom
of `dev.Dockerfile`), the apt layers are almost always a cache hit and the upgrade almost always a
no-op.

`APT_SNAPSHOT` is part of the instruction text, so bumping it invalidates that layer and everything
after it — and because `scripts/ci/images.py` hashes the Dockerfile byte for byte, it also changes
the content hash, so CI rebuilds instead of re-scanning the published image.

@note This is why a failing Trivy gate is usually not something to suppress. `.trivyignore` is for
a fix that is *not ours to make*; a fixed CVE in an apt package is ours, and `APT_SNAPSHOT` is how
we take it.

The findings a development image accumulates are not usually the compiler and debugger it ships;
they are what the *build* left behind. `dev-cpu` bakes `~/.cache/pre-commit` in, and
`pre-commit install-hooks` leaves a Go toolchain, four unused code-generator binaries from the
actionlint repository, and one copy of `pip` — with pip's whole vendored dependency tree — in
every hook virtualenv. `docker/dev.Dockerfile` prunes all of it in the same layer that creates it,
which is both what clears the scan and what takes about 590 MB off the image — `~/.cache` inside
`dev-cpu` measures 407 MB where it used to measure 993 MB.

@section devenv_python Python in the container

`python` and `python3` are a uv-managed CPython 3.14, matching the image `scripts/gen_reference.py`
runs in — so a helper script behaves the same in both places:

```console
$ python3 --version
Python 3.14.7
```

Ubuntu's own `python3.12` is untouched at `/usr/bin/python3.12`; the uv shims in
`~/.local/bin` simply come first on `PATH`. There is no unversioned `python` on Ubuntu at all, so
that name comes entirely from uv.

`pip` is **not** installed into it. Use `uv pip install` — it is the documented tool here, and it
is what keeps the image's Trivy gate clean, since an extracted `pip` drags its whole vendored
dependency tree into the scan. `python -m venv` is unaffected. If you genuinely need pip itself,
`python -m ensurepip` puts it back from the wheel that ships with the interpreter.

@note `pre-commit` deliberately does **not** use it. Its hook environments are built on the 3.12
that its own interpreter reports, are baked into the image, and would have to be rebuilt over the
network if that changed — and not every pinned hook has a 3.14 wheel. This is why
`docker/dev.Dockerfile` installs 3.14 *after* the pre-commit layer, and why the smoke tests run the
hooks with no network.

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
while typing:

| Key                      | What it does                                                     |
| ------------------------ | ---------------------------------------------------------------- |
| `<Tab>`, `<Right>`       | **Accept.** With nothing highlighted yet, takes the first match   |
| `<C-n>`, `<Down>`        | Next candidate                                                    |
| `<C-p>`, `<Up>`          | Previous candidate                                                |
| `<C-y>`                  | Accept the highlighted candidate — Vim's own binding, still there |
| `<C-e>`                  | Dismiss the popup and keep what you typed                         |
| `<CR>`                   | Dismiss the popup and insert a newline                            |

`completeopt` is `menuone,noinsert,noselect,popup`, which is what makes that table read the way it
does. `noselect` means the popup opens with *nothing* highlighted, so `<Tab>` is never ambiguous
with "I am still typing"; `noinsert` keeps a candidate out of the buffer until you choose one; and
`popup` puts clangd's `--completion-style=detailed` documentation in a floating window instead of
the `preview` split, which otherwise opens and closes a window on every keystroke.

Because nothing is highlighted at first, accepting the obvious candidate would cost two keystrokes
— one to highlight, one to accept. `<Tab>` and `<Right>` collapse that: with no selection they take
the first match outright, and with a selection they accept that. When no popup is open both keys do
what they always did, so `<Tab>` still indents.

@note `<Tab>` and `<Right>` are `inoremap <expr>` mappings on `s:AcceptCompletion()` in
`docker/vim/vimrc`. They test `pumvisible()` rather than anything `asyncomplete`-specific, so they
work for Vim's built-in completions (`<C-x><C-f>` for filenames, say) as well as for clangd's.

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

@subsection devenv_vim_spell Spell checking and the shared word list

Spell checking is on for every buffer (`set spell`, `spelllang=en`). `]s` and `[s` move between
misspellings and `z=` offers corrections.

The part worth knowing is where a *new* word goes. `'spellfile'` names two files, in this order:

| Key    | File                              | Shared?                                  |
| ------ | --------------------------------- | ---------------------------------------- |
| `zg`   | `spell/en.utf-8.add` in the repo  | Yes — committed, and read by VS Code too |
| `2zg`  | `~/.vim/spell/en.utf-8.add`       | No — container-local, gone with the container |

`zg` on the word under the cursor adds it to the project list; `zug` takes it back out. `2zg` and
`2zug` do the same to the personal one, for words that have no business in someone else's checkout.

`spell/en.utf-8.add` is the whole point of the arrangement: `cspell.config.yaml` declares it as a
dictionary with `addWords: true`, so VS Code's **Add word to dictionary** appends to the same file.
A word added in either editor is known to both, and turns up in the diff rather than in someone's
untracked settings. The format is one word per line with `#` for comments — which both checkers
read the same way. The name is Vim's requirement (`{lang}.{encoding}.add`), which is why it is not
called `dictionary.txt`.

@note `zw` — mark a word as *wrong* — writes `word/!`, which cspell does not understand; it spells
a forbidden word `!word`. Prefer `2zw` and keep the shared list additive.

Vim reads the *compiled* `spell/en.utf-8.add.spl`, never the text, and does not notice on its own
when the text is newer. The vimrc therefore recompiles it on startup when it is out of date, and
again whenever the list is written from inside Vim. `:SpellSync` forces a rebuild, which is the
command to reach for after VS Code adds a word to a file Vim already has open. The `.spl` is
generated and binary, and `.gitignore` covers it.

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

@section devenv_vscode VS Code

VS Code uses the same container, so that both editors resolve symbols through the same clangd,
against the same `compile_commands.json`, with the same compiler and libraries underneath.

Install the **Dev Containers** extension (`ms-vscode-remote.remote-containers`), open the
repository, and accept **Reopen in Container** — or run *Dev Containers: Reopen in Container* from
the command palette. The first open pulls the image and installs the extensions listed below into
it; later ones are immediate.

`.devcontainer/devcontainer.json` is the whole configuration. What it says, and why:

| Key | Why it is there |
| --- | --- |
| `image` | `ghcr.io/…/dev-cpu:edge`, the published image — no local build needed. See below to use one. |
| `workspaceFolder`, `workspaceMount` | `/workspace`, the same path the Vim workflow mounts at, so `~/.config/clangd/config.yaml` and every documented command mean the same thing in both. |
| `remoteUser`, `updateRemoteUserUID` | Run as `non_root` and rewrite its uid to match yours, so files created in the mounted repository are not owned by someone else. This is the automatic version of the `--build-arg UID=…` rebuild described above. |
| `overrideCommand` | The image's `ENTRYPOINT` is `bash` with no `CMD`, so the container would exit the moment it started. |
| `clangd.arguments` | The same five flags as `docker/vim/vimrc`, `--query-driver` included. Divergence here shows up as one editor being right about `libstdc++` and the other not. |
| `C_Cpp.intelliSenseEngine: disabled` | If the Microsoft C/C++ extension is installed as well, its own parser competes with clangd and reports a second, different set of diagnostics. clangd is the one this project configures. |

@note `.vscode/` stays in `.gitignore`. The settings that must be the same for everyone live in
`.devcontainer/devcontainer.json` and `cspell.config.yaml`, both committed; `.vscode/` is the
per-developer remainder. In particular, do **not** keep words in `cSpell.words` there — they are
invisible to Vim and to everyone else. See @ref devenv_vim_spell.

To work against a locally built image instead of the published one, change `image` to
`altx-cpp/dev-cpu:local` (the default name from `scripts/build_docker_images_locally.sh`) — but do
not commit that, since it does not exist on anyone else's machine.

@section devenv_trouble Troubleshooting

| Symptom                                              | Cause                                                        |
| ---------------------------------------------------- | ------------------------------------------------------------ |
| Every line red, `bits/c++config.h` not found          | clangd started without `--query-driver`                       |
| `omp.h` not found, but `g++` compiles fine            | `libomp-18-dev` missing: clang does not search GCC's headers  |
| Completion works in one file, not another             | That file has no `compile_commands.json` entry — reconfigure  |
| No completion in `:term`                              | `$SHELL` not exported, so the terminal is running dash        |
| Files in the repository owned by the wrong user       | Image built with a `UID`/`GID` that is not yours              |
| A CPU build pulling gigabytes of ROCm                 | `docker-buildx` not installed; legacy builder resolves all stages |
| Trivy reports a *fixed* CVE in an apt package         | The apt layer was a cache hit — bump `APT_SNAPSHOT`, see @ref devenv_published_apt |
| A word added in VS Code still underlined in Vim       | The `.add.spl` is stale in an already-open session — `:SpellSync` |
| `zg` reports it cannot write the word list            | Vim started outside the repository, so `spell/` was not found upwards |
| VS Code's "Add to dictionary" offers only user settings | The cspell extension is not seeing `cspell.config.yaml` — check the folder it opened |

`:LspStatus` answers most editor questions directly, and `clangd --check=<file>` outside Vim shows
exactly which flags clangd used and which includes it failed to resolve.
