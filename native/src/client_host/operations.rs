//! One `Client`'s network operations: the limit of 8 across `call` and
//! `submit`, the cancellation registry, and the running aggregate of their
//! cleanups (gwz-py `dev-docs/GwzPyPerOperationTransportDesign.md` §2.4–§2.7).
//! It is pure Rust state behind one lock, and every wait in it holds no GIL:
//! its callers release the GIL first.
//!
//! | Behaviour | Clause |
//! | --- | --- |
//! | At most 8 network operations run at once. A ninth is registered at once and then waits for a slot on the thread that runs it, holding that thread, until one frees | §2.4 "The limit", "Where a waiting operation waits" |
//! | A waiting operation that is cancelled, or whose host closes, is refused with `Cancelled`, and builds no runtime | §2.4, §2.5 |
//! | A cancel reaches a running operation only through its route's canceller, the transport's token. A running operation on libgit2's native route has none, and its cancel fails without cancelling anything | §2.5 |
//! | A wrong, foreign or completed operation, or a non-network one, is not live in this host, and its cancel fails without cancelling anything | §2.5 |
//! | The record of completed cancellations keeps the last 64, so it stays bounded | §2.5 |
//! | The aggregate is a count and a sum, never a per-operation record: the operations whose cleanup was confirmed and was not, and their pending local work | §2.7 |
//! | Close cancels every waiting and running operation and waits for them up to the bound. Its report adds the aggregate in and counts each unfinished operation as unconfirmed, with one pending job | §2.6, §2.7 |
//! | An operation that ends after a close at interpreter exit still records its outcome here, without the GIL, and learns that it must not attach to the interpreter again | §2.6 |

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use gwz_core::model::{ErrorCode, ModelError, ModelResult};

/// At most this many network operations of one `Client` run at once (§2.4).
pub(crate) const LIMIT: usize = 8;

/// How many completed cancellations the record keeps (§2.5).
const RECORDED_CANCELLATIONS: usize = 64;

/// What cancels a running operation: its route's canceller. It runs on the
/// cancelling thread, outside the host's lock.
pub(crate) type Canceller = Arc<dyn Fn() + Send + Sync>;

/// One operation's cleanup facts, gwz-core's `CleanupReport`, which every
/// build can name.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(crate) struct Cleanup {
    pub(crate) pending_local_work: usize,
    pub(crate) peer_cleanup_confirmed: bool,
}

impl Cleanup {
    /// An operation that has not ended: how much local work remains is
    /// unknown, so it counts one job, and nothing is confirmed (S6.1's report
    /// for an unknown cleanup).
    pub(crate) const UNFINISHED: Self = Self {
        pending_local_work: 1,
        peer_cleanup_confirmed: false,
    };
}

/// A host's network operations.
#[derive(Default)]
pub(crate) struct Operations {
    state: Mutex<State>,
    changed: Condvar,
}

#[derive(Default)]
struct State {
    /// The operations not yet ended, waiting or running, by operation ID.
    live: HashMap<String, Live>,
    /// How many hold a slot.
    running: usize,
    /// The last registration's serial number.
    registered: u64,
    /// Close has begun: nothing more registers, and waiting operations are
    /// refused.
    closing: bool,
    /// The host closed at interpreter exit: an operation that ends from now
    /// on must not attach to the interpreter.
    exiting: bool,
    /// The first close's report, which later closes return.
    report: Option<Cleanup>,
    aggregate: Aggregate,
    /// Completed cancellations, oldest first, by serial number.
    cancellations: VecDeque<(u64, Cleanup)>,
}

struct Live {
    serial: u64,
    running: bool,
    cancelled: bool,
    canceller: Option<Canceller>,
}

/// The running aggregate of the host's operations' cleanups (§2.7).
#[derive(Default)]
struct Aggregate {
    confirmed: usize,
    unconfirmed: usize,
    pending_local_work: usize,
}

impl Aggregate {
    fn add(&mut self, cleanup: Cleanup) {
        if cleanup.peer_cleanup_confirmed {
            self.confirmed += 1;
        } else {
            self.unconfirmed += 1;
        }
        self.pending_local_work += cleanup.pending_local_work;
    }

