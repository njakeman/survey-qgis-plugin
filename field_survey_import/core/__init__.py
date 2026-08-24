"""Pure-Python import core: zip -> parsed session model.

No `qgis` or `PyQt5` imports are allowed anywhere under this package (enforced by
tests/test_core_has_no_qgis_imports.py) so it can be unit-tested with plain pytest,
without a QGIS installation.
"""
