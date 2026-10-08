"""TheNormalizer's engine.

Four modules, in the order they depend on each other:

* :mod:`process` — starting ffmpeg, watching it, stopping it. The only module that spawns anything.
* :mod:`media` — what a file is: its streams, and what it measures.
* :mod:`normalize` — what was asked for, what is possible, and the commands that carry it out.
* :mod:`verify` — measuring what was written, against the plan that produced it.

Nothing here imports ``aiohttp`` or knows that a window exists. ``server.py`` is transport, and it is
the only place HTTP appears.
"""

from __future__ import annotations

__all__ = ["media", "normalize", "process", "verify"]
