"""The host-neutral Empirica application service (ADR-30, ADR-31).

Orchestrates the ``empirica/v1`` wire contract over the pure decision core and the two persistence
ports, naming no filesystem, Git, Claude, or Pi concept. It is the layer that:

* validates versioned ``empirica/v1`` requests and returns typed responses (:mod:`.wire`);
* keeps the run's operational state — status, pass budget, and the pointer to the current claim
  graph — behind :class:`~core.ports.RunRepository` (:mod:`.state`);
* records claim graphs, evidence, and audit results as immutable, content-addressed artifacts behind
  :class:`~core.ports.ArtifactRepository`, and derives the adjudicator's injected verdicts from them
  (:mod:`.knowledge`);
* invokes ``core.convergence.adjudicate`` and applies the ``max_passes`` termination the core omits
  (:mod:`.service`).

Public API::

    from application import EmpiricaService, GenerationAllocator, API_VERSION
"""
from .service import EmpiricaService, GenerationAllocator
from .wire import API_VERSION

__all__ = ["EmpiricaService", "GenerationAllocator", "API_VERSION"]
