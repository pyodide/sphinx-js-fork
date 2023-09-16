import os
import sys
from pathlib import Path

import pytest
from sphinx.testing.path import path

if "SPHINX_JS_NODE_MODULES" not in os.environ:
    for p in [Path(sys.prefix), Path(__file__).parents[1]]:
        p = p / "node_modules"
        if p.exists():
            os.environ["SPHINX_JS_NODE_MODULES"] = str(p)
            break
    else:
        raise RuntimeError("Couldn't find node_modules")


pytest_plugins = "sphinx.testing.fixtures"

# Exclude 'roots' dirs for pytest test collector
collect_ignore = ["roots"]


@pytest.fixture(scope="session")
def rootdir():
    return path(__file__).parent.abspath() / "roots"
