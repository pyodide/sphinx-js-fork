"""Converter from TypeDoc output to IR format"""

import subprocess
from collections.abc import Iterator, Sequence
from errno import ENOENT
from json import load
from os.path import basename, join, normpath, relpath, sep, splitext
from tempfile import NamedTemporaryFile
from typing import Literal, TypedDict

from sphinx.application import Sphinx
from sphinx.errors import SphinxError

from . import pydantic_typedoc as pyd
from .analyzer_utils import Command, is_explicitly_rooted
from .ir import (
    NO_DEFAULT,
    Attribute,
    Class,
    Function,
    Interface,
    Param,
    Pathname,
    Return,
    TopLevel,
)
from .suffix_tree import SuffixTree


class TopLevelProperties(TypedDict):
    name: str
    path: Pathname
    filename: str
    deppath: str | None
    description: str
    line: int
    deprecated: bool
    examples: list[str]
    see_alsos: list[str]
    properties: list[Attribute]
    exported_from: Pathname | None


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

    def _parent_nodes(
        self, node: pyd.IndexType
    ) -> Iterator[pyd.ExternalModule | pyd.OtherNode]:
        """Return an iterator of parent nodes"""
        n: pyd.IndexType | None = node
        while n and n.id != 0:
            if n.kindString == "External module" or n.kindString == "Module":
                # Found one!
                yield n
            n = n.parent

    def _containing_module(self, node: pyd.AnyNode) -> Pathname | None:
        """Return the Pathname pointing to the module containing the given
        node, None if one isn't found."""
        for node in self._parent_nodes(node):
            return Pathname(make_path_segments(node, self._base_dir))
        return None

    def _containing_deppath(self, node: pyd.Node | pyd.Signature) -> str | None:
        """Return the path pointing to the module containing the given node.
        The path is absolute or relative to `root_for_relative_js_paths`.
        Raises ValueError if one isn't found.

        """
        return node.sources[0].fileName

    def _top_level_properties(
        self,
        node: pyd.Node | pyd.Signature,
    ) -> TopLevelProperties:
        source = node.sources[0]
        if node.flags.isExported:
            exported_from = self._containing_module(node)
        else:
            exported_from = None
        return dict(
            name=short_name(node),
            path=Pathname(make_path_segments(node, self._base_dir)),
            filename=basename(source.fileName),
            deppath=self._containing_deppath(node),
            description=make_description(node.comment),
            line=source.line,
            # These properties aren't supported by TypeDoc:
            deprecated=False,
            examples=[],
            see_alsos=[],
            properties=[],
            exported_from=exported_from,
        )

    def _constructor_and_members(
        self, cls: pyd.Node
    ) -> tuple[Function | None, list[Function | Attribute]]:
        """Return the constructor and other members of a class.

        In TS, a constructor may have multiple (overloaded) type signatures but
        only one implementation. (Same with functions.) So there's at most 1
        constructor to return. Return None for the constructor if it is
        inherited or implied rather than explicitly present in the class.

        :arg cls: A TypeDoc node of the class to take apart
        :return: A tuple of (constructor Function, list of other members)

        """
        constructor = None
        members = []
        for child in cls.children:
            ir, _ = self._convert_node(child)
            if not ir:
                continue
            if child.kindString == "Constructor":
                # This really, really should happen exactly once per class.
                assert isinstance(ir, Function)
                constructor = ir
            else:
                assert isinstance(ir, (Function, Attribute))
                members.append(ir)
        return constructor, members

    def _convert_all_nodes(self, root: pyd.Root) -> list[TopLevel]:
        todo: list[pyd.Node | pyd.Signature] = list(root.children)
        done = []
        while todo:
            converted, more_todo = self._convert_node(todo.pop())
            if converted:
                done.append(converted)
            todo.extend(more_todo)
        return done

    def _convert_node(
        self, node: pyd.Node | pyd.Signature
    ) -> tuple[TopLevel | None, list[pyd.Node]]:
        """Convert a node of TypeScript JSON output to an IR object.

        :return: A tuple: (the IR object, a list of other nodes found within
            that you can convert to other IR objects). For the second item of
            the tuple, nodes that can't presently be converted to IR objects
            are omitted.

        """
        if node.inheritedFrom is not None:
            return None, []
        if node.sources:
            # Ignore nodes with a reference to absolute paths (like /usr/lib)
            source = node.sources[0]
            if source.fileName[0] == "/":
                return None, []

        ir: TopLevel | None = None
        if node.kindString == "External module":
            # We shouldn't need these until we implement automodule. But what
            # of js:mod in the templates?
            pass
        elif node.kindString == "Module":
            # Does anybody even use TS's old internal modules anymore?
            pass
        elif node.kindString == "Interface":
            _, members = self._constructor_and_members(node)
            ir = Interface(
                members=members,
                supers=self._related_types(node, kind="extendedTypes"),
                **self._top_level_properties(node),
            )
        elif node.kindString == "Class":
            # Every class has a constructor in the JSON, even if it's only
            # inherited.
            constructor, members = self._constructor_and_members(node)
            ir = Class(
                constructor=constructor,
                members=members,
                supers=self._related_types(node, kind="extendedTypes"),
                is_abstract=node.flags.isAbstract,
                interfaces=self._related_types(node, kind="implementedTypes"),
                **self._top_level_properties(node),
            )
        elif node.kindString == "Property" or node.kindString == "Variable":
            ir = Attribute(
                type=node.type.render_name(self._index),
                **member_properties(node),
                **self._top_level_properties(node),
            )
        elif node.kindString == "Accessor":
            if node.getSignature:
                # There's no signature to speak of for a getter: only a return type.
                type = node.getSignature[0].type
            else:
                # ES6 says setters have exactly 1 param. I'm not sure if they
                # can have multiple signatures, though.
                type = node.setSignature[0].parameters[0].type
            ir = Attribute(
                type=type.render_name(self._index),
                **member_properties(node),
                **self._top_level_properties(node),
            )
        elif (
            node.kindString == "Function"
            or node.kindString == "Constructor"
            or node.kindString == "Method"
        ):
            # There's really nothing in these; all the interesting bits are in
            # the contained 'Call signature' keys. We support only the first
            # signature at the moment, because to do otherwise would create
            # multiple identical pathnames to the same function, which would
            # cause the suffix tree to raise an exception while being built. An
            # eventual solution might be to store the signatures in a one-to-
            # many attr of Functions.
            sigs = node.signatures
            first_sig = sigs[0]  # Should always have at least one
            first_sig.sources = node.sources
            return self._convert_node(first_sig)
        elif (
            node.kindString == "Call signature"
            or node.kindString == "Constructor signature"
        ):
            # This is the real meat of a function, method, or constructor.
            #
            # Constructors' .name attrs end up being like 'new Foo'. They
            # should probably be called "constructor", but I'm not bothering
            # with that yet because nobody uses that attr on constructors atm.
            ir = Function(
                params=[self._make_param(p) for p in node.parameters],
                # Exceptions are discouraged in TS as being unrepresentable in its
                # type system. More importantly, TypeDoc does not support them.
                exceptions=[],
                # Though perhaps technically true, it looks weird to the user
                # (and in the template) if constructors have a return value:
                returns=self._make_returns(node)
                if node.kindString != "Constructor signature"
                else [],
                **member_properties(node.parent),
                **self._top_level_properties(node),
            )

        return ir, node.children

    def _related_types(
        self,
        node: pyd.Interface | pyd.Class,
        kind: Literal["extendedTypes", "implementedTypes"],
    ) -> list[Pathname]:
        """Return the unambiguous pathnames of implemented interfaces or
        extended classes.

        If we encounter a formulation of interface or class reference that we
        don't understand (which I expect to occur only if it turns out you can
        use a class or interface literal rather than referencing a declared
        one), return 'UNIMPLEMENTED' for that interface or class so somebody
        files a bug requesting we fix it. (It's not worth crashing for.)

        """
        types = []
        if kind == "extendedTypes":
            orig_types = node.extendedTypes
        elif kind == "implementedTypes":
            assert isinstance(node, pyd.Class)
            orig_types = node.implementedTypes
        else:
            raise ValueError(
                f"Expected kind to be 'extendedTypes' or 'implementedTypes' not {kind}"
            )

        for t in orig_types:
            if t.type == "reference" and t.id is not None:
                rtype = self._index[t.id]
                pathname = Pathname(make_path_segments(rtype, self._base_dir))
                types.append(pathname)
            # else it's some other thing we should go implement
        return types

    def _make_param(self, param: pyd.Param) -> Param:
        """Make a Param from a 'parameters' JSON item"""
        default = param.defaultValue or NO_DEFAULT
        return Param(
            name=param.name,
            description=make_description(param.comment),
            has_default=param.defaultValue is not None,
            is_variadic=param.flags.isRest,
            # For now, we just pass a single string in as the type rather than
            # a list of types to be unioned by the renderer. There's really no
            # disadvantage.
            type=param.type.render_name(self._index),
            default=default,
        )

    def _make_returns(self, signature: pyd.Signature) -> list[Return]:
        """Return the Returns a function signature can have.

        Because, in TypeDoc, each signature can have only 1 @return tag, we
        return a list of either 0 or 1 item.

        """
        type = signature.type
        if type.type == "intrinsic" and type.name == "void":
            # Returns nothing
            return []
        return [
            Return(
                type=type.render_name(self._index),
                description=signature.comment.returns.strip(),
            )
        ]


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


