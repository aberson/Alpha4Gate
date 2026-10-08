"""Jev: an inspectable decision-graph player family.

The runtime lives here (``src/jev`` -> top-level ``jev``); each policy version is
packaged separately under ``bots/jev/vN`` and loaded through :mod:`jev.policy`.
Nothing in this package imports ``bots.current``, a legacy ``bots.<version>``
tree, burnysc2, a neural policy, or an LLM client: the validation and
interpretation path is stdlib-only by design.
"""

from __future__ import annotations
