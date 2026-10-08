# Building packages

Building a release package on your own machine with `scripts/build_local_package.sh`. Tagged
releases are built for all three platforms by GitHub Actions and published by hand; see
[releasing.md](releasing.md). To run VISTA from a checkout without packaging it, see
[development.md](development.md).

The build host needs the credentials and tooling so the recipient does not.

**Each release, review the bundled Electron.** Its version is pinned in
`electron/package.json`, and each package's manifest records it as
`window.electron`. Bump it if it has fallen out of Electron's supported
releases, and put the version in the release notes. On a Linux host that runs
the window without the sandbox, the engine's own security fixes are all that
stands between a page and the researcher's account.

## Build-host requirements

All verified by `--check`:

- `uv`, `npm`, `git`
- Docker or Podman
- Git access to the amsc2 GitLab (`gitlab.com/amsc2/...`) for the private
  `amscrot-py` that HPC job submission needs; see
  [HPC dependencies](development.md#hpc-dependencies) for the `url.insteadOf` rewrite. Nothing is
  read out of your credential store and nothing is written to `.env`. The
  preflight only asks git whether the fetch would succeed.
- Network access to `pypi.org`, `registry.npmjs.org`, `huggingface.co`, and
  `nodejs.org`; add `code.ornl.gov` only when fetching the corpus with a
  token. Probed per host, because a network that allows PyPI and blocks
  HuggingFace is a real configuration worth finding out about in seconds
  rather than an hour in.

Not checked by `--check`, but needed: network access to `github.com`, where the backend's
[PALISADE](palisade.md) dependency is fetched, and the same platform as the package, on a
machine that can run the code-execution sandbox (KVM on Linux; `msb doctor`
reports ready on Windows). Without the sandbox the build's smoke test fails at the end.


## Build-host env vars

Read from the repo-root `.env`, the same way the backend reads it. A value
already exported in your environment wins over the file.

| Variable                                                                          | Needed for                                                                                                    | Skip it with                                                     |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `VISTA_DATA_TOKEN`                                                                | Fetching the AI-safety corpus (and with `--science-projects`, the molten-salt corpus and MSTDB) from `v28/vista-data` on code.ornl.gov | `--payload DIR`, an already-unpacked `vista-data` tree           |
| `OPENAI_API_KEY` (with `OPENAI_BASE_URL` and `VISTA_BACKEND_MODEL`), or `AZURE_OPENAI_*` | Citation metadata in the vector store. One model call per paper for title, authors, journal, year, and DOI | `--vector-store DIR` (and `--science-projects-vector-store DIR`) to reuse built stores, or `--without-citations` |
| `VISTA_VERSION`                                                                   | Overriding the commit-derived version stamp                                                                   | Optional; omit it                                                |

The preflight treats a missing citation credential as an error, not a
warning. Without it, the shipped corpus retrieves passages that cite nothing,
and the recipient has no way to fix that, since the citations are baked into
the store they receive.

Run the preflight first. It checks every prerequisite in one pass, reports
all the misses together, and installs or configures nothing:

```bash
./scripts/build_local_package.sh --check
```

Then build. The archive lands in `dist/` with a `.sha256` and a
`.manifest.json` beside it:

```bash
./scripts/build_local_package.sh
```

Build from a clean, committed tree. Unless `VISTA_VERSION` is set, the version
is the last release tag plus the commits since it, e.g. `0.2.0+3.g<short-sha>`
(`0.0.0+g<short-sha>` before any release tag, plus `-dirty` when the tree is
not clean), and the archive is named after the tag's part,
`vista-0.2.0-<os>-<arch>`. It is recorded in
the manifest along with the runtime versions and payload inventory. Budget
about 7 GB in the output directory, the staging tree plus the archive, for a
~2 GB result. The build finishes by unpacking the archive somewhere else and
running the launcher against it, so a green finish means the artifact has
been started and queried, not just assembled; a failed smoke test fails the
build.

Cross-compiling is not supported. The package carries a platform-specific
interpreter and compiled libraries, and the launcher refuses to run where
`os-arch` does not match its manifest. Build on each platform you ship.

## Build options

| Flag                             | Effect                                                                                                                    |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `--check`                        | Run the preflight and exit; builds nothing                                                                                |
| `--payload DIR`                  | Use an unpacked `vista-data` tree instead of fetching it with `VISTA_DATA_TOKEN`. It must hold `ai-safety/`, and with `--science-projects` also `molten-salt-papers/` and `mstdb/` |
| `--output-dir DIR`               | Archive destination (default `dist/`)                                                                                     |
| `--archive-format gz\|zstd\|zip\|none` | Defaults to `gz` on unix, `zip` on Windows                                                                          |
| `--vector-store DIR`             | Optional. Reuse an already-built **AI-safety** Chroma store instead of indexing that corpus again. Only for skipping re-embedding, and it makes no model calls; omit it and the build indexes the corpus itself |
| `--science-projects`             | Also pack the molten-salt corpus and its index, MSTDB and the `forge-tune` CSV, so the package seeds the `molten-salt` and `alloy-design` projects. Also enabled by `VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true` |
| `--science-projects-vector-store DIR` | Optional, and only with `--science-projects`. Reuse an already-built **molten-salt** Chroma store instead of indexing that corpus again |
| `--without-citations`            | Index the corpus but skip the per-paper metadata calls; recorded in the manifest                                          |
| `--skip-smoke-test`              | Skip the post-build unpack-and-run verification                                                                           |
| `--verify-without-sandbox`       | For a build host that cannot run the sandbox, such as a hosted macOS CI runner. The smoke test still runs, but every check that needs the sandbox, retrieval included, is reported as skipped rather than passed, and the build says the package was verified without it. Run the full smoke test on a real machine before shipping such a package |
| `--keep-staging`                 | Leave the staging tree in place for inspection                                                                            |

A default package carries only the AI-safety corpus and its index. The
molten-salt corpus, MSTDB and the `forge-tune` CSV are packed only with
`--science-projects`, and a default package contains none of them.

A typical rebuild, once you have a corpus clone and a vector store worth reusing:

```bash
./scripts/build_local_package.sh \
  --payload ~/.vista-build/vista-data \
  --vector-store ~/.vista-build/ai-safety-rag_db
```

A package with the science projects, reusing both stores:

```bash
./scripts/build_local_package.sh --science-projects \
  --payload ~/.vista-build/vista-data \
  --vector-store ~/.vista-build/ai-safety-rag_db \
  --science-projects-vector-store ~/.vista-build/rag_db
```

That still downloads the embedding weights, runs `npm ci`, and builds the UI and
the MCP app; it skips only the indexing pass and its per-paper model calls. Both
store options exist only to skip re-embedding. Leaving them out always produces a
correct build.

## Building on Windows

Run the same script from Git Bash (it comes with Git for Windows). It builds a
`win-x86` package whose launcher is PowerShell, so a researcher needs no bash.
They open `app\window\VISTA.exe` from the Start menu, and it runs `vista.ps1`
itself, hidden, after the same unblock and execution-policy check `vista.cmd`
makes. `vista.cmd` is the diagnostic launcher, from cmd, PowerShell, or by
double-clicking; `vista.ps1` is not meant to be run directly.

```bash
./scripts/build_local_package.sh --check
./scripts/build_local_package.sh --vector-store data/knowledge-bases/ai-safety/rag_db
```

The sandbox image is built with Docker Desktop or Podman Desktop; start its
machine first. No C++ build tools are needed, and long paths do not have to be
enabled: the build reports how much room its deepest path leaves for the
folder a package is unpacked into. The archive is a zip, which Explorer's
Extract All opens.
