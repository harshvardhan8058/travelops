"""Make the repository root importable so contract tests can reach `data.generators`.

See the note in `tests/unit/services/conftest.py`; `app.config.REPO_ROOT` is the single
definition of where the repository root is.
"""

from __future__ import annotations

import sys

from app.config import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
