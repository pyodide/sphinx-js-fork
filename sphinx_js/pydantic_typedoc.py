import re
from inspect import isclass
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseConfig, BaseModel, Field, ValidationError


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


class Base(BaseModel):
    children: list["Node"] = []
    id: int | None
    inheritedFrom: Any = None
    kindString: str = ""
    originalName: str | None
    parent: Optional["IndexType"]

    class Config(BaseConfig):
        fields = {"parent": {"exclude": True}}  # type:ignore[dict-item]


class Root(Base):
    # These are probably never present except "name"
    kindString: Literal["root"] = "root"
    flags: "Flags" = Field(default_factory=Flags)
    name: str | None


class NodeBase(Base):
    comment: Comment = Field(default_factory=Comment)
    flags: "Flags" = Field(default_factory=Flags)
    name: str
    sources: list[Source]


class Accessor(NodeBase):
    kindString: Literal["Accessor"]
    getSignature: list["Signature"] = []
    setSignature: list["Signature"] = []


class Callable(NodeBase):
    kindString: Literal[
        "Constructor",
        "Method",
        "Function",
    ]
    signatures: list["Signature"] = []


class Class(NodeBase):
    kindString: Literal["Class"]
    extendedTypes: list["TypeD"] = []
    implementedTypes: list["TypeD"] = []


class Interface(NodeBase):
    kindString: Literal["Interface"]
    extendedTypes: list["TypeD"] = []


class Member(NodeBase):
    kindString: Literal[
        "Property",
        "Variable",
    ]
    type: "TypeD"


class ExternalModule(NodeBase):
    kindString: Literal["External module"]
    originalName: str


class OtherNode(NodeBase):
    kindString: Literal[
        "Module",
        "Namespace",
        "Type alias",
        "Enumeration",
        "Enumeration member",
    ]


Node = Annotated[
    Accessor | Callable | Class | ExternalModule | Interface | Member | OtherNode,
    Field(discriminator="kindString"),
]


class Param(Base):
    kindString: Literal["Parameter"] = "Parameter"
    comment: Comment = Field(default_factory=Comment)
    defaultValue: str | None
    flags: Flags
    name: str
    type: "TypeD"


class Signature(Base):
    kindString: Literal[
        "Constructor signature", "Call signature", "Get signature", "Set signature"
    ]
    parent: "Node" = None  # type:ignore[assignment]

    comment: Comment = Field(default_factory=Comment)
    flags: Flags = Field(default_factory=Flags)
    name: str
    parameters: list["Param"] = []
    sources: list[Source] = []
    type: "TypeD"


class TypeBase(Base):
    typeArguments: list["TypeD"] = []

    def render_name(self, index: dict[int, "IndexType"]) -> str:
        name = self._render_name_root(index)

        if self.typeArguments:
            arg_names = ", ".join(arg.render_name(index) for arg in self.typeArguments)
            name += f"<{arg_names}>"

        return name

    def _render_name_root(self, index: dict[int, "IndexType"]) -> str:
        raise NotImplementedError


class AndOrType(TypeBase):
    type: Literal["union", "intersection"]
    types: list["TypeD"]

    def _render_name_root(self, index: dict[int, "IndexType"]) -> str:
        if self.type == "union":
            return "|".join(t.render_name(index) for t in self.types)
        elif self.type == "intersection":
            return " & ".join(t.render_name(index) for t in self.types)


class ArrayType(TypeBase):
    type: Literal["array"]
    elementType: "TypeD"

    def _render_name_root(self, index: dict[int, "IndexType"]) -> str:
        return self.elementType.render_name(index) + "[]"


class OperatorType(TypeBase):
    type: Literal["typeOperator"]
    operator: str
    target: "TypeD"

    def _render_name_root(self, index: dict[int, "IndexType"]) -> str:
        return self.operator + " " + self.target.render_name(index)


class ParameterType(TypeBase):
    type: Literal["typeParameter"]
    name: str
    constraint: Optional["TypeD"]

    def _render_name_root(self, index: dict[int, "IndexType"]) -> str:
        name = self.name
        if self.constraint is not None:
            name += " extends " + self.constraint.render_name(index)
            # e.g. K += extends + keyof T
        return name


class ReferenceType(TypeBase):
    type: Literal["reference", "intrinsic"]
    name: str
    id: int | None

    def _render_name_root(self, index: dict[int, "IndexType"]) -> str:
        # test_generic_member() (currently skipped) tests this.
        if self.id:
            node = index[self.id]
            assert node.name
        return self.name


class ReflectionType(TypeBase):
    type: Literal["reflection"]

    def _render_name_root(self, index: dict[int, "IndexType"]) -> str:
        return "<TODO: reflection>"


class StringLiteralType(TypeBase):
    type: Literal["stringLiteral"]
    name: str
    value: str

    def _render_name_root(self, index: dict[int, "IndexType"]) -> str:
        return f'"{self.value}"'


class TupleType(TypeBase):
    type: Literal["tuple"]
    elements: list["TypeD"]

    def _render_name_root(self, index: dict[int, "IndexType"]) -> str:
        types = [t.render_name(index) for t in self.elements]
        return "[" + ", ".join(types) + "]"


class UnknownType(TypeBase):
    type: Literal["unknown"]
    name: str

    def _render_name_root(self, index: dict[int, "IndexType"]) -> str:
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


def parse(json: dict[str, Any]) -> Root:
    try:
        return Root.parse_obj(json)
    except ValidationError as exc:
        fix_exc_errors(json, exc)
        raise


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

    exc._error_cache = errors
