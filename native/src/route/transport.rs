//! The per-operation transport route (1.1.0 S6.2; gwz-py
//! `dev-docs/GwzPyPerOperationTransportDesign.md` §2.2–§2.3, §2.5).
//!
//! | Behaviour | Clause |
//! | --- | --- |
//! | The environment snapshot is taken at the native entry, from the process environment, while the caller holds the GIL: before `py.detach`, and for `submit` before its operation's thread is spawned. A Python thread that writes `os.environ` needs the GIL, so it cannot race the capture | §2.3 |
//! | The off switch is resolved from that snapshot at the operation's start; with it on, the operation takes libgit2's native route and builds no runtime | §2.2 "The off switch"; amendment 2 §3.17 |
//! | Otherwise the operation runs inside gwz-core's cancellable entry, on a runtime of its own built from the snapshot, with a token whose controls the host keeps as the operation's canceller | §2.2, §2.4 "No sharing", §2.5 |
//! | An invocation identity named `~` or `~/…` resolves against the snapshot's `HOME`, so the operation's key, like its known hosts, comes from the environment it captured | §2.8 "Changed in documented behaviour" |

use std::collections::BTreeSet;
use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, PoisonError};

use gwz_core::git::Git2Backend;
use gwz_core::model::ModelResult;
use gwz_core::session_host::EnvironmentSnapshot;
use gwz_core::transport_host::{
    CallControls, CancellationToken, NativeCaller, with_cancellable_local_transport_native,
};
use gwz_core::transport_scope::Operation;
use gwz_core::transport_setting::{self, Driver, Source, Transport};
use gwz_core::{Cbor, RequestMeta};
use pyo3::types::PyAnyMethods;
use pyo3::{PyResult, Python};

use crate::client_host::{Canceller, Cleanup};
use crate::error;

pub(crate) struct Route(Inner);

/// The repository files already reported by this Client.
pub(crate) type Notices = Mutex<BTreeSet<PathBuf>>;

enum Inner {
    /// The off switch is on: libgit2's native route, and no runtime.
    Native,
    /// The operation's own runtime, built from `environment` when it runs.
    Transport {
        environment: EnvironmentSnapshot,
        native: Option<NativeCaller>,
        token: CancellationToken,
        canceller: Canceller,
    },
}

/// The route of a network operation, captured at the native entry while the
/// caller holds the GIL. `meta` is the request's metadata, which the entry
/// takes and checks against the handler's: anything this resolves in it, it
/// resolves in `request_bytes` too.
pub(crate) fn capture(
    py: Python<'_>,
    request_bytes: &mut Vec<u8>,
    meta: &mut RequestMeta,
    operation: Operation,
    ignored_files: &Notices,
) -> PyResult<Route> {
    // Capture before releasing the GIL; file reads and the bounded target
    // scan can then let another Python thread run.
    let pairs: Vec<_> = std::env::vars_os().collect();
    let environment = EnvironmentSnapshot::from_os_pairs(pairs.clone()).map_err(error::model)?;
    let start = gwz_core::workspace_ops::caller_directory(meta).ok();
    let scan_meta = meta.clone();
    let (setting, ignored) = py.detach(move || {
        let setting = transport_setting::resolve(None, &environment);
        let ignored = start
            .as_deref()
            .map(|start| transport_setting::ignored_values(operation, start, &scan_meta))
            .unwrap_or_default();
        (setting, ignored)
    });
    let setting = setting.map_err(|refusal| {
        error::model(gwz_core::model::ModelError::new(
            gwz_core::model::ErrorCode::InvalidRequest,
            refusal.message(Driver::Python),
        ))
    })?;
    for value in ignored {
        let is_new = ignored_files
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .insert(value.location.file.clone());
        if is_new {
            let message = format!(
                "gwz: ignoring {} in {} ({}): only GWZ_TRANSPORT and your global git configuration select the transport; {}",
                value.entry_text(),
                value.location.where_text(),
                value.scope.text(),
                value.location.remove_text()
            );
            // Logging failures never turn an ignored repository value into a refusal.
            if let Ok(logging) = py.import("logging")
                && let Ok(logger) = logging.call_method1("getLogger", ("gwz",))
            {
                let _ = logger.call_method1("warning", (message,));
            }
        }
    }
    if setting.transport == Transport::Native {
        let source = match &setting.source {
            Source::Environment => "GWZ_TRANSPORT=native".to_owned(),
            Source::GlobalConfiguration(location) => {
                format!("gwz.transport in {}", location.where_text())
            }
            Source::Default | Source::Flag => unreachable!("native requires a Python setting"),
        };
        let remedy = match &setting.source {
            Source::Environment => "set GWZ_TRANSPORT=gwz, or unset it".to_owned(),
            Source::GlobalConfiguration(location) => {
                format!("{}, or set GWZ_TRANSPORT=gwz", location.remove_text())
            }
            Source::Default | Source::Flag => unreachable!("native requires a Python setting"),
        };
        let message = format!(
            "gwz: using libgit2's native transport (from {source}), as gwz 1.0 did; {remedy}, to use gwz's transport"
        );
        py.import("gwz._transport_notices")?
            .call_method1("native_notice", (message,))?;
    }
    let native = setting.transport == Transport::Native;
    let route = capture_with(request_bytes, meta, pairs, |_| native)?;
    if native {
        fill_native_defaults(request_bytes, meta);
    }
    Ok(route)
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
        native: None,
        token,
        canceller,
    }))
}

impl Route {
    pub(crate) fn attach_native(&mut self, caller: NativeCaller) {
        if let Inner::Transport { native, .. } = &mut self.0 {
            *native = Some(caller);
        }
    }
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
                environment,
                token,
                native,
                ..
            } => {
                let (result, report) = with_cancellable_local_transport_native(
                    meta,
                    operation_id,
                    &environment,
                    &token,
                    native,
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
    rewrite_request_meta(request_bytes, meta, resolved_meta);
}

/// Native uses the 1.0.17 concurrency defaults only where the caller left
/// them unset. The request and the metadata passed to the route stay equal.
fn fill_native_defaults(request_bytes: &mut Vec<u8>, meta: &mut RequestMeta) {
    let mut resolved_meta = meta.clone();
    let policy = resolved_meta.policy.get_or_insert_with(Default::default);
    policy.concurrency.get_or_insert(50);
    policy.max_connections_per_host.get_or_insert(8);
    rewrite_request_meta(request_bytes, meta, resolved_meta);
}

fn rewrite_request_meta(
    request_bytes: &mut Vec<u8>,
    meta: &mut RequestMeta,
    resolved_meta: RequestMeta,
) {
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
