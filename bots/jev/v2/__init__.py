"""Jev v2 policy package (displayed as ``v2.jev``).

Holds the packaged ``manifest.json`` and ``policy.json``: the four-Gateway opening
with planned supply, bounded build recovery, and grouped reinforcements (plan J2
Step 214), plus one owned, spaced Probe scout that reads the bot's own bounded
enemy sightings and retreats when threatened (Step 215), plus a gas economy: one
Assimilator per active base once the opening is placed, built on the geyser unit,
saturated up to SC2's ideal and emptied back to minerals when depleted (Step 216).
Like v1, the policy is read through :mod:`importlib.resources` relative to this
package, so it resolves the same way from a source checkout and from an installed
wheel, never from the caller's working directory. The runtime itself lives in the shared ``jev``
package; v1 stays frozen beside this package.
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
