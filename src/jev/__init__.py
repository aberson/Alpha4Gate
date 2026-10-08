"""Jev: an inspectable decision-graph player family.

The runtime lives here (``src/jev`` -> top-level ``jev``); each policy version is
packaged separately under ``bots/jev/vN`` and loaded through :mod:`jev.policy`.
Nothing in this package imports ``bots.current``, a legacy ``bots.<version>``
tree, a neural policy, or an LLM client. The validation and interpretation path
(contracts, policy, operations, runtime, and the runner's ``--validate-policy``)
is stdlib-only by design; burnysc2 is imported only by the SC2 lifecycle in
:mod:`jev.bot` and, lazily, by the SC2 port in :mod:`jev.sc2_adapter`.
"""

from __future__ import annotations
