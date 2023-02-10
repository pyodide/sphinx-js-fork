from inspect import isclass
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseConfig, BaseModel, Field


class Source(BaseModel):
    fileName: str
    line: int


class Comment(BaseModel):
    shortText: str | None
    text: str | None
    returns: str = ""


class Flags(BaseModel):
    isAbstract: bool = False
    isRest: bool = False
    isOptional: bool = False
    isStatic: bool = False
    isPrivate: bool = False
    isExported: bool = False


class Base(BaseModel):
    parent: Optional["IndexType"]
    id: int | None
    children: list["Node"] = []
    inheritedFrom: Any = None
    kindString: str = ""
    originalName: str | None

    class Config(BaseConfig):
        fields = {"parent": {"exclude": True}}  # type:ignore[dict-item]


class Root(Base):
    kindString: Literal["root"] = "root"
    sources: list[Source] = [] # probably never present
    flags: "Flags" = Field(default_factory=Flags)
    name: str | None


class Callable(Base):
    sources: list[Source]
    kindString: Literal[
        "Constructor",
        "Method",
        "Function",
    ]
    signatures: list["Signature"] = []
    flags: "Flags" = Field(default_factory=Flags)
    name: str


class Member(Base):
    kindString: Literal[
        "Property",
        "Variable",
    ]
    sources: list[Source]
    type: "Type"
    name: str
    flags: "Flags" = Field(default_factory=Flags)
    comment: Comment = Field(default_factory=Comment)


class ManyNode(Base):
    sources: list[Source]
    kindString: Literal[
        "Class",
        "External module",
        "Module",
        "Interface",
        "Accessor",
        "Type alias",
        "Enumeration",
        "Enumeration member",
    ]
    type: Optional["Type"]
    name: str

    # setSignature: list["Signature"]
    # getSignature: list["Signature"]
    # signatures: list["Signature"]

    # Only for Interface
    extendedTypes: list["Type"] = []
    # Only for Interface and Class
    implementedTypes: list["Type"] = []
    # Only for accessor
    getSignature: list["Signature"] = []
    setSignature: list["Signature"] = []

    comment: Comment = Field(default_factory=Comment)
    flags: "Flags" = Field(default_factory=Flags)


Node = Annotated[ManyNode | Callable | Member, Field(discriminator="kindString")]


class Signature(Base):
    sources: list[Source] = []
    kindString: Literal[
        "Constructor signature", "Call signature", "Get signature", "Set signature"
    ]
    type: "Type"
    comment: Comment = Field(default_factory=Comment)
    parameters: list["Param"] = []
    flags: "Flags" = Field(default_factory=Flags)
    name: str


class Param(Base):
    name: str
    type: "Type"
    kindString: Literal["Parameter"] = "Parameter"
    comment: Comment = Field(default_factory=Comment)
    defaultValue: str | None
    flags: Flags


class TypeBase(Base):
    typeArguments: list["Type"] = []


class ReferenceType(TypeBase):
    type: Literal["reference", "intrinsic"]
    name: str
    id: int | None


class StringLiteralType(TypeBase):
    type: Literal["stringLiteral"]
    name: str
    value: str


class ArrayType(TypeBase):
    type: Literal["array"]
    elementType: "Type"


class TupleType(TypeBase):
    type: Literal["tuple"]
    elements: list["Type"]


class AndOrType(TypeBase):
    type: Literal["union", "intersection"]
    types: list["Type"]


class OperatorType(TypeBase):
    type: Literal["typeOperator"]
    operator: str
    target: "Type"


class ParameterType(TypeBase):
    type: Literal["typeParameter"]
    name: str
    constraint: Optional["Type"]


class UnknownType(TypeBase):
    type: Literal["unknown"]
    name: str


class ReflectionType(TypeBase):
    type: Literal["reflection"]


AnyNode = Node | Root | Signature


Type = Annotated[
    (
        ReferenceType
        | StringLiteralType
        | ArrayType
        | TupleType
        | AndOrType
        | OperatorType
        | ParameterType
        | UnknownType
        | ReflectionType
    ),
    Field(discriminator="type"),
]

IndexType = Node | Root | Signature | Param


for cls in list(globals().values()):
    if isclass(cls) and issubclass(cls, BaseModel):
        cls.update_forward_refs()
