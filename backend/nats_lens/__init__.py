"""nats-lens -- a NATS management GUI that says where every number came from."""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version


def _resolve() -> str:
    # The released image stamps this at build time. The package is on PYTHONPATH
    # there rather than pip-installed, so there is no distribution metadata to
    # read and the UI footer would otherwise report a development version for
    # every tagged release.
    stamped = os.environ.get("NATS_LENS_VERSION", "").strip()
    if stamped:
        return stamped
    try:
        return _version("nats-lens")
    except PackageNotFoundError:
        # A source checkout with only the dependencies installed -- the dev
        # container bind-mounts the source rather than installing the package.
        return "0.1.0.dev"


__version__ = _resolve()

__all__ = ["__version__"]
