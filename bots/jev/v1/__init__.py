"""Jev v1 policy package (displayed as ``v1.jev``).

Holds the packaged ``manifest.json`` and ``policy.json``. The policy is read
through :mod:`importlib.resources` relative to this package, so it resolves the
same way from a source checkout and from an installed wheel, never from the
caller's working directory. The runtime itself lives in the ``jev`` package.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from jev.policy import PolicyBundle, load_policy_bundle

__all__ = ["load_policy"]


def load_policy(policy_path: Path | None = None) -> PolicyBundle:
    """Load and validate this package's manifest and policy.

    ``policy_path`` validates a candidate policy document against this package's
    manifest instead of the packaged ``policy.json``.
    """
    return load_policy_bundle(
        files(__name__), expected_entrypoint=__name__, policy_path=policy_path
    )
