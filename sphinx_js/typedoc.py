"""Converter from TypeDoc output to IR format"""

import os
import pathlib
import re
import subprocess
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from errno import ENOENT
from functools import cache, partial
from inspect import isclass
from json import load
from os.path import basename, relpath, sep, splitext
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field, ValidationError
from sphinx.application import Sphinx
from sphinx.errors import SphinxError

from . import ir
from .analyzer_utils import Command, is_explicitly_rooted, search_node_modules
from .suffix_tree import SuffixTree

__all__ = ["Analyzer"]

MIN_TYPEDOC_VERSION = (0, 24, 0)


@cache
def typedoc_version_info(typedoc: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    result = subprocess.run(
        [typedoc, "--version"], capture_output=True, encoding="utf8"
    )
    lines = result.stdout.strip().splitlines()
    m = re.search(r"TypeDoc ([0-9]+\.[0-9]+\.[0-9]+)", lines[0])
    assert m
    typedoc_version = tuple(int(x) for x in m.group(1).split("."))
    m = re.search(r"TypeScript ([0-9]+\.[0-9]+\.[0-9]+)", lines[1])
    assert m
    typescript_version = tuple(int(x) for x in m.group(1).split("."))
    return typedoc_version, typescript_version


def version_to_str(t: Sequence[int]) -> str:
    return ".".join(str(x) for x in t)


def typedoc_output(
    abs_source_paths: list[str], sphinx_conf_dir: str | pathlib.Path, config_path: str
) -> "Project":
    """Return the loaded JSON output of the TypeDoc command run over the given
    paths."""
    typedoc = search_node_modules("typedoc", "typedoc/bin/typedoc", sphinx_conf_dir)
    typedoc_version, _ = typedoc_version_info(typedoc)
    if typedoc_version < MIN_TYPEDOC_VERSION:
        raise RuntimeError(
            f"Typedoc version {version_to_str(typedoc_version)} is too old, minimum required is {version_to_str(MIN_TYPEDOC_VERSION)}"
        )

    command = Command("node")
    os.environ["TYPEDOC_NODE_MODULES"] = str(Path(typedoc).parents[2])
    if typedoc_version >= (0, 24, 0):
        command.add(str(Path(__file__).parent / "typedoc_0.24.mjs"))
    else:
        command.add(typedoc)
    command.add("--entryPointStrategy", "expand")

    if config_path:
        tsconfig_path = str((Path(sphinx_conf_dir) / config_path).absolute())
        command.add("--tsconfig", tsconfig_path)

    with NamedTemporaryFile(mode="w+b") as temp:
        command.add("--json", temp.name, *abs_source_paths)
        try:
            subprocess.run(command.make(), check=True)
        except OSError as exc:
            if exc.errno == ENOENT:
                raise SphinxError(
                    '%s was not found. Install it using "npm install -g typedoc".'
                    % command.program
                )
            else:
                raise
        # typedoc emits a valid JSON file even if it finds no TS files in the dir:
        return parse(load(temp))


def parse(json: dict[str, Any]) -> "Project":
    try:
        return Project.parse_obj(json)  # type:ignore[no-any-return]
    except ValidationError as exc:
        fix_exc_errors(json, exc)
        raise


class Converter:
    def __init__(self, base_dir: str):
        self.base_dir: str = base_dir
        self.index: dict[int, IndexType] = {}

    def populate_index(self, root: "IndexType") -> "Converter":
        """Create an ID-to-node mapping for all the TypeDoc output nodes.

        We don't unnest them, but we do add ``__parent`` keys so we can easily walk
        both up and down.
        """
        self._populate_index_inner(root, parent=None, containing_module=[])
        return self

    def _populate_index_inner(
        self,
        node: "IndexType",
        parent: "IndexType | None",
        containing_module: list[str],
        filename: str = "",
    ) -> None:
        if node.id is not None:  # 0 is okay; it's the root node.
            self.index[node.id] = node

        parent_kind = parent.kindString if parent else ""
        parent_segments = parent.path if parent else []
        if node.sources:
            filename = node.sources[0].fileName
            node.filename = filename
        self.compute_path(node, parent_kind, parent_segments, filename)

        if node.kindString in ["External module", "Module"]:
            containing_module = node.path

        if parent and isinstance(node, Signature):
            node.parent_member_properties = parent.member_properties()

        # Burrow into everything that could contain more ID'd items
        children: list[Sequence[IndexType]] = []

        children.append(node.children)
        if isinstance(node, Accessor):
            if node.getSignature:
                children.append([node.getSignature])
            if node.setSignature:
                children.append([node.setSignature])

        if isinstance(node, (Callable, TypeLiteral)):
            children.append(node.signatures)

        if isinstance(node, (Member, Param)) and isinstance(node.type, ReflectionType):
            children.append([node.type.declaration])

        if isinstance(node, Signature):
            children.append(node.parameters)
            children.append(node.typeParameter)
            children.append(node.typeParameters)

        if isinstance(node, ClassOrInterface):
            children.append(node.typeParameter)
            children.append(node.typeParameters)

        for child in (c for l in children for c in l):
            self._populate_index_inner(
                child,
                parent=node,
                containing_module=containing_module,
                filename=filename,
            )

    def compute_path(
        self,
        node: "IndexType",
        parent_kind: str,
        parent_segments: list[str],
        filename: str,
    ) -> None:
        """Compute the full, unambiguous list of path segments that points to an
        entity described by a TypeDoc JSON node.

        Example: ``['./', 'dir/', 'dir/', 'file.', 'object.', 'object#', 'object']``

        TypeDoc uses a totally different, locality-sensitive resolution mechanism
        for links: https://typedoc.org/guides/link-resolution/. It seems like a
        less well thought-out system than JSDoc's namepaths, as it doesn't
        distinguish between, say, static and instance properties of the same name.
        (AFAICT, TypeDoc does not emit documentation for inner properties, as for a
        function nested within another function.) We're sticking with our own
        namepath-like paths, even if we eventually support {@link} syntax.
        """
        delimiter = "."
        if not node.flags.isStatic and parent_kind == "Class":
            delimiter = "#"

        if (
            parent_kind == "Project"
            and node.kindString
            not in [
                "Module",
                "External module",
            ]
            and not parent_segments
        ):
            parent_segments = make_filepath_segments(filename)

        segs = node._path_segments(self.base_dir)

        if segs and parent_segments:
            segments = list(parent_segments)
            segments[-1] += delimiter
            segments.extend(segs)
        else:
            segments = segs or parent_segments

        node.path = segments

    def convert_all_nodes(self, root: "Project") -> list[ir.TopLevel]:
        todo: list[Node | Signature] = list(root.children)
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


class Analyzer:
    def __init__(self, json: "Project", base_dir: str):
        """
        :arg json: The loaded JSON output from typedoc
        :arg base_dir: The absolute path of the dir relative to which to
            construct file-path segments of object paths

        """
        converter = Converter(base_dir).populate_index(json)
        ir_objects = converter.convert_all_nodes(json)

        self._base_dir = base_dir
        self._objects_by_path: SuffixTree[ir.TopLevel] = SuffixTree()
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
    ) -> ir.TopLevel:
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


