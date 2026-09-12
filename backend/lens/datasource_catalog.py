"""Compatibility import for the consolidated datasource package."""

import importlib
import sys

_module = importlib.import_module("lens.datasource.catalog")
sys.modules[__name__] = _module
