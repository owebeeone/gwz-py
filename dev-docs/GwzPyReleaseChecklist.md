# GWZ Python Release Checklist

Status: pre-release checklist

## Chosen Release Mode

- PyPI distribution: `gwz-py`.
- Import package: `gwz`.
- Installed command: `gwz-py`, backed by Python CLI entry point
  `gwz.cli:main`.
- Native execution path: PyO3 `gwz._gwz_core` extension linked to `gwz-core`.
- First-line wheels do not bundle or dispatch to the Rust `gwz` CLI binary.

`main` keeps the development dependency `gwz-core = { path = "../gwz-core" }`.
The local `release` branch pins `gwz-core` through a git tag. For the first
release, use `python scripts/release.py vX.Y.Z --bootstrap-release` to create
`release` from `main`. For later releases, use `python scripts/release.py
vX.Y.Z` to merge `main` into `release`, set the `gwz-py` package version to
`X.Y.Z`, pin `gwz-core` to tag `vX.Y.Z`, run the release gates, commit the
reconciled branch, and create the matching tag.

## Required Local Gates

Run these from `gwz-py` before tagging or publishing:

```sh
python scripts/check_protocol_drift.py
python scripts/regen_protocol.py --check
cargo check
python run_tests.py
python scripts/package_smoke.py
```

The release script must run from a clean working tree. Commit or stash local
changes first; bootstrap and normal release work from committed branch refs.

The package smoke must build a repaired wheel, install it into a fresh virtualenv,
run `gwz-py --help`, clone a local workspace through the installed `gwz-py`,
verify streamed clone lifecycle output, verify materialized member state, and
run `gwz-py status` in the clone.

## CI Gates

- Protocol drift guard.
- Protocol regeneration check.
- Native `cargo check`.
- Python test suite.
- macOS/Linux/Windows validation.
- macOS/Linux repaired-wheel package smoke.
- Windows installed-wheel package smoke.
- Release-tag metadata guard that verifies `Cargo.toml`, `Cargo.lock`, and
  `pyproject.toml` point at the shared release tag and `gwz-py` console script.

## Release Blockers

- Bootstrap the local `release` branch with
  `python scripts/release.py vX.Y.Z --bootstrap-release` before the first
  release, after the matching `gwz-core` tag exists.
- Run manual Windows SSH clone smoke against an installed `gwz-py` in a real
  developer Windows environment when the host is reachable.
- Finish any remaining CLI parity gap review and document known differences.
- Configure the PyPI project for trusted publishing from the GitHub Actions
  publish workflow before the first public upload.

## Documentation Gates

- README install instructions match the wheel strategy.
- README examples cover both `gwz.Client` and installed `gwz-py`.
- Native extension troubleshooting covers missing extension, OpenSSL/libgit2
  source-build prerequisites, and protocol drift failures.
- Known CLI differences from Rust `gwz` are listed if any remain.