class Source(BaseModel):
    fileName: str
    line: int


class DescriptionItem(BaseModel):
    kind: Literal["text", "code"]
    text: str

    def to_ir(self) -> ir.DescriptionItem:
        if self.kind == "text":
            return ir.DescriptionText(self.text)
        return ir.DescriptionCode(self.text)


class Tag(BaseModel):
    tag: str
    content: list[DescriptionItem]


def description_to_ir(desc: Sequence[DescriptionItem]) -> Sequence[ir.DescriptionItem]:
    return [item.to_ir() for item in desc]


class Comment(BaseModel):
    summary: list[DescriptionItem] = []
    blockTags: list[Tag] = []
    tags: dict[str, list[Sequence[DescriptionItem]]] = Field(
        default_factory=partial(defaultdict, list)
    )

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        for tag in self.blockTags:
            self.tags[tag.tag.removeprefix("@")].append(tag.content)

    def get_description(self) -> Sequence[ir.DescriptionItem]:
        return description_to_ir(self.summary)

    def get_tag_list(self, tag: str) -> list[Sequence[ir.DescriptionItem]]:
        return [description_to_ir(t) for t in self.tags[tag]]

    def get_tag_one(self, tag: str) -> Sequence[ir.DescriptionItem]:
        l = self.tags[tag]
        if not l:
            return []
        assert len(l) == 1
        return description_to_ir(l[0])


