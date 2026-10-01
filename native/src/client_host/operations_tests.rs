//! A host's network operations (gwz-py
//! `dev-docs/GwzPyPerOperationTransportDesign.md` §2.4–§2.7), without an
//! interpreter: the limit, cancellation, the bounded record of completed
//! cancellations, the aggregate and close.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use gwz_core::model::ErrorCode;

use super::{Canceller, Cleanup, LIMIT, Operations, RECORDED_CANCELLATIONS, Ticket};

const NONE: Duration = Duration::ZERO;
const SOON: Duration = Duration::from_secs(5);

fn host() -> Arc<Operations> {
    Arc::new(Operations::default())
}

/// A canceller that counts its calls.
fn counting() -> (Canceller, Arc<AtomicUsize>) {
    let calls = Arc::new(AtomicUsize::new(0));
    let seen = Arc::clone(&calls);
    let canceller: Canceller = Arc::new(move || {
        seen.fetch_add(1, Ordering::SeqCst);
    });
    (canceller, calls)
}

fn admitted(operations: &Arc<Operations>, id: &str, canceller: Option<Canceller>) -> Ticket {
    let mut ticket = operations.register(id.into(), canceller).unwrap();
    ticket.admit().unwrap();
    ticket
}

const REPORT: Cleanup = Cleanup {
    pending_local_work: 2,
    peer_cleanup_confirmed: false,
};

#[test]
fn a_ninth_operation_waits_for_a_slot_and_takes_the_first_that_frees() {
    let operations = host();
    let mut running: Vec<Ticket> = (0..LIMIT)
        .map(|i| admitted(&operations, &format!("op_{i}"), None))
        .collect();
    let mut ninth = operations.register("op_ninth".into(), None).unwrap();
    let (sender, receiver) = mpsc::channel();
    let waiter = thread::spawn(move || {
        ninth.admit().unwrap();
        sender.send(Instant::now()).unwrap();
        ninth
    });
    assert!(
        receiver.recv_timeout(Duration::from_millis(200)).is_err(),
        "the ninth waits"
    );
    let freed = Instant::now();
    assert!(!running.pop().unwrap().finish(None));
    let admitted_at = receiver
        .recv_timeout(SOON)
        .expect("a freed slot admits the ninth");
    assert!(admitted_at >= freed);
    drop(waiter.join().unwrap());
}

#[test]
fn a_waiting_operation_is_cancelled_by_name_and_built_nothing() {
    let operations = host();
    let _running: Vec<Ticket> = (0..LIMIT)
        .map(|i| admitted(&operations, &format!("op_{i}"), None))
        .collect();
    let (canceller, calls) = counting();
    let mut waiting = operations
        .register("op_waiting".into(), Some(canceller))
        .unwrap();
    let waiter = thread::spawn(move || waiting.admit());
    thread::sleep(Duration::from_millis(50));
    assert_eq!(
        operations.cancel("op_waiting", SOON).unwrap(),
        Cleanup::default()
    );
    let refusal = waiter.join().unwrap().unwrap_err();
    assert_eq!(refusal.code, ErrorCode::Cancelled);
    // A waiting operation has no request to cancel yet.
    assert_eq!(calls.load(Ordering::SeqCst), 0);
}

#[test]
fn a_running_operation_is_cancelled_through_its_canceller_and_returns_its_report() {
    let operations = host();
    let (canceller, calls) = counting();
    let ticket = admitted(&operations, "op_running", Some(canceller));
    let ender = thread::spawn(move || {
        thread::sleep(Duration::from_millis(100));
        ticket.finish(Some(REPORT))
    });
    assert_eq!(operations.cancel("op_running", SOON).unwrap(), REPORT);
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert!(!ender.join().unwrap());
}

#[test]
fn a_running_operation_that_outlives_the_bound_reports_one_pending_job() {
    let operations = host();
    let (canceller, _calls) = counting();
    let ticket = admitted(&operations, "op_slow", Some(canceller));
    let begun = Instant::now();
    assert_eq!(
        operations
            .cancel("op_slow", Duration::from_millis(100))
            .unwrap(),
        Cleanup::UNFINISHED
    );
    assert!(begun.elapsed() < SOON);
    ticket.finish(Some(REPORT));
}

#[test]
fn a_running_operation_on_the_native_route_cannot_be_cancelled_and_runs_on() {
    let operations = host();
    let ticket = admitted(&operations, "op_native", None);
    let refusal = operations.cancel("op_native", SOON).unwrap_err();
    assert_eq!(refusal.code, ErrorCode::UnsupportedOperation);
    // Nothing was cancelled, so its end records no cancellation.
    ticket.finish(Some(REPORT));
    assert!(operations.lock().cancellations.is_empty());
}

