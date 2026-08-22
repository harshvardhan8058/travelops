"""Repository-root importability for tests that reach `data.generators`.

The deliberate cascade roster lives in `data/generators/` because it is dataset definition, not
application code. The backend package is installed into the venv; `data/` is not.

**This no longer manipulates `sys.path`.** `pythonpath = ["..", "."]` in
`backend/pyproject.toml` does it declaratively for the whole test run, which is both the documented
pytest mechanism and order-independent — the `sys.path` version depended on this conftest being
imported before anything that needed `data`, and that assumption broke the moment another conftest
appeared and moved the first import earlier.

Kept as a file, empty of behaviour, because deleting it would leave the reason for the pyproject
setting recorded nowhere near the tests that depend on it.
"""

from __future__ import annotations
