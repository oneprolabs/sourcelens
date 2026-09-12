"""Compatibility import for the consolidated datasource package."""

import importlib
import sys

_module = importlib.import_module("lens.datasource.versions")
sys.modules[__name__] = _module
