"""Jev policy family: one immutable package per version (``bots/jev/vN``).

There is deliberately no ``bots/jev/VERSION`` file: legacy discovery
(``orchestrator.registry.list_versions``) only considers immediate ``bots/``
children containing ``VERSION``, so Jev stays out of the legacy registry,
snapshots and evolution until a later mixed-family integration (plan D1).
"""
