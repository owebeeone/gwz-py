# TR2.5 step 3 — Python transport settings

Date: 2026-10-03

Status: **implementation draft; not accepted.** The lane owner will settle the
root/Python tuple, then run independent Code, State and Surface reviews.
No implementation GO, merge, push, tag or alpha installation is implied.

## Authority and scope

`gwz-core/dev-docs/GwzTransportOffSwitchDesign.md` §§4, 6, 7–10 controls
this package. The operator accepted all recommended open-question answers
and kept D2, D3 and E3. The release plan and amendment 2, the Python
per-operation design, and `dev-docs/GwzTransportHandoff.md` §6.1 apply.
The process is AgentProcessRules as amended by GwzProcessOptimization §8,
with the review-loop canonical prompts and bounded remediation.

Only gwz-py's driver, public constructor default, diagnostics, tests and caller
documentation change. Core's accepted resolver is consumed unchanged. CLI
Rust, credentials, wire protocol, platform activation and deferred performance
qualification are outside this package.

## Implementation

- ClientHost captures the process environment with the GIL held. Candidate
  route capture releases the GIL for `transport_setting::resolve` and the
  bounded repository scan, then reports diagnostics before registering an
  operation. `call` and `submit` share this entry. Local requests bypass it.
- Environment overrides global configuration; each operation re-reads both.
  Refusals become model `InvalidRequest` errors with Python driver's exact
  remedy. No runtime exists on the native route. The existing per-operation
  cancellable transport entry and independent message forwarding are retained.
- Ignored values log on logger `gwz` at WARNING, once per configuration file
  per ClientHost. This state belongs to the candidate route's Notices type;
  ordinary builds use an empty Notices type. Logging failures are ignored.
  The existing candidate module boundary covers this code. No new switch
  occurrence is added, so the checked inventory remains unchanged.
- Native selection calls one private Python warning location in the gwz
  package. Standard warning registry/filter behaviour applies, including a
  filter that refuses the operation before it registers. The CLI scopes and
  restores warning/logging presentation, rendering both paths as stderr notes.
- The constructor defaults `max_connections_per_host` to None. Explicit 32 is
  sent, as are other positive limits; a per-call value still overrides it.
  Native fills only unset concurrency/host limits with 50/8, updating decoded
  metadata and encoded request together. Gwz's defaults stay 100/32.
- The process clock remains 9 seconds by default and is configurable before
  the first backend. No per-route timeout mutation or second clock is added.
  The Python CLI timeout help and README describe both routes and removal.

## Validation and provenance

Focused diagnostics tests were red on the missing private notice module before
implementation, then passed with the existing client tests. Candidate native
entry integration covers call/submit refusal before registration, warnings as
errors, environment/file changes on an existing host, environment precedence,
local-request bypass, ignored invalid values and failed application logging,
and actual native operation cancellation with no absolute HOME/runtime.

