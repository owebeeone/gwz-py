# GWZ Python Release Checklist

Status: pre-release checklist

## Chosen Release Mode

- PyPI distribution: `gwz-py`.
- Import package: `gwz`.
- Installed command: Python CLI entry point `gwz.cli:main`.
- Native execution path: PyO3 `gwz._gwz_core` extension linked to `gwz-core`.
- First-line wheels do not bundle or dispatch to the Rust `gwz` CLI binary.

## Required Local Gates

Run these from `gwz-py` before tagging or publishing:

```sh
python scripts/check_protocol_drift.py
python scripts/regen_protocol.py --check
cargo check
python run_tests.py
python scripts/package_smoke.py
```

The package smoke must build a repaired wheel, install it into a fresh virtualenv,
run `gwz --help`, clone a local workspace through the installed `gwz`, verify
streamed clone lifecycle output, verify materialized member state, and run
`gwz status` in the clone.

## CI Gates

- Protocol drift guard.
- Protocol regeneration check.
- Native `cargo check`.
- Python test suite.
- macOS/Linux/Windows validation matrix for Python 3.10 through 3.13.
- macOS repaired-wheel package smoke.
- Windows installed-wheel package smoke.

## Release Blockers

- Decide and implement the released `gwz-core` dependency source. Local
  development uses `gwz-core = { path = "../gwz-core" }`; PyPI release builds
  need a reproducible tag or other released source.
- Add the release-tag form of the protocol drift guard, not only the sibling
  checkout guard.
- Define Linux wheel repair/publish policy and add a Linux package-smoke job when
  that policy is settled.
- Run manual Windows SSH clone smoke against an installed `gwz` in a real
  developer Windows environment when the host is reachable.
- Finish the CLI parity gap review. Known candidate: `snapshot --list` is still
  intentionally unimplemented in the Python CLI.
- Set final version/tag ordering across `gwz-core`, `gwz-py`, and `taut-proto`.

## Documentation Gates

- README install instructions match the wheel strategy.
- README examples cover both `gwz.Client` and installed `gwz`.
- Native extension troubleshooting covers missing extension, OpenSSL/libgit2
  source-build prerequisites, and protocol drift failures.
- Known CLI differences from Rust `gwz` are listed if any remain.
