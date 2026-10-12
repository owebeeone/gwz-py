//! How a network operation reaches the network (gwz-py
//! `dev-docs/GwzPyPerOperationTransportDesign.md` §2.2–§2.3): its own
//! per-operation transport runtime, through gwz-core's cancellable entry, or
//! libgit2's native route.
//!
//! Both arms give `ClientHost` the same three things: `capture`, which runs
//! at the native entry while the caller holds the GIL; the operation's
//! canceller, if its route can cancel it while it runs; and `run`, which
//! returns the action's value with the cleanup report of whatever the route
//! built.
//!
//! The transport arm is candidate code until 1.1.0 S7.1 removes the switch,
//! as gwz-core's `transport_host` is. It is built on Linux, macOS and Windows
//! (1.1.0 S4.5 opened `transport_host` to Windows); every other build takes
//! the native arm.

cfg_if::cfg_if! {
    if #[cfg(all(gwz_transport_candidate, any(unix, windows)))] {
        mod transport;
        pub(crate) use transport::{Notices, Route, capture};
    } else {
        mod native;
        pub(crate) use native::{Notices, Route, capture};
    }
}
