"""Host adapters that bind empirica's host-neutral core ports to a concrete platform (ADR-30).

The `core` package defines *ports* (``typing.Protocol`` shapes) and speaks only in domain records;
an adapter here supplies the mechanism the port abstracts over — a filesystem, a Git object store, a
database — without the core ever importing it. See ``adapters.state`` for the ``RunRepository``
implementation over machine-local files (ADR-31).
"""
