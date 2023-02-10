from inspect import isclass
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseConfig, BaseModel, Field


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


class AndOrType(TypeBase):
    type: Literal["union", "intersection"]
    types: list["TypeD"]


class ArrayType(TypeBase):
    type: Literal["array"]
    elementType: "TypeD"


class OperatorType(TypeBase):
    type: Literal["typeOperator"]
    operator: str
    target: "TypeD"


class ParameterType(TypeBase):
    type: Literal["typeParameter"]
    name: str
    constraint: Optional["TypeD"]


class ReferenceType(TypeBase):
    type: Literal["reference", "intrinsic"]
    name: str
    id: int | None


class ReflectionType(TypeBase):
    type: Literal["reflection"]


class StringLiteralType(TypeBase):
    type: Literal["stringLiteral"]
    name: str
    value: str


class TupleType(TypeBase):
    type: Literal["tuple"]
    elements: list["TypeD"]


class UnknownType(TypeBase):
    type: Literal["unknown"]
    name: str


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
