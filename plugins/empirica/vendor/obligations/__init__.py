"""The generic obligation contract.

This module knows nothing about GSN, claims, folds, hooks, hosts, filesystem paths, or exit codes.
It performs no I/O and makes no claim about natural-language semantic specificity. Callers inject
observation trust and map domain facts at the boundary. Relative imports support either installed
package import or import by putting the package parent on ``sys.path``.
"""
from .model import (
    Contract,
    Obligation,
    Observation,
    Preservation,
    Retirement,
    Verdict,
    Witness,
    from_json,
    to_json,
)
from .project import canonical, parse, preserved, project, render_text
from .revise import revise
from .verify import verify

__all__ = [
    "Witness", "Obligation", "Observation", "Contract", "Retirement", "Verdict",
    "Preservation", "verify", "revise", "project", "parse", "canonical", "preserved",
    "render_text", "to_json", "from_json",
]
