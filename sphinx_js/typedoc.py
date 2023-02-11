"""Converter from TypeDoc output to IR format"""

import subprocess
from collections.abc import Sequence
from errno import ENOENT
from json import load
from os.path import join, normpath
from tempfile import NamedTemporaryFile
from typing import Literal

from sphinx.application import Sphinx
from sphinx.errors import SphinxError

from . import pydantic_typedoc as pyd
from .analyzer_utils import Command
from .ir import (
    TopLevel,
)
from .suffix_tree import SuffixTree


class Analyzer:
    def __init__(self, json: pyd.Root, base_dir: str):
        """
        :arg json: The loaded JSON output from typedoc
        :arg base_dir: The absolute path of the dir relative to which to
            construct file-path segments of object paths

        """
        self._base_dir = base_dir
        self._index = index_by_id({}, json)
        ir_objects = self._convert_all_nodes(json)
        # Toss this overboard to save RAM. We're done with it now:
        del self._index
        self._objects_by_path: SuffixTree[TopLevel] = SuffixTree()
        self._objects_by_path.add_many((obj.path.segments, obj) for obj in ir_objects)

    @classmethod
    def from_disk(
        cls, abs_source_paths: list[str], app: Sphinx, base_dir: str
    ) -> "Analyzer":
        json = typedoc_output(
            abs_source_paths, app.confdir, app.config.jsdoc_config_path
        )
        return cls(json, base_dir)

    def get_object(
        self,
        path_suffix: list[str],
        as_type: Literal["function", "class", "attribute"] = "function",
    ) -> TopLevel:
        """Return the IR object with the given path suffix.

        :arg as_type: Ignored

        We can't scan through the raw TypeDoc output at runtime like the JSDoc
        analyzer does, because it's just a linear list of files, each
        containing a nested tree of nodes. They're not indexed at all. And
        since we need to index by suffix, we need to traverse all the way down,
        eagerly. Also, we will keep the flattening, because we need it to
        resolve the IDs of references. (Some of the references are potentially
        important in the future: that's how TypeDoc points to superclass
        definitions of methods inherited by subclasses.)

        """
        return self._objects_by_path.get(path_suffix)

    def _convert_all_nodes(self, root: pyd.Root) -> list[TopLevel]:
        todo: list[pyd.Node | pyd.Signature] = list(root.children)
        done = []
        while todo:
            node = todo.pop()
            if node.sources and node.sources[0].fileName[0] == "/":
                # Ignore nodes with a reference to absolute paths (like /usr/lib)
                continue
            converted, more_todo = node.to_ir(self)
            if converted:
                done.append(converted)
            todo.extend(more_todo)
        return done


def typedoc_output(
    abs_source_paths: list[str], sphinx_conf_dir: str, config_path: str
) -> pyd.Root:
    """Return the loaded JSON output of the TypeDoc command run over the given
    paths."""
    command = Command("typedoc")
    if config_path:
        command.add("--tsconfig", normpath(join(sphinx_conf_dir, config_path)))

    with NamedTemporaryFile(mode="w+b") as temp:
        command.add("--json", temp.name, *abs_source_paths)
        try:
            subprocess.call(command.make())
        except OSError as exc:
            if exc.errno == ENOENT:
                raise SphinxError(
                    '%s was not found. Install it using "npm install -g typedoc".'
                    % command.program
                )
            else:
                raise
        # typedoc emits a valid JSON file even if it finds no TS files in the dir:
        return pyd.parse(load(temp))


from .pydantic_typedoc import IndexType


def index_by_id(
    index: dict[int, IndexType],
    node: IndexType,
    parent: IndexType | None = None,
) -> dict[int, IndexType]:
    """Create an ID-to-node mapping for all the TypeDoc output nodes.

    We don't unnest them, but we do add ``__parent`` keys so we can easily walk
    both up and down.

    :arg index: The mapping to add keys to as we go
    :arg node: The node to start traversing down from
    :arg parent: The parent node of ``node``

    """
    # TODO: Can we just uniformly set __parent on nodes rather than doing it
    # differently in different cases? I think the reason for the cases is that
    # we used to set a parent ID rather than a parent pointer and not all nodes
    # have IDs.
    if node.id is not None:  # 0 is okay; it's the root node.
        # Give anything in the map a parent:

        # Parents are used for (1) building longnames, (2) setting memberof
        # (which we shouldn't have to do anymore; we can just traverse
        # children), (3) getting the module a class or interface is defined in
        # so we can link to it, and (4) one other things, so it's worth setting
        # them.
        node.parent = parent
        index[node.id] = node

    # Burrow into everything that could contain more ID'd items. We don't
    # need setSignature or getSignature for now. Do we need indexSignature?
    children: list[Sequence[pyd.Node | pyd.Signature | pyd.Param]] = []

    children.append(node.children)
    if isinstance(node, pyd.Callable):
        children.append(node.signatures)

    if isinstance(node, pyd.Signature):
        children.append(node.parameters)

    for child in (c for l in children for c in l):
        index_by_id(index, child, parent=node)

    return index