#[test]
fn a_wrong_or_ended_operation_cannot_be_cancelled() {
    let operations = host();
    assert_eq!(
        operations.cancel("op_unknown", SOON).unwrap_err().code,
        ErrorCode::InvalidRequest
    );
    let ticket = admitted(&operations, "op_done", None);
    ticket.finish(None);
    assert_eq!(
        operations.cancel("op_done", SOON).unwrap_err().code,
        ErrorCode::InvalidRequest
    );
}

#[test]
fn a_live_operation_id_registers_once() {
    let operations = host();
    let ticket = operations.register("op_same".into(), None).unwrap();
    let refusal = operations
        .register("op_same".into(), None)
        .err()
        .expect("a second live registration is refused");
    assert_eq!(refusal.code, ErrorCode::InvalidRequest);
    drop(ticket);
    // Its end, here its drop, frees the ID.
    drop(operations.register("op_same".into(), None).unwrap());
}

#[test]
fn the_record_of_completed_cancellations_keeps_the_last_64() {
    let operations = host();
    for i in 0..3 * RECORDED_CANCELLATIONS {
        let (canceller, _calls) = counting();
        let id = format!("op_{i}");
        let ticket = admitted(&operations, &id, Some(canceller));
        // No wait: the cancel returns before the operation ends, so nothing
        // collects its report, which stays in the record.
        assert_eq!(operations.cancel(&id, NONE).unwrap(), Cleanup::UNFINISHED);
        ticket.finish(Some(REPORT));
    }
    let state = operations.lock();
    assert_eq!(state.cancellations.len(), RECORDED_CANCELLATIONS);
    assert!(state.live.is_empty());
}

#[test]
fn close_refuses_waiting_operations_cancels_running_ones_and_adds_the_aggregate() {
    let operations = host();
    // Two ended earlier: one confirmed nothing and left two jobs, one built
    // nothing.
    admitted(&operations, "op_earlier", None).finish(Some(REPORT));
    admitted(&operations, "op_native_earlier", None).finish(None);
    let (canceller, calls) = counting();
    let mut running: Vec<Ticket> = (0..LIMIT - 1)
        .map(|i| admitted(&operations, &format!("op_{i}"), None))
        .collect();
    let slow = admitted(&operations, "op_slow", Some(canceller));
    let mut waiting = operations.register("op_waiting".into(), None).unwrap();
    let waiter = thread::spawn(move || waiting.admit());
    thread::sleep(Duration::from_millis(50));
    let ender = thread::spawn(move || {
        for ticket in running.drain(..) {
            ticket.finish(None);
        }
    });
    // The slow operation outlives the bound.
    let report = operations.close(false, Duration::from_millis(300));
    assert_eq!(
        waiter.join().unwrap().unwrap_err().code,
        ErrorCode::Cancelled
    );
    ender.join().unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert_eq!(
        report,
        Cleanup {
            pending_local_work: REPORT.pending_local_work + 1,
            peer_cleanup_confirmed: false,
        }
    );
    // Nothing more registers, and a later close returns the first report.
    assert_eq!(
        operations
            .register("op_late".into(), None)
            .err()
            .expect("a closed host registers nothing")
            .code,
        ErrorCode::InvalidRequest
    );
    assert!(!slow.finish(Some(Cleanup::default())));
    assert_eq!(operations.close(false, SOON), report);
}

#[test]
fn an_operation_that_ends_after_a_close_at_exit_learns_it_must_not_attach() {
    let operations = host();
    let ticket = admitted(&operations, "op_outlives", None);
    assert_eq!(
        operations.close(true, Duration::from_millis(50)),
        Cleanup::UNFINISHED
    );
    assert!(ticket.finish(Some(Cleanup::default())));
}

#[test]
fn a_close_at_exit_after_an_earlier_close_waits_for_what_is_still_running() {
    let operations = host();
    let ticket = admitted(&operations, "op_outlives", None);
    let first = operations.close(false, Duration::from_millis(50));
    assert!(!operations.is_idle());
    let ender = thread::spawn(move || {
        thread::sleep(Duration::from_millis(100));
        ticket.finish(None)
    });
    assert_eq!(operations.close(true, SOON), first);
    assert!(operations.is_idle());
    assert!(ender.join().unwrap(), "it ended after the close at exit");
}

#[test]
fn peer_cleanup_is_confirmed_only_when_some_operation_confirmed_it_and_none_did_not() {
    let confirmed = Cleanup {
        pending_local_work: 0,
        peer_cleanup_confirmed: true,
    };
    let operations = host();
    assert_eq!(
        operations.close(false, NONE),
        Cleanup::default(),
        "(0, false): nothing ran"
    );
    let operations = host();
    admitted(&operations, "op_confirmed", None).finish(Some(confirmed));
    assert_eq!(operations.close(false, NONE), confirmed);
    let operations = host();
    admitted(&operations, "op_confirmed", None).finish(Some(confirmed));
    admitted(&operations, "op_unconfirmed", None).finish(Some(Cleanup::default()));
    assert_eq!(operations.close(false, NONE), Cleanup::default());
}