def make_description(comment: pyd.Comment) -> str:
    """Construct a single comment string from a fancy object."""
    ret = "\n\n".join(text for text in [comment.shortText, comment.text] if text)
    return ret.strip()


def member_properties(
    node: pyd.Node | pyd.Signature | pyd.Param,
) -> dict[str, bool]:
    return dict(
        is_abstract=node.flags.isAbstract,
        is_optional=node.flags.isOptional,
        is_static=node.flags.isStatic,
        is_private=node.flags.isPrivate,
    )


def short_name(node: pyd.Node | pyd.Signature) -> str:
    if (
        node.kindString == "Module"
        or node.kindString == "External module"
        or node.kindString == "Namespace"
    ):
        return node.name[1:-1]  # strip quotes
    return node.name


# Optimization: Could memoize this for probably a decent perf gain: every child
# of an object redoes the work for all its parents.
def make_path_segments(
    node: IndexType, base_dir: str, child_was_static: bool | None = None
) -> list[str]:
    """Return the full, unambiguous list of path segments that points to an
    entity described by a TypeDoc JSON node.

    Example: ``['./', 'dir/', 'dir/', 'file.', 'object.', 'object#', 'object']``

    :arg base_dir: Absolute path of the dir relative to which file-path
        segments are constructed
    :arg child_was_static: True if the child node we're computing the path of
        is a static member of the node under consideration. False if it is.
        None if the current node is the one we're ultimately computing the path
        of.

    TypeDoc uses a totally different, locality-sensitive resolution mechanism
    for links: https://typedoc.org/guides/link-resolution/. It seems like a
    less well thought-out system than JSDoc's namepaths, as it doesn't
    distinguish between, say, static and instance properties of the same name.
    (AFAICT, TypeDoc does not emit documentation for inner properties, as for a
    function nested within another function.) We're sticking with our own
    namepath-like paths, even if we eventually support {@link} syntax.

    """
    node_is_static = node.flags.isStatic

    parent_segments = (
        make_path_segments(node.parent, base_dir, child_was_static=node_is_static)
        if node.parent
        else []
    )

    delimiter = "" if child_was_static is None else "."

    # Handle the cases here that are handled in _convert_node(), plus any that
    # are encountered on other nodes on the way up to the root.
    if (
        node.kindString == "Variable"
        or node.kindString == "Property"
        or node.kindString == "Accessor"
        or node.kindString == "Interface"
        or node.kindString == "Namespace"
    ):
        # We emit a segment for a Method's child Call Signature but skip the
        # Method itself. They 2 nodes have the same names, but, by taking the
        # child, we fortuitously end up without a trailing delimiter on our
        # last segment.
        segments = [node.name]
    elif (
        node.kindString == "Call signature"
        or node.kindString == "Constructor signature"
    ):
        # Similar to above, we skip the parent Constructor and glom onto the
        # Constructor Signature. That gets us no trailing delimiter. However,
        # the signature has name == 'new Foo', so we go up to the parent to get
        # the real name, which is usually (always?) "constructor".
        segments = [node.parent.name]
    elif node.kindString == "Class":
        segments = [node.name]
        if child_was_static is False:
            delimiter = "#"
    elif node.kindString == "External module" or node.kindString == "Module":
        # 'name' contains folder names if multiple folders are passed into
        # TypeDoc. It's also got excess quotes. So we ignore it and take
        # 'originalName', which has a nice, absolute path.
        assert node.originalName
        rel = relpath(node.originalName, base_dir)
        if not is_explicitly_rooted(rel):
            rel = f".{sep}{rel}"

        segments = rel.split(sep)
        filename = splitext(segments[-1])[0]
        segments = [s + "/" for s in segments[:-1]] + [filename]
    else:
        # None, as for a root node, Constructor, or Method
        segments = []

    if segments:
        # It's not some abstract thing the user doesn't think about and we skip
        # over.
        segments[-1] += delimiter
        result = parent_segments + segments
    else:
        # Allow some levels of the JSON to not have a corresponding path segment:
        result = parent_segments
    return result
