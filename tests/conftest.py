import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

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

from sphinx_js.analyzer_utils import search_node_modules
from sphinx_js.typedoc import typedoc_version_info

TYPEDOC = search_node_modules("typedoc", "typedoc/bin/typedoc", "")
TYPEDOC_VERSION = typedoc_version_info(TYPEDOC)[0]


pytest_plugins = "sphinx.testing.fixtures"

# Exclude 'roots' dirs for pytest test collector
collect_ignore = ["roots"]


@pytest.fixture(scope="session")
def rootdir():
    return path(__file__).parent.abspath() / "roots"
