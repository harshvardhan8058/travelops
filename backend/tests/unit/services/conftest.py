"""Make the repository root importable so tests can reach `data.generators`.

The deliberate cascade roster lives in `data/generators/` because it is dataset
definition, not application code. The backend package is installed into the venv, but
`data/` is not, so the repository root has to go on the path explicitly.

`app.config.REPO_ROOT` already derives the root from the package location, so there is no
second definition of "where is the repo" to drift.
"""

from __future__ import annotations

import sys

from app.config import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
