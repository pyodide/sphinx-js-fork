"""Converter from TypeDoc output to IR format"""

import pathlib
import re
import subprocess
from collections.abc import Sequence
from errno import ENOENT
from functools import cache
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


def typedoc_output(
    abs_source_paths: list[str], sphinx_conf_dir: str | pathlib.Path, config_path: str
) -> "Project":
    """Return the loaded JSON output of the TypeDoc command run over the given
    paths."""
    typedoc = search_node_modules("typedoc", "typedoc/bin/typedoc", sphinx_conf_dir)
    command = Command("node")
    command.add(typedoc)
    if config_path:
        tsconfig_path = str((Path(sphinx_conf_dir) / config_path).absolute())
        command.add("--tsconfig", tsconfig_path)
    typedoc_version, _ = typedoc_version_info(typedoc)
    if typedoc_version >= (0, 22, 0):
        command.add("--entryPointStrategy", "expand")

    with NamedTemporaryFile(mode="w+b") as temp:
        command.add("--json", temp.name, *abs_source_paths)
        try:
            subprocess.run(command.make())
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
                if isinstance(node.getSignature, list):
                    sig = node.getSignature[0]
                else:
                    sig = node.getSignature
                node.getSignature = sig
                children.append([sig])
            if node.setSignature:
                if isinstance(node.setSignature, list):
                    sig = node.setSignature[0]
                else:
                    sig = node.setSignature
                node.setSignature = sig
                children.append([sig])

        if isinstance(node, Callable):
            children.append(node.signatures)

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


class Summary(BaseModel):
    kind: Literal["text"]
    text: str


class Tag(BaseModel):
    tag: str
    content: list[Summary]


class Comment(BaseModel):
    returns: str = ""
    shortText: str | None
    text: str | None
    summary: list[Summary] | None
    blockTags: list[Tag] = []

    def get_returns(self) -> str:
        result = self.returns.strip()
        if result:
            return result
        for tag in self.blockTags:
            if tag.tag == "@returns":
                return tag.content[0].text.strip()
        return ""


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
    description: str
    line: int | None
    deprecated: bool
    examples: list[str]
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
        return dict(
            name=self.short_name(),
            path=ir.Pathname(self.path),
            filename=basename(self.filename),
            deppath=self.filename,
            description=make_description(self.comment),
            line=self.sources[0].line if self.sources else None,
            # These properties aren't supported by TypeDoc:
            deprecated=False,
            examples=[],
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
    getSignature: "list[Signature] | Signature" = []
    setSignature: "list[Signature] | Signature" = []

    def to_ir(self, converter: Converter) -> tuple[ir.Attribute, Sequence["Node"]]:
        if self.getSignature:
            # There's no signature to speak of for a getter: only a return type.
            type = self.getSignature.type  # type: ignore[union-attr]
        else:
            # ES6 says setters have exactly 1 param. I'm not sure if they
            # can have multiple signatures, though.
            type = self.setSignature.parameters[0].type  # type: ignore[union-attr]
        res = ir.Attribute(
            type=type.render_name(converter),
            **self.member_properties(),
            **self._top_level_properties(),
        )
        return res, self.children


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
            if t.type == "reference" and t.id is not None:
                rtype = converter.index[t.id]
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

    def to_ir(self, converter: Converter) -> tuple[ir.Attribute, Sequence["Node"]]:
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


class OtherNode(NodeBase):
    kindString: Literal[
        "Enumeration",
        "Enumeration Member",  # M changed to uppercase in version 0.22
        "Enumeration member",
        "Namespace",
        "Type alias",
        "Reference",
    ]


Node = Annotated[
    Accessor | Callable | Class | ExternalModule | Interface | Member | OtherNode,
    Field(discriminator="kindString"),
]

ClassChild = Annotated[Accessor | Callable | Member, Field(discriminator="kindString")]


def make_description(comment: Comment) -> str:
    """Construct a single comment string from a fancy object."""
    if comment.summary:
        ret = comment.summary[0].text
    else:
        ret = "\n\n".join(text for text in [comment.shortText, comment.text] if text)
    return ret.strip()


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
            self.name, extends, description=make_description(self.comment)
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
            description=make_description(self.comment),
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
        "Constructor signature", "Call signature", "Get signature", "Set signature"
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
                description=self.comment.get_returns(),
            )
        ]

    def to_ir(
        self, converter: Converter
    ) -> tuple[ir.Function | None, Sequence["Node"]]:
        if self.inheritedFrom is not None:
            if self.comment == Comment():
                return None, []
        # This is the real meat of a function, method, or constructor.
        #
        # Constructors' .name attrs end up being like 'new Foo'. They
        # should probably be called "constructor", but I'm not bothering
        # with that yet because nobody uses that attr on constructors atm.
        result = ir.Function(
            params=[p.to_ir(converter) for p in self.parameters],
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
            **self.parent_member_properties,
            **self._top_level_properties(),
        )
        return result, self.children


class TypeBase(Base):
    typeArguments: list["TypeD"] = []

    def render_name(self, converter: Converter) -> str:
        name = self._render_name_root(converter)

        if self.typeArguments:
            arg_names = ", ".join(
                arg.render_name(converter) for arg in self.typeArguments
            )
            name += f"<{arg_names}>"

        return name

    def _render_name_root(self, converter: Converter) -> str:
        raise NotImplementedError


class AndOrType(TypeBase):
    type: Literal["union", "intersection"]
    types: list["TypeD"]

    def _render_name_root(self, converter: Converter) -> str:
        if self.type == "union":
            return "|".join(t.render_name(converter) for t in self.types)
        elif self.type == "intersection":
            return " & ".join(t.render_name(converter) for t in self.types)


class ArrayType(TypeBase):
    type: Literal["array"]
    elementType: "TypeD"

    def _render_name_root(self, converter: Converter) -> str:
        return self.elementType.render_name(converter) + "[]"


class OperatorType(TypeBase):
    type: Literal["typeOperator"]
    operator: str
    target: "TypeD"

    def _render_name_root(self, converter: Converter) -> str:
        return self.operator + " " + self.target.render_name(converter)


class ReferenceType(TypeBase):
    type: Literal["reference", "intrinsic"]
    name: str
    id: int | None

    def _render_name_root(self, converter: Converter) -> str:
        # test_generic_member() (currently skipped) tests this.
        if self.id:
            node = converter.index[self.id]
            assert node.name
        return self.name


class ReflectionType(TypeBase):
    type: Literal["reflection"]

    def _render_name_root(self, converter: Converter) -> str:
        return "<TODO: reflection>"


class LiteralType(TypeBase):
    type: Literal["literal"]
    value: Any

    def _render_name_root(self, converter: Converter) -> str:
        if self.value is None:
            return "null"
        # TODO: it could be a bigint or a string?
        if isinstance(self.value, int):
            return "number"
        return "<TODO: Unknown type>"


class TupleType(TypeBase):
    type: Literal["tuple"]
    elements: list["TypeD"]

    def _render_name_root(self, converter: Converter) -> str:
        types = [t.render_name(converter) for t in self.elements]
        return "[" + ", ".join(types) + "]"


class OtherType(TypeBase):
    type: Literal["indexedAccess"]

    def _render_name_root(self, converter: Converter) -> str:
        return "<TODO: not implemented>"


AnyNode = Node | Project | Signature


Type = (
    AndOrType
    | ArrayType
    | LiteralType
    | OtherType
    | OperatorType
    | ReferenceType
    | ReflectionType
    | TupleType
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