class Flags(BaseModel):
    isAbstract: bool = False
    isOptional: bool = False
    isPrivate: bool = False
    isRest: bool = False
    isStatic: bool = False


class MemberProperties(TypedDict):
    is_abstract: bool
    is_optional: bool
    is_static: bool
    is_private: bool


class Base(BaseModel):
    children: Sequence["Node"] = []
    path: list[str] = []
    id: int | None
    kindString: str = ""
    originalName: str | None
    sources: list[Source] = []
    filename: str = ""
    flags: Flags = Field(default_factory=Flags)

    def member_properties(self) -> MemberProperties:
        return dict(
            is_abstract=self.flags.isAbstract,
            is_optional=self.flags.isOptional,
            is_static=self.flags.isStatic,
            is_private=self.flags.isPrivate,
        )

    def _path_segments(self, base_dir: str) -> list[str]:
        raise NotImplementedError


class Project(Base):
    # These are probably never present except "name"
    kindString: Literal["Project"] = "Project"
    name: str | None

    def _path_segments(self, base_dir: str) -> list[str]:
        return []


class TopLevelPropertiesDict(TypedDict):
    name: str
    path: ir.Pathname
    filename: str
    deppath: str | None
    description: Sequence[ir.DescriptionItem]
    line: int | None
    deprecated: Sequence[ir.DescriptionItem] | bool
    examples: Sequence[ir.Description]
    see_alsos: list[str]
    properties: list[ir.Attribute]
    exported_from: ir.Pathname | None


class TopLevelProperties(Base):
    name: str
    kindString: str
    comment: Comment = Field(default_factory=Comment)

    def short_name(self) -> str:
        """Overridden by Modules and Namespaces to strip quotes."""
        return self.name

    def _top_level_properties(self) -> TopLevelPropertiesDict:
        deprecated: Sequence[ir.DescriptionItem] | bool
        deprecated = self.comment.get_tag_one("deprecated") or False
        return dict(
            name=self.short_name(),
            path=ir.Pathname(self.path),
            filename=basename(self.filename),
            deppath=self.filename,
            description=self.comment.get_description(),
            line=self.sources[0].line if self.sources else None,
            # These properties aren't supported by TypeDoc:
            deprecated=deprecated,
            examples=self.comment.get_tag_list("example"),
            see_alsos=[],
            properties=[],
            exported_from=ir.Pathname(make_filepath_segments(self.filename)),
        )

    def to_ir(
        self, converter: Converter
    ) -> tuple[ir.TopLevel | None, Sequence["Node"]]:
        return None, self.children


class NodeBase(TopLevelProperties):
    sources: list[Source] = []

    def _path_segments(self, base_dir: str) -> list[str]:
        return [self.name]


class Accessor(NodeBase):
    kindString: Literal["Accessor"]
    getSignature: "Signature | None" = None
    setSignature: "Signature | None" = None

    def to_ir(self, converter: Converter) -> tuple[ir.Attribute, Sequence["Node"]]:
        if self.getSignature:
            # There's no signature to speak of for a getter: only a return type.
            type = self.getSignature.type
        else:
            assert self.setSignature
            # ES6 says setters have exactly 1 param.
            type = self.setSignature.parameters[0].type

        res = ir.Attribute(
            type=type.render_name(converter),
            **self.member_properties(),
            **self._top_level_properties(),
        )
        return res, self.children


