"""Compatibility import for the consolidated datasource package."""

import importlib
import sys

_module = importlib.import_module("lens.datasource.routing")
sys.modules[__name__] = _module
