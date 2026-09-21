"""FinSight: a grounded, citation-first financial research assistant.

Answers financial questions by combining hybrid retrieval over SEC filings with deterministic
XBRL analytics, and is evaluated end to end with a reproducible harness.
"""

from __future__ import annotations

from importlib import metadata

try:
    __version__ = metadata.version("finsight")
except metadata.PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
