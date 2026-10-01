//! The per-operation transport route (1.1.0 S6.2; gwz-py
//! `dev-docs/GwzPyPerOperationTransportDesign.md` §2.2–§2.3, §2.5).
//!
//! | Behaviour | Clause |
//! | --- | --- |
//! | The environment snapshot is taken at the native entry, from the process environment, while the caller holds the GIL: before `py.detach`, and for `submit` before its operation's thread is spawned. A Python thread that writes `os.environ` needs the GIL, so it cannot race the capture | §2.3 |
//! | The off switch is resolved from that snapshot at the operation's start; with it on, the operation takes libgit2's native route and builds no runtime | §2.2 "The off switch"; amendment 2 §3.17 |
//! | Otherwise the operation runs inside gwz-core's cancellable entry, on a runtime of its own built from the snapshot, with a token whose controls the host keeps as the operation's canceller | §2.2, §2.4 "No sharing", §2.5 |
//! | An invocation identity named `~` or `~/…` resolves against the snapshot's `HOME`, so the operation's key, like its known hosts, comes from the environment it captured | §2.8 "Changed in documented behaviour" |

use std::ffi::{OsStr, OsString};
use std::path::Path;
use std::sync::Arc;

use gwz_core::git::Git2Backend;
use gwz_core::model::ModelResult;
use gwz_core::session_host::EnvironmentSnapshot;
use gwz_core::transport_host::{CallControls, CancellationToken, with_cancellable_local_transport};
use gwz_core::{Cbor, RequestMeta};
use pyo3::PyResult;

use crate::client_host::{Canceller, Cleanup};
use crate::error;

pub(crate) struct Route(Inner);

enum Inner {
    /// The off switch is on: libgit2's native route, and no runtime.
    Native,
    /// The operation's own runtime, built from `environment` when it runs.
    Transport {
        environment: EnvironmentSnapshot,
        token: CancellationToken,
        canceller: Canceller,
    },
}

/// The route of a network operation, captured at the native entry while the
/// caller holds the GIL. `meta` is the request's metadata, which the entry
/// takes and checks against the handler's: anything this resolves in it, it
/// resolves in `request_bytes` too.
pub(crate) fn capture(request_bytes: &mut Vec<u8>, meta: &mut RequestMeta) -> PyResult<Route> {
    capture_with(request_bytes, meta, std::env::vars_os(), transport_off)
}

/// Whether the off switch is on for an operation. TR1.5 designs its
/// environment and user-configuration forms, which govern gwz-py resolved
/// from the operation's snapshot and the user configuration at the
/// operation's start (amendment 2 §3.17, design §2.2), and TR2.5 implements
/// them. Until then the switch has no form, and it is off.
fn transport_off(_environment: &EnvironmentSnapshot) -> bool {
    false
}

fn capture_with(
    request_bytes: &mut Vec<u8>,
    meta: &mut RequestMeta,
    environment: impl IntoIterator<Item = (OsString, OsString)>,
    transport_off: impl FnOnce(&EnvironmentSnapshot) -> bool,
) -> PyResult<Route> {
    let pairs: Vec<(OsString, OsString)> = environment.into_iter().collect();
    // The snapshot keeps a name's first value, as a lookup of the live
    // environment finds it.
    let home = pairs
        .iter()
        .find(|(name, _)| name == "HOME")
        .map(|(_, home)| home.clone());
    let environment = EnvironmentSnapshot::from_os_pairs(pairs).map_err(error::model)?;
    if transport_off(&environment) {
        return Ok(Route(Inner::Native));
    }
    resolve_identity_home(request_bytes, meta, home.as_deref());
    let (controls, gate) = CallControls::new(&Arc::new(()));
    let token = gate.token().clone();
    let canceller: Canceller = Arc::new(move || controls.cancel());
    Ok(Route(Inner::Transport {
        environment,
        token,
        canceller,
    }))
}

impl Route {
    /// The token's controls, as the host keeps them: cancelling them cancels
    /// the operation's request, while it waits for its runtime or runs.
    pub(crate) fn canceller(&self) -> Option<Canceller> {
        match &self.0 {
            Inner::Native => None,
            Inner::Transport { canceller, .. } => Some(Arc::clone(canceller)),
        }
    }

    /// Runs `action` on the route: inside gwz-core's cancellable entry, which
    /// returns the cleanup report of the runtime it built, or on a backend of
    /// its own, which builds nothing.
    pub(crate) fn run<T>(
        self,
        meta: RequestMeta,
        operation_id: String,
        action: impl FnOnce(&Git2Backend) -> T,
    ) -> (ModelResult<T>, Option<Cleanup>) {
        match self.0 {
            Inner::Native => (Ok(action(&Git2Backend::new())), None),
            Inner::Transport {
                environment, token, ..
            } => {
                let (result, report) = with_cancellable_local_transport(
                    meta,
                    operation_id,
                    &environment,
                    &token,
                    action,
                );
                let cleanup = Cleanup {
                    pending_local_work: report.pending_local_work,
                    peer_cleanup_confirmed: report.peer_cleanup_confirmed,
                };
                (result, Some(cleanup))
            }
        }
    }
}

/// Resolves the request's `~` and `~/…` invocation identities against
/// `home`, the snapshot's `HOME`, where gwz-core would resolve them against
/// the process's `HOME` at the point of use (`transport_support/identity.rs`,
/// `resolve_path`, debt the allowlist records). It changes `meta` and the
/// request's own metadata in `request_bytes` together, or neither. A `~user`
/// path stays for core to refuse, and so does every path when `home` is not
/// absolute, since the entry then refuses the operation for its snapshot's
/// `HOME`.
fn resolve_identity_home(
    request_bytes: &mut Vec<u8>,
    meta: &mut RequestMeta,
    home: Option<&OsStr>,
) {
    let Some(home) = home.map(Path::new).filter(|home| home.is_absolute()) else {
        return;
    };
    let mut resolved_meta = meta.clone();
    let Some(transport) = resolved_meta.transport.as_mut() else {
        return;
    };
    let paths = transport.default_identity.iter_mut().chain(
        transport
            .remote_identities
            .iter_mut()
            .map(|identity| &mut identity.private_key_path),
    );
    let mut resolved = false;
    for path in paths {
        let rest = if path.as_str() == "~" {
            Some("")
        } else {
            path.strip_prefix("~/")
        };
        if let Some(absolute) = rest.and_then(|rest| home.join(rest).to_str().map(str::to_owned)) {
            *path = absolute;
            resolved = true;
        }
    }
    if !resolved {
        return;
    }
    let Ok(mut request) = gwz_core::try_decode(request_bytes) else {
        return;
    };
    let Cbor::Map(fields) = &mut request else {
        return;
    };
    let Some((_, field)) = fields.iter_mut().find(|(key, _)| *key == 1) else {
        return;
    };
    *field = resolved_meta.to_cbor();
    *request_bytes = gwz_core::encode(&request);
    *meta = resolved_meta;
}

cfg_if::cfg_if! {
    if #[cfg(test)] {
        #[path = "transport_tests.rs"]
        mod tests;
    }
}
