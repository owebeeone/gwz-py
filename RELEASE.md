# Releasing gwz-py

gwz-py is the repository for the `gwz` Python distribution. It provides:

- `import gwz` Python API bindings.
- The installed Python CLI command `gwz-py`.
- A PyO3 native extension, `gwz._gwz_core`, linked to **gwz-core**.

gwz-py uses the same release tag string as gwz-core and gwz-cli: `vX.Y.Z`.
The PyPI distribution is `gwz`. The Python distribution version is `X.Y.Z`;
the release script sets the Cargo package version to that value before building
wheels.

The core source differs intentionally between branches. The new SSPI path edge
is also reconciled to the exact `=0.1.0` registry pin before existing gates. SSPI
is currently unpublished and `publish=false`; provisioned local artifacts do
not bypass this public release prerequisite. See [HostPackaging.md](docs/HostPackaging.md).

The core source is:

- **`main` (dev):** `gwz-core = { path = "../gwz-core" }` - builds against the
  local sibling checkout, so `../gwz-core` must be checked out next to this repo.
  **Do not cut release tags here.**
- **`release`:** `gwz-core = "=X.Y.Z"` - exactly the gwz-core release of the
  same version, from crates.io, as gwz-cli's `release` branch pins it, so wheel
  builds are reproducible. **Release tags are cut off `release`.**

Releases through 1.0.17 pinned gwz-core by git tag (gwz-core
`dev-docs/GwzCratesIoPlan.md` D7). From 1.1.0 the registry pin replaces it
(TR3.4 of gwz-core `dev-docs/GwzTransportReleasePlanAmendment-2.md`), and
`scripts/release.py` migrates the old pin once.

## Native dependency pins

The native extension's dependencies, as `release` pins them. Every one comes
from crates.io; none is a `git` or `path` dependency.

| Crate | Pin on `release` |
|---|---|
| `gwz-sspi` | `gwz-sspi = "=0.1.0"`; registry publication remains a prerequisite |
| `gwz-core` | `gwz-core = "=X.Y.Z"`: gwz-py's own version, exactly. Not a git tag, and not main's sibling path |
| `pyo3`, `tokio`, `cfg-if` | main's crates.io version requirement, unchanged |
| gwz-core's own dependencies: the git2-rs fork's `gwz-git2` and `gwz-libgit2-sys`, gwz-core's internal `gwz-*` crates and, once the transport is in the ordinary build, `gwz-transport` | the versions the published gwz-core X.Y.Z requires |

`Cargo.lock` on `release` fixes each version, with its crates.io checksum.
`scripts/release.py` and the publish workflow run the same check: it refuses any
other gwz-core pin, any `git` or `path` dependency, and any locked package but
gwz-py itself that does not come from crates.io, and it names every native
dependency pin in the log, with the version the lock resolved. OpenSSL and
pkg-config are build prerequisites that each CI runner installs; they are not
pinned here.

The extension and the gwz-cli it tests against both build gwz-core X.Y.Z from
crates.io, so both report `revision=unavailable` and the published package's
source digest. `test_native_module_reports_compiled_core_provenance` then
compares their whole core provenance (its registry rule), as it does when both
sides are built from git. Only a pair of one git build and one registry build,
which no release makes now, falls back to the gwz-core version and build kind.

## One-Time Release Branch Bootstrap

For the first gwz-py release, bootstrap the `release` branch through the release
script:

```sh
python scripts/release.py vX.Y.Z --bootstrap-release
```

This creates `release` from `main`, rewrites the `gwz-core` dependency to
`gwz-core = "=X.Y.Z"` from crates.io, runs the release gates, commits the
initialized `release` branch, and creates tag `vX.Y.Z`.

Every release checks that gwz-core's tag exists and checks it out beside the
worktree for the protocol checks. If that should use a non-default URL, pass it
explicitly:

```sh
python scripts/release.py vX.Y.Z --bootstrap-release \
  --gwz-core-url https://github.com/owebeeone/gwz-core
```

After bootstrap, normal releases use the existing `release` branch and do not
need `--bootstrap-release`.

## Local Release Process

1. **Release matching gwz-core and gwz-cli first** - tag them using the shared
   tag `vX.Y.Z`, and let gwz-core's crates.io publish job finish. The Python
   release builds against gwz-core `X.Y.Z` from crates.io, reads gwz-core's
   protocol at its tag, and uses the CLI tag for cross-driver parity tests.
2. Make sure gwz-py `main` contains the changes to release.
3. Commit or stash local changes. The release script refuses to run from a dirty
   working tree because it creates the release branch from committed refs.