def callable_to_ir(
    self: "Callable | TypeLiteral", converter: Converter
) -> tuple[ir.Function | None, Sequence["Node"]]:
    # There's really nothing in these; all the interesting bits are in
    # the contained 'Call signature' keys. We support only the first
    # signature at the moment, because to do otherwise would create
    # multiple identical pathnames to the same function, which would
    # cause the suffix tree to raise an exception while being built. An
    # eventual solution might be to store the signatures in a one-to-
    # many attr of Functions.
    first_sig = self.signatures[0]  # Should always have at least one
    first_sig.sources = self.sources
    return first_sig.to_ir(converter)


class Callable(NodeBase):
    kindString: Literal[
        "Constructor",
        "Method",
        "Function",
    ]
    signatures: list["Signature"] = []

    def _path_segments(self, base_dir: str) -> list[str]:
        return [self.name]

    def to_ir(
        self, converter: Converter
    ) -> tuple[ir.Function | None, Sequence["Node"]]:
        return callable_to_ir(self, converter)


class ClassOrInterface(NodeBase):
    kindString: Literal["Class", "Interface"]
    extendedTypes: list["TypeD"] = []
    implementedTypes: list["TypeD"] = []
    children: Sequence["ClassChild"] = []
    typeParameter: list["TypeParameter"] = []
    typeParameters: list["TypeParameter"] = []

    def _related_types(
        self,
        converter: Converter,
        kind: Literal["extendedTypes", "implementedTypes"],
    ) -> list[ir.Pathname]:
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
            orig_types = self.extendedTypes
        elif kind == "implementedTypes":
            assert isinstance(self, Class)
            orig_types = self.implementedTypes
        else:
            raise ValueError(
                f"Expected kind to be 'extendedTypes' or 'implementedTypes' not {kind}"
            )

        for t in orig_types:
            if t.type != "reference":
                continue
            if not isinstance(t.target, int):
                continue
            rtype = converter.index[t.target]
            pathname = ir.Pathname(rtype.path)
            types.append(pathname)
            # else it's some other thing we should go implement
        return types

    def _constructor_and_members(
        self, converter: Converter
    ) -> tuple[ir.Function | None, list[ir.Function | ir.Attribute]]:
        """Return the constructor and other members of a class.

        In TS, a constructor may have multiple (overloaded) type signatures but
        only one implementation. (Same with functions.) So there's at most 1
        constructor to return. Return None for the constructor if it is
        inherited or implied rather than explicitly present in the class.

        :arg cls: A TypeDoc node of the class to take apart
        :return: A tuple of (constructor Function, list of other members)

        """
        constructor: ir.Function | None = None
        members = []
        for child in self.children:
            if child.kindString == "Constructor":
                # This really, really should happen exactly once per class.
                # Type parameter cannot appear on constructor declaration so copy
                # it down from the class.
                child.signatures[0].typeParameter = (
                    self.typeParameter or self.typeParameters
                )
                constructor = child.to_ir(converter)[0]
                continue
            result = child.to_ir(converter)[0]
            if result:
                members.append(result)
        return constructor, members


class Class(ClassOrInterface):
    kindString: Literal["Class"]

    def to_ir(self, converter: Converter) -> tuple[ir.Class | None, Sequence["Node"]]:
        constructor, members = self._constructor_and_members(converter)
        result = ir.Class(
            constructor=constructor,
            members=members,
            supers=self._related_types(converter, kind="extendedTypes"),
            is_abstract=self.flags.isAbstract,
            interfaces=self._related_types(converter, kind="implementedTypes"),
            type_params=[
                x.to_ir(converter) for x in (self.typeParameter or self.typeParameters)
            ],
            **self._top_level_properties(),
        )
        return result, self.children