    /// The close report: the aggregate, and `unfinished` operations that each
    /// count one pending job and leave cleanup unconfirmed. Peer cleanup is
    /// confirmed only when some operation confirmed it and none left it
    /// unconfirmed: with no operation at all it is the contract's (0, false),
    /// since no peer cleanup occurred.
    fn report(&self, unfinished: usize) -> Cleanup {
        Cleanup {
            pending_local_work: self.pending_local_work
                + unfinished * Cleanup::UNFINISHED.pending_local_work,
            peer_cleanup_confirmed: self.confirmed > 0 && self.unconfirmed == 0 && unfinished == 0,
        }
    }
}

impl Operations {
    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Waits for a change until `deadline`; false once it has passed.
    fn wait_until<'a>(
        &self,
        state: MutexGuard<'a, State>,
        deadline: Instant,
    ) -> (MutexGuard<'a, State>, bool) {
        let now = Instant::now();
        if now >= deadline {
            return (state, false);
        }
        let (state, _) = self
            .changed
            .wait_timeout(state, deadline - now)
            .unwrap_or_else(PoisonError::into_inner);
        (state, true)
    }

    /// Registers a network operation at its native entry, before it waits
    /// for a slot, so a cancel finds it at once. Refused once the host is
    /// closing, and while another operation with the same ID is live.
    pub(crate) fn register(
        self: &Arc<Self>,
        operation_id: String,
        canceller: Option<Canceller>,
    ) -> ModelResult<Ticket> {
        let mut state = self.lock();
        if state.closing {
            return Err(ModelError::new(
                ErrorCode::InvalidRequest,
                "client is closed",
            ));
        }
        if state.live.contains_key(&operation_id) {
            return Err(ModelError::new(
                ErrorCode::InvalidRequest,
                format!("operation {operation_id} is already running on this client"),
            ));
        }
        state.registered += 1;
        let serial = state.registered;
        state.live.insert(
            operation_id.clone(),
            Live {
                serial,
                running: false,
                cancelled: false,
                canceller,
            },
        );
        Ok(Ticket {
            operations: Arc::clone(self),
            operation_id,
            serial,
            admitted: false,
            ended: false,
        })
    }

    /// Cancels one live operation (§2.5). A waiting one is refused with
    /// `Cancelled` and built nothing, so its report is (0, false). A running
    /// one is cancelled through its canceller, and this waits up to `bound`
    /// for it to end and returns its cleanup report; one that has not ended
    /// by then reports `Cleanup::UNFINISHED`.
    pub(crate) fn cancel(&self, operation_id: &str, bound: Duration) -> ModelResult<Cleanup> {
        let deadline = Instant::now() + bound;
        let mut state = self.lock();
        let Some(live) = state.live.get_mut(operation_id) else {
            return Err(ModelError::new(
                ErrorCode::InvalidRequest,
                format!(
                    "operation {operation_id} is not a waiting or running network operation of this client"
                ),
            ));
        };
        if !live.running {
            live.cancelled = true;
            drop(state);
            self.changed.notify_all();
            return Ok(Cleanup::default());
        }
        let Some(canceller) = live.canceller.clone() else {
            return Err(ModelError::new(
                ErrorCode::UnsupportedOperation,
                format!(
                    "operation {operation_id} runs on libgit2's native route, which cannot cancel it"
                ),
            ));
        };
        live.cancelled = true;
        let serial = live.serial;
        drop(state);
        canceller();
        let mut state = self.lock();
        loop {
            if !state.is_live(operation_id, serial) {
                // An operation that ended without a report, as one that
                // panicked outside its entry does, or whose report the
                // record has since let go, left its cleanup unknown.
                return Ok(state.cancellation(serial).unwrap_or(Cleanup::UNFINISHED));
            }
            let (next, waiting) = self.wait_until(state, deadline);
            state = next;
            if !waiting {
                return Ok(Cleanup::UNFINISHED);
            }
        }
    }

    /// Closes the host (§2.6): nothing more registers, every waiting
    /// operation is refused, every running one is cancelled, and this waits
    /// up to `bound` for them to end. It returns the first close's report.
    /// A close at `exit` marks the host so that operations ending later do
    /// not attach to the interpreter, and waits for them again if an earlier
    /// close left some running.
    pub(crate) fn close(&self, exit: bool, bound: Duration) -> Cleanup {
        let deadline = Instant::now() + bound;
        let mut state = self.lock();
        state.exiting |= exit;
        if state.closing {
            // A concurrent close, or a later one: it waits for the first
            // close's report and, at exit, for the operations still running.
            loop {
                let done = state.report.is_some() && (!exit || state.live.is_empty());
                if done {
                    break;
                }
                let (next, waiting) = self.wait_until(state, deadline);
                state = next;
                if !waiting {
                    break;
                }
            }
            return state
                .report
                .unwrap_or_else(|| state.aggregate.report(state.live.len()));
        }
        state.closing = true;
        let mut cancellers = Vec::new();
        for live in state.live.values_mut() {
            live.cancelled = true;
            if live.running {
                cancellers.extend(live.canceller.clone());
            }
        }
        drop(state);
        self.changed.notify_all();
        for canceller in cancellers {
            canceller();
        }
        let mut state = self.lock();
        while !state.live.is_empty() {
            let (next, waiting) = self.wait_until(state, deadline);
            state = next;
            if !waiting {
                break;
            }
        }
        let report = state.aggregate.report(state.live.len());
        state.report = Some(report);
        drop(state);
        self.changed.notify_all();
        report
    }

    /// Whether no operation is live: the exit hook has nothing left to wait
    /// for.
    pub(crate) fn is_idle(&self) -> bool {
        self.lock().live.is_empty()
    }
}