The copied virtualenv initially imported main's editable package. Maturin
`develop` was run in this lane; imports now resolve to this lane's `src/gwz`
and ordinary extension. Candidate manifests were freshly generated outside the
workspace with `scripts/build_candidate_extension.py`'s prepare/resolve API,
anchored to this lane (not prewarm's main sources):
`/Volumes/projects/limbo/gwz-tr25-py-candidate-61-20261003/{core,py}`.
Candidate Maturin runs through this lane's `.venv/bin/python` and targets
`/opt/homebrew/bin/python3.13`; the resulting abi3 extension is unpacked using
the same recipe's `unpack` API. Ordinary cache: `gwz-py/target`; candidate cache:
root `target/candidate-py`. The normal ordinary runner uses the lane virtualenv's
CPython 3.12.12. All final gates explicitly select Rust 1.95.0 and retain
default profiles. An initial Rust 1.96.0 run was superseded after final
provenance inspection found that `stable` resolved to that compiler.

Final pinned validation:

| Gate | Result |
| --- | --- |
| `RUSTUP_TOOLCHAIN=1.95.0 .venv/bin/python run_tests.py` (adjacent CLI build, editable extension, regeneration and whole pytest suite) | 992 passed, 18 candidate-only skips; runner exit 0 |
| Whole pytest suite with both-switch extension (`extension-195/gwz/_gwz_core.abi3.so`) and this lane's `target/debug/gwz` | 1,010 passed; exit 0 |
| Ordinary `cargo test --locked --lib` | 25 passed |
| Both-switch candidate `cargo test --locked --manifest-path <prep>/py/Cargo.toml --lib` | 29 passed |
| Ordinary and both-switch `cargo clippy --locked --lib -- -D warnings` | both pass on the gwz-py target |
| Final rebuilt ordinary extension: client, notice and CLI focused suites | 56 passed |
| Final rebuilt both-switch extension: `test_transport_setting.py` | 8 passed, including public GwzBridgeError mapping |
| Transport-only rebuilt extension: settings and existing network integration suites | 18 passed; independent of the application-session switch |
| Process-global and candidate-switch guards | both pass |
| Conditional-boundary guard over the workspace family | pass, no new occurrences |
| Changed Rust files, `rustfmt --check --config skip_children=true`; `git diff --check` | pass |

The full pinned runs preceded two lint-only corrections: the ordinary empty
Notices stub changed from a unit to a braced empty struct, and nested logging
lookups became an equivalent let chain. Both extensions were then rebuilt;
the final focused suites, Rust unit suite and strict lint gates pass. Broad
suites were not repeated for these equivalent source forms.

Raw validation output is local, outside the repositories, in
`/tmp/tr25-py-ordinary-195-full.log`, `-candidate-195-full.log`,
`-rust-195-ordinary.log`, `-rust-195-candidate.log`, `-clippy-ordinary.log`,
`-clippy-candidate.log`, `-ordinary-195-focused.log`,
`-setting-195-focused.log`, `-transport-195-integration.log` and `-cfg.log`,
with the common `/tmp/tr25-py` prefix. These are ordinary product gates,
not a new experimental campaign or a release-platform qualification.

## Review package

Review the gwz-py diff plus this report and the accepted controlling design,
at the lane owner's exact committed tuple. Code and State inspect route/native
capture, ClientHost registration, metadata rewriting, public constructor and
CLI diagnostics. Surface reads only README, Client API documentation and
`gwz-py --help` / subcommand help, with special attention to defaults, native
selection/removal and Python warning versus logging semantics. Inspect all
conditional branches; do not run source-mutation/compiler probes. Reviewers
use the canonical review-prompt-template, verify the tuple at start and end,
and file complete reports verbatim. A combined remediation patch and the same
reviewers' counterexample verification follow any blocking finding, subject to
the two-round cap.

## Reproduction and reviewer commands

Run commands from `/Volumes/projects/limbo/gwz-dev-tr2-5-py/gwz-py`.
Set `RUSTUP_TOOLCHAIN=1.95.0`. The both-switch final module is
`/Volumes/projects/limbo/gwz-tr25-py-candidate-61-20261003/extension-final/gwz/_gwz_core.abi3.so`;
the transport-only module is in the same prep's `extension-transport/gwz/`.
With `GWZ_PY_NATIVE_MODULE` naming either module, the exact focused command is
`PYTHONPATH=src:../taut/src .venv/bin/python -m pytest src/tests/test_transport_setting.py src/tests/test_client_host_transport.py -q`.
Whole-suite invocation adds `GWZ_RUST_BIN=/Volumes/projects/limbo/gwz-dev-tr2-5-py/target/debug/gwz`
and uses `src/tests` instead. Candidate Rust gates use the prepared manifest
`/Volumes/projects/limbo/gwz-tr25-py-candidate-61-20261003/py/Cargo.toml`,
`CARGO_TARGET_DIR=/Volumes/projects/limbo/gwz-dev-tr2-5-py/target/candidate-py`,
`PYO3_PYTHON=/opt/homebrew/bin/python3.13` and
`RUSTFLAGS='--cfg gwz_transport_candidate --cfg gwz_session_candidate -C link-arg=-undefined -C link-arg=dynamic_lookup'`.
Those are the same linker flags Maturin supplies on macOS; extension builds
use Maturin rather than bare cargo with extension-module enabled.

Surface can inspect `.venv/bin/python -m gwz.cli --help`, the same command with
`fetch --help`, and
`.venv/bin/python -c 'import inspect; from gwz import Client; print(inspect.signature(Client)); print(Client.__doc__)'`,
plus README's network transport section. It need not read implementation code
or a design document. The lane owner fills exact settled SHAs in the canonical
review prompts, including the controlling design's core commit, before dispatch.

## Remaining qualification

Windows candidate availability is owned by S4.5 and its preceding parity
work; the existing unix candidate boundary is retained. Linux/Windows release
qualification, consumer release packaging and performance campaigns remain
in their declared batch. Maturin reports Homebrew OpenSSL dependencies in this
local validation wheel; this package does not assert a distributable wheel.