class Interface(ClassOrInterface):
    kindString: Literal["Interface"]

    def to_ir(self, converter: Converter) -> tuple[ir.Interface, Sequence["Node"]]:
        _, members = self._constructor_and_members(converter)
        result = ir.Interface(
            members=members,
            supers=self._related_types(converter, kind="extendedTypes"),
            type_params=[x.to_ir(converter) for x in self.typeParameter],
            **self._top_level_properties(),
        )
        return result, self.children


class Member(NodeBase):
    kindString: Literal[
        "Property",
        "Variable",
    ]
    type: "TypeD"

    def to_ir(
        self, converter: Converter
    ) -> tuple[ir.Attribute | ir.Function | None, Sequence["Node"]]:
        if (
            self.type.type == "reflection"
            and isinstance(self.type.declaration, TypeLiteral)
            and self.type.declaration.signatures
        ):
            return self.type.declaration.to_ir(converter)
        result = ir.Attribute(
            type=self.type.render_name(converter),
            **self.member_properties(),
            **self._top_level_properties(),
        )
        return result, self.children


def make_filepath_segments(path: str) -> list[str]:
    if not is_explicitly_rooted(path):
        path = f".{sep}{path}"
    segs = path.split(sep)
    filename = splitext(segs[-1])[0]
    segments = [s + "/" for s in segs[:-1]] + [filename]
    return segments


class ExternalModule(NodeBase):
    kindString: Literal["External module", "Module"]
    originalName: str = ""

    def short_name(self) -> str:
        return self.name[1:-1]  # strip quotes

    def _path_segments(self, base_dir: str) -> list[str]:
        # 'name' contains folder names if multiple folders are passed into
        # TypeDoc. It's also got excess quotes. So we ignore it and take
        # 'originalName', which has a nice, absolute path.
        if not self.originalName:
            return []
        rel = relpath(self.originalName, base_dir)
        return make_filepath_segments(rel)