impl State {
    fn is_live(&self, operation_id: &str, serial: u64) -> bool {
        self.live
            .get(operation_id)
            .is_some_and(|live| live.serial == serial)
    }

    fn cancellation(&self, serial: u64) -> Option<Cleanup> {
        self.cancellations
            .iter()
            .rev()
            .find(|(recorded, _)| *recorded == serial)
            .map(|(_, cleanup)| *cleanup)
    }
}

/// One registered network operation. It holds its registration, and once
/// admitted its slot, until it ends: `finish`, or its drop, which a panic
/// that unwinds through the operation reaches too.
pub(crate) struct Ticket {
    operations: Arc<Operations>,
    operation_id: String,
    serial: u64,
    admitted: bool,
    ended: bool,
}

impl Ticket {
    /// Waits, holding no GIL, for a slot and takes it. Refused with
    /// `Cancelled` when the operation is cancelled, or its host closes,
    /// while it waits.
    pub(crate) fn admit(&mut self) -> ModelResult<()> {
        let operations = Arc::clone(&self.operations);
        let mut state = operations.lock();
        loop {
            let current = &mut *state;
            let Some(live) = current
                .live
                .get_mut(&self.operation_id)
                .filter(|live| live.serial == self.serial)
            else {
                return Err(waiting_cancelled());
            };
            if live.cancelled || current.closing {
                return Err(waiting_cancelled());
            }
            if current.running < LIMIT {
                live.running = true;
                current.running += 1;
                self.admitted = true;
                return Ok(());
            }
            state = operations
                .changed
                .wait(state)
                .unwrap_or_else(PoisonError::into_inner);
        }
    }

    /// The operation has ended. It records `cleanup`, the report of what it
    /// built, or nothing when it built nothing, frees its slot, and returns
    /// whether its host has closed at interpreter exit, after which the
    /// operation must not attach to the interpreter.
    pub(crate) fn finish(mut self, cleanup: Option<Cleanup>) -> bool {
        self.end(cleanup)
    }

    fn end(&mut self, cleanup: Option<Cleanup>) -> bool {
        self.ended = true;
        let operations = Arc::clone(&self.operations);
        let mut state = operations.lock();
        let cancelled = state.is_live(&self.operation_id, self.serial)
            && state
                .live
                .remove(&self.operation_id)
                .is_some_and(|live| live.cancelled);
        if self.admitted {
            state.running -= 1;
        }
        if let Some(cleanup) = cleanup {
            state.aggregate.add(cleanup);
            if cancelled {
                if state.cancellations.len() == RECORDED_CANCELLATIONS {
                    state.cancellations.pop_front();
                }
                state.cancellations.push_back((self.serial, cleanup));
            }
        }
        let exiting = state.exiting;
        drop(state);
        operations.changed.notify_all();
        exiting
    }
}

impl Drop for Ticket {
    fn drop(&mut self) {
        if !self.ended {
            self.end(None);
        }
    }
}

fn waiting_cancelled() -> ModelError {
    ModelError::new(
        ErrorCode::Cancelled,
        "the network operation was cancelled before it started",
    )
}

cfg_if::cfg_if! {
    if #[cfg(test)] {
        #[path = "operations_tests.rs"]
        mod tests;
    }
}