4. From gwz-py `main`, run:

   ```sh
   python scripts/release.py vX.Y.Z
   ```

   The script:

   - Verifies the matching gwz-core and gwz-cli tags exist.
   - Creates a temporary worktree for the gwz-py `release` branch.
   - Merges `main` into `release`.
   - Sets the Cargo package version to `X.Y.Z`.
   - Pins `gwz-core = "=X.Y.Z"` from crates.io.
   - Resolves `Cargo.lock` from crates.io and checks the native dependency
     pins (above).
   - Verifies the PyPI distribution is `gwz` and the installed console script is
     `gwz-py`.
   - Checks out the matching gwz-core and gwz-cli tags beside the temporary
     gwz-py worktree.
   - Creates an isolated temporary Python check environment with the release/test
     tools (`taut-proto`, `pytest`, `maturin`, and `setuptools-scm`).
   - Runs protocol drift checks, protocol regeneration checks, `cargo check`,
     `python run_tests.py`, and package smoke.
   - Builds and installs a wheel in a clean virtualenv.
   - Smoke-tests the installed `gwz-py` command, including clone progress.
   - Asserts the wheel and installed package version are `X.Y.Z`.
   - Commits the reconciled `release` branch and creates tag `vX.Y.Z`.

5. If the script succeeds without `--push`, push exactly what it reports:

   ```sh
   git push origin release vX.Y.Z
   ```

   Or use:

   ```sh
   python scripts/release.py vX.Y.Z --push
   ```

## Publish Process

Publishing is done by `.github/workflows/publish.yml`.

Trigger it by publishing a GitHub release for tag `vX.Y.Z`, or by running the
workflow manually with the same tag.

The workflow:

- Checks out gwz-py at tag `vX.Y.Z`.
- Checks out `owebeeone/gwz-core` at the same tag beside it, for the protocol
  checks and fixtures. The build takes gwz-core from crates.io.
- Checks out `owebeeone/gwz-cli` at the same tag for cross-driver tests.
- Verifies `Cargo.toml` version is `X.Y.Z`.
- Verifies the native dependency pins (above) with the release script's check:
  it refuses a git-tag pin and accepts `gwz-core = "=X.Y.Z"` from crates.io.
- Verifies `pyproject.toml` publishes distribution `gwz` and installs
  `gwz-py = "gwz.cli:main"`.
- Runs protocol drift, protocol regeneration, `cargo check --locked`, which
  builds from the lock it verified, and Python tests.
- Builds Linux amd64, Linux arm64, macOS amd64, macOS arm64, and Windows amd64
  wheels.
- Builds the Linux source distribution.
- Smoke-tests each built wheel through the installed `gwz-py` command.
- Publishes to PyPI using trusted publishing.

Before the first public upload, configure the `gwz` PyPI project, or a pending
publisher for `gwz`, to trust this GitHub Actions publisher:

- Owner: `owebeeone`
- Repository: `gwz-py`
- Workflow: `publish.yml`
- Environment: `pypi`

## Routine Local Gates

Run these before release-oriented changes, and the release script will run them
again from the temporary `release` worktree:

```sh
python scripts/check_protocol_drift.py
python scripts/regen_protocol.py --check
cargo check
python run_tests.py
python scripts/package_smoke.py
```

## The Merge Gotcha

`main` always carries the sibling `path` dependency, while `release` always
carries `gwz-core = "=X.Y.Z"`. Do not manually leave the release branch
pointing back at `../gwz-core`.

`scripts/release.py` reconciles this intentionally different line every release.
If the merge conflicts in `Cargo.toml` or `Cargo.lock`, the script takes main's
file, then pins gwz-core and the version again and resolves the lock from
crates.io. A conflict in any other file stops the release.

The first registry release, 1.1.0, needs no hand edit: `release` still carries
1.0.17's git + tag pin, which the script migrates to `gwz-core = "=1.1.0"`, and
its merge conflicts in `Cargo.toml`, since main added `cfg-if = "1"` beside that
line, which the script resolves as above.

## Recovery Notes

- Existing release tags are never moved. If `vX.Y.Z` already exists at a
  different commit, the script aborts.
- If `release` is checked out in another worktree, the script aborts before
  mutating anything.
- If `--bootstrap-release` fails before committing, the script removes the
  branch it created unless `--keep-worktree` was used for inspection.
- Failed release attempts remove their temporary worktree by default. Use
  `--keep-worktree` when you need to inspect the failure.
- First-line wheels do not bundle or dispatch to the Rust `gwz` binary. The
  installed command is `gwz-py`, backed by the native `gwz-core` extension.

## Slow architecture tests

The source-mutation/compiler suites live in gwz-core and are manual-only:
`python ../gwz-core/scripts/run_compiler_tests.py`. They are not part of Python
release checks or automatic CI. See gwz-core's release documentation.