class TypeLiteral(NodeBase):
    kindString: Literal["Type literal"]
    variant: Literal["declaration"]

    signatures: list["Signature"] = []
    indexSignature: "Signature | None" = None
    children: Sequence["Member"] = []

    def render(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        if self.signatures:
            yield from self.signatures[0].render(converter)
            return
        yield "{ "
        index_sig = self.indexSignature
        if index_sig:
            assert len(index_sig.parameters) == 1
            key = index_sig.parameters[0]
            yield "["
            yield key.name
            yield ": "
            yield from key.type._render_name(converter)
            yield "]"
            yield ": "
            yield from index_sig.type._render_name(converter)
            yield "; "

        for child in self.children:
            yield child.name
            if child.flags.isOptional:
                yield "?: "
            else:
                yield ": "
            yield from child.type._render_name(converter)
            yield "; "
        yield "}"

    def to_ir(
        self, converter: Converter
    ) -> tuple[ir.Function | None, Sequence["Node"]]:
        return callable_to_ir(self, converter)


class OtherNode(NodeBase):
    kindString: Literal[
        "Enumeration",
        "Enumeration Member",
        "Namespace",
        "Type alias",
        "Reference",
    ]


Node = Annotated[
    Accessor
    | Callable
    | Class
    | ExternalModule
    | Interface
    | Member
    | OtherNode
    | TypeLiteral,
    Field(discriminator="kindString"),
]


ClassChild = Annotated[Accessor | Callable | Member, Field(discriminator="kindString")]


class TypeParameter(Base):
    kindString: Literal["Type parameter"]
    name: str
    type: "OptionalTypeD"
    comment: Comment = Field(default_factory=Comment)

    def to_ir(self, converter: Converter) -> ir.TypeParam:
        extends = None
        if self.type:
            extends = self.type.render_name(converter)
        return ir.TypeParam(
            self.name, extends, description=self.comment.get_description()
        )

    def _path_segments(self, base_dir: str) -> list[str]:
        return []


class Param(Base):
    kindString: Literal["Parameter"] = "Parameter"
    comment: Comment = Field(default_factory=Comment)
    defaultValue: str | None
    flags: Flags
    name: str
    type: "TypeD"

    def to_ir(self, converter: Converter) -> ir.Param:
        """Make a Param from a 'parameters' JSON item"""
        default = self.defaultValue or ir.NO_DEFAULT
        return ir.Param(
            name=self.name,
            description=self.comment.get_description(),
            has_default=self.defaultValue is not None,
            is_variadic=self.flags.isRest,
            # For now, we just pass a single string in as the type rather than
            # a list of types to be unioned by the renderer. There's really no
            # disadvantage.
            type=self.type.render_name(converter),
            default=default,
        )

    def _path_segments(self, base_dir: str) -> list[str]:
        return []


class Signature(TopLevelProperties):
    kindString: Literal[
        "Constructor signature",
        "Call signature",
        "Get signature",
        "Set signature",
        "Index signature",
    ]

    name: str
    typeParameter: list[TypeParameter] = []
    typeParameters: list[TypeParameter] = []
    parameters: list["Param"] = []
    sources: list[Source] = []
    type: "TypeD"  # This is the return type!
    inheritedFrom: Any = None
    parent_member_properties: MemberProperties = {}  # type: ignore[typeddict-item]

    def _path_segments(self, base_dir: str) -> list[str]:
        return []

    def return_type(self, converter: Converter) -> list[ir.Return]:
        """Return the Returns a function signature can have.

        Because, in TypeDoc, each signature can have only 1 @return tag, we
        return a list of either 0 or 1 item.

        """
        if self.type.type == "intrinsic" and self.type.name == "void":
            # Returns nothing
            return []

        return [
            ir.Return(
                type=self.type.render_name(converter),
                description=self.comment.get_tag_one("returns"),
            )
        ]

    def _fix_type_suffix(self) -> None:
        if self.path[-1] == "__type":
            self.path = self.path[:-1]
            self.path[-1] = self.path[-1].removesuffix(".")
            self.name = self.path[-1]

    def _destructure_param(self, param: Param) -> list[Param]:
        """We want to document a destructured argument as if it were several
        separate arguments. This finds complex inline object types in the arguments
        list of a function and "destructures" them into separately documented arguments.

        E.g., a function

            /**
            * @param options
            * @destructure options
            */
            function f({x , y } : {
                /** The x value */
                x : number,
                /** The y value */
                y : string
            }){ ... }

        should be documented like:

            options.x (number) The x value
            options.y (number) The y value
        """
        type = param.type
        assert isinstance(type, ReflectionType)
        decl = type.declaration
        result = []
        for child in decl.children:
            assert isinstance(child, Member)
            child_param = Param(
                name=param.name + "." + child.name, flags=Flags(), type=child.type
            )
            result.append(child_param)
        return result

    def _destructure_params(self) -> list[Param]:
        destructure_targets = []
        for tag in self.comment.blockTags:
            if tag.tag == "@destructure":
                destructure_targets = tag.content[0].text.split(" ")
                break

        if not destructure_targets:
            return self.parameters

        params = []
        for p in self.parameters:
            if p.name in destructure_targets:
                params.extend(self._destructure_param(p))
        return params

    def render(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        yield "("

        def inner(param: Param) -> Iterator[str | ir.TypeXRef]:
            yield param.name + ": "
            yield from param.type._render_name(converter)

        yield from riffle((inner(param) for param in self.parameters), ", ")

        yield ") => "
        return_type = self.return_type(converter)
        if return_type:
            ret = return_type[0].type
        else:
            ret = "void"
        assert ret
        if isinstance(ret, str):
            yield ret
        else:
            yield from ret

    def to_ir(
        self, converter: Converter
    ) -> tuple[ir.Function | None, Sequence["Node"]]:
        if self.inheritedFrom is not None:
            if self.comment == Comment():
                return None, []

        if self.name.startswith("["):
            # a symbol.
            # \u2024 looks like a period but is not a period.
            # This isn't ideal, but otherwise the coloring is weird.
            self.name = "[Symbol\u2024" + self.name[1:]
        self._fix_type_suffix()
        params = self._destructure_params()
        # Would be nice if we could statically determine that the function was
        # defined with `async` keyword but this is probably good enough
        is_async = isinstance(self.type, ReferenceType) and self.type.name == "Promise"
        # This is the real meat of a function, method, or constructor.
        #
        # Constructors' .name attrs end up being like 'new Foo'. They
        # should probably be called "constructor", but I'm not bothering
        # with that yet because nobody uses that attr on constructors atm.
        result = ir.Function(
            params=[p.to_ir(converter) for p in params],
            # Exceptions are discouraged in TS as being unrepresentable in its
            # type system. More importantly, TypeDoc does not support them.
            exceptions=[],
            # Though perhaps technically true, it looks weird to the user
            # (and in the template) if constructors have a return value:
            type_params=[
                x.to_ir(converter) for x in (self.typeParameter or self.typeParameters)
            ],
            returns=self.return_type(converter)
            if self.kindString != "Constructor signature"
            else [],
            is_async=is_async,
            **self.parent_member_properties,
            **self._top_level_properties(),
        )
        return result, self.children


def riffle(
    t: Iterable[Iterable[str | ir.TypeXRef]], other: str
) -> Iterator[str | ir.TypeXRef]:
    it = iter(t)
    try:
        yield from next(it)
    except StopIteration:
        return
    for i in it:
        yield other
        yield from i


class TypeBase(BaseModel):
    typeArguments: list["TypeD"] = []

    def render_name(self, converter: Converter) -> list[str | ir.TypeXRef]:
        return list(self._render_name(converter))

    def _render_name(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        yield from self._render_name_root(converter)

        if not self.typeArguments:
            return
        yield "<"
        gen = (arg._render_name(converter) for arg in self.typeArguments)
        yield from riffle(gen, ", ")
        yield ">"

    def _render_name_root(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        raise NotImplementedError


class AndOrType(TypeBase):
    type: Literal["union", "intersection"]
    types: list["TypeD"]

    def _render_name_root(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        if self.type == "union":
            symbol = "|"
        elif self.type == "intersection":
            symbol = " & "
        gen = (t._render_name(converter) for t in self.types)
        yield from riffle(gen, symbol)


class ArrayType(TypeBase):
    type: Literal["array"] = "array"
    elementType: "TypeD"

    def _render_name_root(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        yield from self.elementType._render_name(converter)
        yield "[]"


class OperatorType(TypeBase):
    type: Literal["typeOperator"]
    operator: str
    target: "TypeD"

    def _render_name_root(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        yield self.operator + " "
        yield from self.target._render_name(converter)


class Target(BaseModel):
    sourceFileName: str
    qualifiedName: str


class IntrinsicType(TypeBase):
    type: Literal["intrinsic"] = "intrinsic"
    name: str

    def _render_name_root(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        yield self.name


class ReferenceType(TypeBase):
    type: Literal["reference"] = "reference"
    name: str
    target: int | Target | None
    package: str | None = None
    refersToTypeParameter: bool = False

    def _render_name_root(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        if self.refersToTypeParameter:
            yield self.name
            return
        if isinstance(self.target, int) and self.target > 0:
            node = converter.index[self.target]
            yield ir.TypeXRefInternal(self.name, node.path)
            return
        assert isinstance(self.target, Target)
        assert self.package
        yield ir.TypeXRefExternal(
            self.name,
            self.package,
            self.target.sourceFileName,
            self.target.qualifiedName,
        )


class ReflectionType(TypeBase):
    type: Literal["reflection"]
    declaration: Node

    def _render_name_root(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        if isinstance(self.declaration, TypeLiteral):
            yield from self.declaration.render(converter)
            return

        if isinstance(self.declaration, Callable):
            if self.declaration.kindString == "Constructor":
                yield "{new "
            yield from self.declaration.signatures[0].render(converter)
            if self.declaration.kindString == "Constructor":
                yield "}"
            return
        yield "<TODO: reflection>"
        return


class LiteralType(TypeBase):
    type: Literal["literal"] = "literal"
    value: Any

    def _render_name_root(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        if self.value is None:
            yield "null"
            return
        # TODO: it could be a bigint or a string?
        if isinstance(self.value, int):
            yield "number"
            return
        yield "<TODO: Unknown type>"
        return


class TupleType(TypeBase):
    type: Literal["tuple"] = "tuple"
    elements: list["TypeD"]

    def _render_name_root(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        types = (t._render_name(converter) for t in self.elements)
        yield "["
        yield from riffle(types, ", ")
        yield "]"


class OtherType(TypeBase):
    type: Literal["indexedAccess"]

    def _render_name_root(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        yield "<TODO: not implemented>"


class UnknownType(TypeBase):
    type: Literal["unknown"]
    name: str

    def _render_name_root(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        yield self.name


class PredicateType(TypeBase):
    type: Literal["predicate"]
    name: str
    targetType: "TypeD"

    def _render_name_root(self, converter: Converter) -> Iterator[str | ir.TypeXRef]:
        yield self.name
        yield " is "
        yield from self.targetType.render_name(converter)


AnyNode = Node | Project | Signature


Type = (
    AndOrType
    | ArrayType
    | LiteralType
    | OtherType
    | OperatorType
    | IntrinsicType
    | ReferenceType
    | ReflectionType
    | TupleType
    | PredicateType
    | UnknownType
)

TypeD = Annotated[Type, Field(discriminator="type")]
OptionalTypeD = Annotated[Type | None, Field(discriminator="type")]

IndexType = Node | Project | Signature | Param | TypeParameter


for cls in list(globals().values()):
    if isclass(cls) and issubclass(cls, BaseModel):
        cls.update_forward_refs()


# Fix error messages
#
# The Pydantic error messages tend to contain TONS of irrelevant stuff. This
# deletes the useless stuff and adds important context.

discriminators = ["kindString", "type"]
classesByDiscriminator: dict[str, dict[str, type[BaseModel]]] = {
    disc: {} for disc in discriminators
}

for cls in list(globals().values()):
    if not isclass(cls) or not issubclass(cls, BaseModel):
        continue
    for disc in discriminators:
        if disc not in cls.__annotations__:
            continue
        ann = cls.__annotations__[disc]
        if getattr(ann, "__name__", None) != "Literal":
            continue
        for arg in ann.__args__:
            classesByDiscriminator[disc][arg] = cls


def fix_exc_errors(json: Any, exc: ValidationError) -> None:
    from pprint import pprint

    s = sorted(
        set(e["loc"][:-1] for e in exc.errors()), key=lambda loc: (-len(loc), loc)
    )
    handled = set()
    errors = []
    for loc in s:
        if loc in handled:
            continue
        # Add all prefixes of the current loc to handled
        for i in range(len(s)):
            handled.add(loc[:i])

        # follow path to get problematic subobject
        o = json
        for a in loc:
            o = o[a]

        # Work out the discriminator and use it to look up the appropriate class
        for disc in discriminators:
            if disc not in o:
                continue
            if o[disc] not in classesByDiscriminator[disc]:
                continue
            c = classesByDiscriminator[disc][o[disc]]
            break
        else:
            print("Unable to locate discriminator for object:")
            pprint(o, depth=1)
            continue

        try:
            c.parse_obj(o)
        except ValidationError as e:
            errs = e.errors()
        else:
            continue

        for err in errs:
            # Extend the loc so that it is relative to the top level object
            err["loc"] = loc + err["loc"]
            err["msg"] += "\n" + f"  Discriminator: {disc} = {o[disc]}\n"
            print("\n")
            print("loc:", err["loc"])
            print("msg:", err["msg"])
            print("obj:")
            pprint(o, depth=1)
            print("\n")
        errors.extend(errs)

    if errors:
        exc._error_cache = errors  # type:ignore[attr-defined]
