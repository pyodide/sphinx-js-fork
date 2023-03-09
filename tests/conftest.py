import os
from pathlib import Path

import pytest
from sphinx.testing.path import path

if "SPHINX_JS_NODE_MODULES" not in os.environ:
    os.environ["SPHINX_JS_NODE_MODULES"] = str(
        Path(__file__).parents[1] / "node_modules"
    )

pytest_plugins = "sphinx.testing.fixtures"

# Exclude 'roots' dirs for pytest test collector
collect_ignore = ["roots"]


@pytest.fixture(scope="session")
def rootdir():
    return path(__file__).parent.abspath() / "roots"
