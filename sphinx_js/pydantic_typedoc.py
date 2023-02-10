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
    kindString: Literal["root"] = "root"
    flags: "Flags" = Field(default_factory=Flags)
    name: str | None


class Callable(Base):
    kindString: Literal[
        "Constructor",
        "Method",
        "Function",
    ]
    sources: list[Source]
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
    sources: list[Source]
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
    kindString: Literal[
        "Constructor signature", "Call signature", "Get signature", "Set signature"
    ]
    parent: Optional["ManyNode | Callable"]
    sources: list[Source] = []
    type: "Type"
    comment: Comment = Field(default_factory=Comment)
    parameters: list["Param"] = []
    flags: "Flags" = Field(default_factory=Flags)
    name: str


class Param(Base):
    kindString: Literal["Parameter"] = "Parameter"
    name: str
    type: "Type"
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
