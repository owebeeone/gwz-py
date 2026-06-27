# Releasing gwz-py

gwz-py is the repository for the `gwz` Python distribution. It provides:

- `import gwz` Python API bindings.
- The installed Python CLI command `gwz-py`.
- A PyO3 native extension, `gwz._gwz_core`, linked to **gwz-core**.

gwz-py uses the same release tag string as gwz-core and gwz-cli: `vX.Y.Z`.
The PyPI distribution is `gwz`. The Python distribution version is `X.Y.Z`;
the release script sets the Cargo package version to that value before building
wheels.

The only intentional dependency difference between branches is the `gwz-core`
source:

- **`main` (dev):** `gwz-core = { path = "../gwz-core" }` - builds against the
  local sibling checkout, so `../gwz-core` must be checked out next to this repo.
  **Do not cut release tags here.**
- **`release`:** `gwz-core = { git = ".../gwz-core", tag = "vX.Y.Z" }` - pinned
  to the matching published gwz-core release, so wheel builds are reproducible.
  **Release tags are cut off `release`.**

## One-Time Release Branch Bootstrap

For the first gwz-py release, bootstrap the `release` branch through the release
script:

```sh
python scripts/release.py vX.Y.Z --bootstrap-release
```

This creates `release` from `main`, rewrites the `gwz-core` dependency to the
git + tag form, runs the release gates, commits the initialized `release` branch,
and creates tag `vX.Y.Z`.

If the gwz-core release dependency should use a non-default URL, pass it
explicitly:

```sh
python scripts/release.py vX.Y.Z --bootstrap-release \
  --gwz-core-url https://github.com/owebeeone/gwz-core
```

After bootstrap, normal releases use the existing `release` branch and do not
need `--bootstrap-release`.

## Local Release Process

1. **Release the matching gwz-core first** - tag it off gwz-core `main` using
   the shared tag `vX.Y.Z`.
2. Make sure gwz-py `main` contains the changes to release.
3. Commit or stash local changes. The release script refuses to run from a dirty
   working tree because it creates the release branch from committed refs.
4. From gwz-py `main`, run:

   ```sh
   python scripts/release.py vX.Y.Z
   ```

   The script:

   - Verifies the matching gwz-core tag exists at the release branch's configured
     gwz-core git URL.
   - Creates a temporary worktree for the gwz-py `release` branch.
   - Merges `main` into `release`.
   - Sets the Cargo package version to `X.Y.Z`.
   - Pins `gwz-core` to git tag `vX.Y.Z`.
   - Checks the `Cargo.lock` gwz-core git pin.
   - Verifies the PyPI distribution is `gwz` and the installed console script is
     `gwz-py`.
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
- Checks out `owebeeone/gwz-core` at the same tag beside it.
- Verifies `Cargo.toml` version is `X.Y.Z`.
- Verifies `Cargo.toml` and `Cargo.lock` pin gwz-core to tag `vX.Y.Z`.
- Verifies `pyproject.toml` publishes distribution `gwz` and installs
  `gwz-py = "gwz.cli:main"`.
- Runs protocol drift, protocol regeneration, `cargo check`, and Python tests.
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
carries the `git` + `tag` dependency. Do not manually leave the release branch
pointing back at `../gwz-core`.

`scripts/release.py` reconciles this intentionally different line every release.
If the `Cargo.lock` merge conflicts, the script accepts the merged-in lock file
and refreshes it with `cargo check` after the release dependency pin is restored.

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
