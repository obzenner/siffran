"""The host-neutral Empirica v2 application composition (ADR-30, ADR-31).

This package exposes the ``empirica/v2`` protocol dispatch and service composition seams that callers
(hooks, host adapters, conformance tests) reach through the shared bridge. It names no filesystem,
Git, Claude, or Pi concept.

* :func:`dispatch_request` validates a raw ``empirica/v2`` request and dispatches it once through
  a handler (:mod:`.protocol`);
* :func:`compose` returns one minimal v2 service exposing the LiveDriver surface (:mod:`.v2`).

Public API::

    from application import compose, dispatch_request
"""
from .protocol import dispatch_request


def compose(*args, **kwargs):
    """Load stateful host composition only when a caller actually requests it."""
    from .v2 import compose as _compose
    return _compose(*args, **kwargs)


__all__ = ["compose", "dispatch_request"]
