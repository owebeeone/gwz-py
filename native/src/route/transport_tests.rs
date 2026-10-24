//! The transport route's seams (gwz-py
//! `dev-docs/GwzPyPerOperationTransportDesign.md` §3): the off switch, which
//! with its seam on takes the native route and builds no runtime, and `~/`
//! identities, which resolve against the snapshot's `HOME`.

use std::cell::Cell;
use std::ffi::OsString;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use gwz_core::model::ErrorCode;
use gwz_core::{FetchRequest, RemoteSshIdentity, TransportOptions};

use gwz_core::transport_host::NativeHost;

use super::{Cleanup, ExitGate, RequestMeta, capture_with, fill_native_defaults, note_sink};
use crate::client_host::Operations;

/// A Client host with no SSPI worker, which is all a route's backend needs of it.
fn host() -> NativeHost {
    NativeHost::new(Err(gwz_sspi::ErrorKind::UnsupportedPlatform))
}

fn gate() -> ExitGate {
    Arc::new(Operations::default()).exit_gate()
}

/// What reached the interpreter: each record the sink made, with its level.
type Records = Arc<Mutex<Vec<(&'static str, String)>>>;

/// A sink whose interpreter call is a push to `Records`.
fn recording(exit: ExitGate) -> (gwz_core::transport_host::NoteSink, Records) {
    let records = Records::default();
    let seen = Arc::clone(&records);
    let sink = note_sink(exit, move |level, line| {
        seen.lock().unwrap().push((level, line.to_owned()));
    });
    (sink, records)
}

/// A fetch whose invocation names `default_identity` and, for `origin`,
/// `remote_identity`.
fn request(default_identity: Option<&str>, remote_identity: Option<&str>) -> Vec<u8> {
    let request = FetchRequest {
        meta: RequestMeta {
            request_id: "req_route".into(),
            schema_version: "gwz.protocol/v0".into(),
            transport: Some(TransportOptions {
                default_identity: default_identity.map(str::to_owned),
                remote_identities: remote_identity
                    .map(|path| RemoteSshIdentity {
                        remote: "origin".into(),
                        private_key_path: path.into(),
                    })
                    .into_iter()
                    .collect(),
                ..TransportOptions::default()
            }),
            ..RequestMeta::default()
        },
    };
    gwz_core::encode(&request.to_cbor())
}

fn meta(request_bytes: &[u8]) -> RequestMeta {
    FetchRequest::from_cbor(&gwz_core::try_decode(request_bytes).unwrap())
        .unwrap()
        .meta
}

fn identities(request_bytes: &[u8]) -> (Option<String>, String) {
    let transport = meta(request_bytes).transport.unwrap();
    (
        transport.default_identity,
        transport.remote_identities[0].private_key_path.clone(),
    )
}

/// An environment that holds `HOME`, if given, and one other variable.
fn environment(home: Option<&str>) -> Vec<(OsString, OsString)> {
    let mut pairs = vec![("GWZ_ROUTE_TEST".into(), "1".into())];
    pairs.extend(home.map(|home| ("HOME".into(), home.into())));
    pairs
}

#[test]
fn native_defaults_are_written_to_request_and_explicit_limits_survive() {
    let mut bytes = request(None, None);
    let mut request_meta = meta(&bytes);
    fill_native_defaults(&mut bytes, &mut request_meta);
    assert_eq!(request_meta.policy.as_ref().unwrap().concurrency, Some(50));
    assert_eq!(
        request_meta
            .policy
            .as_ref()
            .unwrap()
            .max_connections_per_host,
        Some(8)
    );
    assert_eq!(request_meta, meta(&bytes));

    request_meta
        .policy
        .as_mut()
        .unwrap()
        .max_connections_per_host = Some(32);
    fill_native_defaults(&mut bytes, &mut request_meta);
    assert_eq!(
        request_meta
            .policy
            .as_ref()
            .unwrap()
            .max_connections_per_host,
        Some(32)
    );
}

/// With the seam on, the operation takes libgit2's native route: the snapshot
/// has no `HOME`, so a runtime built from it would refuse before the action,
/// and here the action runs and no cleanup is reported. Nothing about the
/// request changes, and nothing can cancel it while it runs.
#[test]
fn with_the_off_switch_on_an_operation_takes_the_native_route_and_builds_no_runtime() {
    let mut request_bytes = request(Some("~/id"), None);
    let original = request_bytes.clone();
    let mut request_meta = meta(&request_bytes);
    let route = capture_with(
        &mut request_bytes,
        &mut request_meta,
        environment(None),
        host(),
        |_| true,
    )
    .unwrap();
    assert!(route.canceller().is_none());
    assert_eq!(request_bytes, original);
    let ran = Cell::new(false);
    let (result, cleanup) = route.run(request_meta, "op_req_route".into(), gate(), |_| {
        ran.set(true);
    });
    assert!(result.is_ok());
    assert!(ran.get());
    assert_eq!(cleanup, None);
}

/// With the seam on, the operation still runs on its Client host's gate (operator decision D47): the backend it is
/// handed is the host's own backend, not a plain one, so its `git commit`, `git tag` and libgit2 credential helper
/// take the gate that the host's supervisor's worker launches take. That gate is the process's (gwz-core step 1.3a;
/// operator decision D-E), so another Client's host is on it too.
#[test]
fn with_the_off_switch_on_an_operation_runs_on_the_client_hosts_gate() {
    let host = NativeHost::new(Err(gwz_sspi::ErrorKind::UnsupportedPlatform));
    let other = NativeHost::new(Err(gwz_sspi::ErrorKind::UnsupportedPlatform));
    let mut request_bytes = request(Some("~/id"), None);
    let mut request_meta = meta(&request_bytes);
    let route = capture_with(
        &mut request_bytes,
        &mut request_meta,
        environment(None),
        host.clone(),
        |_| true,
    )
    .unwrap();
    let seen = Cell::new((false, false));
    let (result, _) = route.run(request_meta, "op_req_route".into(), gate(), |backend| {
        seen.set((host.gates(backend), other.gates(backend)));
    });
    assert!(result.is_ok());
    assert_eq!(seen.get(), (true, true));
}

/// With the seam off, the same operation enters the cancellable entry, which
/// builds its runtime from the snapshot and refuses for its missing `HOME`,
/// before the action and with nothing left to clean up.
#[test]
fn with_the_off_switch_off_an_operation_enters_the_transport() {
    let mut request_bytes = request(None, None);
    let mut request_meta = meta(&request_bytes);
    let route = capture_with(
        &mut request_bytes,
        &mut request_meta,
        environment(None),
        host(),
        |_| false,
    )
    .unwrap();
    assert!(route.canceller().is_some());
    let ran = Cell::new(false);
    let (result, cleanup) = route.run(request_meta, "op_req_route".into(), gate(), |_| {
        ran.set(true);
    });
    assert_eq!(result.unwrap_err().code, ErrorCode::InvalidRequest);
    assert!(!ran.get());
    assert_eq!(cleanup, Some(Cleanup::default()));
}

/// Resolved in the request and in the metadata the entry takes alike, which
/// the entry checks equal to the handler's.
#[test]
fn tilde_identities_resolve_against_the_snapshot_home() {
    let mut request_bytes = request(Some("~/keys/id"), Some("~"));
    let mut request_meta = meta(&request_bytes);
    capture_with(
        &mut request_bytes,
        &mut request_meta,
        environment(Some("/snapshot/home")),
        host(),
        |_| false,
    )
    .unwrap();
    assert_eq!(
        identities(&request_bytes),
        (
            Some("/snapshot/home/keys/id".into()),
            "/snapshot/home/".into()
        )
    );
    assert_eq!(request_meta, meta(&request_bytes));

    // `~user`, relative and absolute paths stay for core. So does every path
    // without an absolute HOME: the entry refuses the operation for its
    // snapshot's HOME.
    let unchanged = [
        (Some("/snapshot/home"), "~other/id", "keys/id"),
        (Some("/snapshot/home"), "/abs/id", "id"),
        (None, "~/id", "~/id"),
        (Some("relative/home"), "~/id", "~"),
        (Some(""), "~/id", "~"),
    ];
    for (home, default_identity, remote_identity) in unchanged {
        let mut request_bytes = request(Some(default_identity), Some(remote_identity));
        let original = request_bytes.clone();
        let mut request_meta = meta(&request_bytes);
        capture_with(
            &mut request_bytes,
            &mut request_meta,
            environment(home),
            host(),
            |_| false,
        )
        .unwrap();
        assert_eq!(
            request_bytes, original,
            "{home:?} {default_identity} {remote_identity}"
        );
        assert_eq!(request_meta, meta(&original));
    }
}

/// gwz-py design §2.6: an operation that outlives the exit bound attaches to
/// no interpreter, and a note is an attachment. A note, or a detail line, that
/// the transport delivers after the host closed at exit makes no interpreter
/// call; before the close, and after a close that is not at exit, it does.
#[test]
fn a_note_after_the_host_closed_at_exit_makes_no_interpreter_call() {
    let operations = Arc::new(Operations::default());
    let (sink, records) = recording(operations.exit_gate());
    sink.tell("note: before");
    sink.tell_detail("detail: before");
    assert_eq!(records.lock().unwrap().len(), 2, "alive: both reach it");

    operations.close(true, Duration::ZERO);
    sink.tell("note: after the exit close");
    sink.tell_detail("detail: after the exit close");
    assert_eq!(
        records.lock().unwrap().len(),
        2,
        "closed at exit: neither reaches it"
    );
}

#[test]
fn a_note_after_a_close_that_is_not_at_exit_still_reaches_the_interpreter() {
    let operations = Arc::new(Operations::default());
    let (sink, records) = recording(operations.exit_gate());
    operations.close(false, Duration::ZERO);
    sink.tell("note: after an ordinary close");
    assert_eq!(
        *records.lock().unwrap(),
        vec![("warning", "note: after an ordinary close".to_owned())]
    );
}

/// The exit hook's close can come while the operation's own thread is the one
/// that delivers: the sink the operation took before the close sees it.
#[test]
fn a_sink_taken_before_the_exit_close_drops_notes_delivered_from_another_thread() {
    let operations = Arc::new(Operations::default());
    let (sink, records) = recording(operations.exit_gate());
    operations.close(true, Duration::ZERO);
    std::thread::spawn(move || sink.tell("note: from the pass thread"))
        .join()
        .unwrap();
    assert!(records.lock().unwrap().is_empty());
}
