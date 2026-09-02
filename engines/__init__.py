"""Answer engines for the CNC Russia AI support system.

Each engine handles one domain. The router (engines/router.py) orchestrates them.
See ARCHITECTURE.md for the full design.
"""

from engines.base import Engine

__all__ = ["Engine"]
