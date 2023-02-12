"""Converter from TypeDoc output to IR format"""

import re
import subprocess
from collections.abc import Sequence
from errno import ENOENT
from inspect import isclass
from json import load
from os.path import basename, join, normpath, relpath, sep, splitext
from tempfile import NamedTemporaryFile
from typing import Annotated, Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, ValidationError
from sphinx.application import Sphinx
from sphinx.errors import SphinxError

from . import ir
from .analyzer_utils import Command, is_explicitly_rooted
from .suffix_tree import SuffixTree

__all__ = ["Analyzer"]


def typedoc_output(
    abs_source_paths: list[str], sphinx_conf_dir: str, config_path: str
) -> "Root":
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
        return parse(load(temp))


def parse(json: dict[str, Any]) -> "Root":
    try:
        return Root.parse_obj(json)
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
    ) -> None:
        if node.id is not None:  # 0 is okay; it's the root node.
            self.index[node.id] = node

        parent_kind = parent.kindString if parent else ""
        parent_segments = parent.path if parent else []
        self.compute_path(node, parent_kind, parent_segments)

        if node.kindString in ["External module", "Module"]:
            containing_module = node.path

        if node.flags.isExported:
            node.exported_from = containing_module

        if parent and isinstance(node, Signature):
            node.parent_member_properties = parent.member_properties()

        # Burrow into everything that could contain more ID'd items. We don't
        # need setSignature or getSignature for now. Do we need indexSignature?
        children: list[Sequence[IndexType]] = []

        children.append(node.children)
        if isinstance(node, Callable):
            children.append(node.signatures)

        if isinstance(node, Signature):
            children.append(node.parameters)

        for child in (c for l in children for c in l):
            self._populate_index_inner(
                child, parent=node, containing_module=containing_module
            )

    def compute_path(
        self, node: "IndexType", parent_kind: str, parent_segments: list[str]
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

        segs = node._path_segments(self.base_dir)

        if segs and parent_segments:
            segments = list(parent_segments)
            segments[-1] += delimiter
            segments.extend(segs)
        else:
            segments = segs or parent_segments

        node.path = segments

    def convert_all_nodes(self, root: "Root") -> list[ir.TopLevel]:
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
    def __init__(self, json: "Root", base_dir: str):
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


class Comment(BaseModel):
    returns: str = ""
    shortText: str | None
    text: str | None


class Flags(BaseModel):
    isAbstract: bool = False
    isExported: bool = False
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
    flags: Flags = Field(default_factory=Flags)

    exported_from: list[str] | None = None

    def member_properties(self) -> MemberProperties:
        return dict(
            is_abstract=self.flags.isAbstract,
            is_optional=self.flags.isOptional,
            is_static=self.flags.isStatic,
            is_private=self.flags.isPrivate,
        )

    def _containing_deppath(self) -> str | None:
        """Return the path pointing to the module containing the given node.
        The path is absolute or relative to `root_for_relative_js_paths`.
        Raises ValueError if one isn't found.

        """
        return self.sources[0].fileName

    def _path_segments(self, base_dir: str) -> list[str]:
        raise NotImplementedError


class Root(Base):
    # These are probably never present except "name"
    kindString: Literal["root"] = "root"
    name: str | None

    def _path_segments(self, base_dir: str) -> list[str]:
        return []


class TopLevelPropertiesDict(TypedDict):
    name: str
    path: ir.Pathname
    filename: str
    deppath: str | None
    description: str
    line: int
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
        source = self.sources[0]
        return dict(
            name=self.short_name(),
            path=ir.Pathname(self.path),
            filename=basename(source.fileName),
            deppath=self._containing_deppath(),
            description=make_description(self.comment),
            line=source.line,
            # These properties aren't supported by TypeDoc:
            deprecated=False,
            examples=[],
            see_alsos=[],
            properties=[],
            exported_from=ir.Pathname(self.exported_from)
            if self.exported_from
            else None,
        )

    def to_ir(
        self, converter: Converter
    ) -> tuple[ir.TopLevel | None, Sequence["Node"]]:
        return None, self.children


class NodeBase(TopLevelProperties):
    sources: list[Source]

    def _path_segments(self, base_dir: str) -> list[str]:
        return [self.name]


class Accessor(NodeBase):
    kindString: Literal["Accessor"]
    getSignature: list["Signature"] = []
    setSignature: list["Signature"] = []

    def to_ir(self, converter: Converter) -> tuple[ir.Attribute, Sequence["Node"]]:
        if self.getSignature:
            # There's no signature to speak of for a getter: only a return type.
            type = self.getSignature[0].type
        else:
            # ES6 says setters have exactly 1 param. I'm not sure if they
            # can have multiple signatures, though.
            type = self.setSignature[0].parameters[0].type
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
        if not is_explicitly_rooted(rel):
            rel = f".{sep}{rel}"

        segments = rel.split(sep)
        filename = splitext(segments[-1])[0]
        return [s + "/" for s in segments[:-1]] + [filename]


class OtherNode(NodeBase):
    kindString: Literal["Enumeration", "Enumeration member", "Namespace", "Type alias"]


Node = Annotated[
    Accessor | Callable | Class | ExternalModule | Interface | Member | OtherNode,
    Field(discriminator="kindString"),
]

ClassChild = Annotated[Accessor | Callable | Member, Field(discriminator="kindString")]


def make_description(comment: Comment) -> str:
    """Construct a single comment string from a fancy object."""
    ret = "\n\n".join(text for text in [comment.shortText, comment.text] if text)
    return ret.strip()


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
    parameters: list["Param"] = []
    sources: list[Source] = []
    type: "TypeD"
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
                description=self.comment.returns.strip(),
            )
        ]

    def to_ir(
        self, converter: Converter
    ) -> tuple[ir.Function | None, Sequence["Node"]]:
        if self.inheritedFrom is not None:
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


class ParameterType(TypeBase):
    type: Literal["typeParameter"]
    name: str
    constraint: Optional["TypeD"]

    def _render_name_root(self, converter: Converter) -> str:
        name = self.name
        if self.constraint is not None:
            name += " extends " + self.constraint.render_name(converter)
            # e.g. K += extends + keyof T
        return name


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


class StringLiteralType(TypeBase):
    type: Literal["stringLiteral"]
    name: str
    value: str

    def _render_name_root(self, converter: Converter) -> str:
        return f'"{self.value}"'


class TupleType(TypeBase):
    type: Literal["tuple"]
    elements: list["TypeD"]

    def _render_name_root(self, converter: Converter) -> str:
        types = [t.render_name(converter) for t in self.elements]
        return "[" + ", ".join(types) + "]"


class UnknownType(TypeBase):
    type: Literal["unknown"]
    name: str

    def _render_name_root(self, converter: Converter) -> str:
        if re.match(r"-?\d*(\.\d+)?", self.name):  # It's a number.
            # TypeDoc apparently sticks numeric constants' values into the
            # type name. String constants? Nope. Function ones? Nope.
            return "number"
        return self.name


AnyNode = Node | Root | Signature


Type = (
    AndOrType
    | ArrayType
    | OperatorType
    | ParameterType
    | ReferenceType
    | ReflectionType
    | StringLiteralType
    | TupleType
    | UnknownType
)

TypeD = Annotated[Type, Field(discriminator="TypeD")]

IndexType = Node | Root | Signature | Param


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
        exc._error_cache = errors
