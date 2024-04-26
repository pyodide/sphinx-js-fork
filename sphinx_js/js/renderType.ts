import {
  ArrayType,
  ConditionalType,
  DeclarationReflection,
  IndexedAccessType,
  InferredType,
  IntersectionType,
  IntrinsicType,
  LiteralType,
  MappedType,
  NamedTupleMember,
  OptionalType,
  PredicateType,
  QueryType,
  ReferenceType,
  ReflectionKind,
  ReflectionType,
  RestType,
  Serializer,
  SignatureReflection,
  SomeType,
  TemplateLiteralType,
  TupleType,
  TypeContext,
  TypeOperatorType,
  TypeVisitor,
  UnionType,
  UnknownType,
} from "typedoc";
import {
  Type,
  TypeXRefExternal,
  TypeXRefInternal,
  intrinsicType,
} from "./ir.ts";

class TypeRenderer implements TypeVisitor<Type> {
  reflToPath: Map<DeclarationReflection | SignatureReflection, string[]>;
  constructor(
    reflToPath: Map<DeclarationReflection | SignatureReflection, string[]>,
  ) {
    this.reflToPath = reflToPath;
  }
  addTypeParams(
    type: { typeArguments?: SomeType[] | undefined },
    l: Type,
  ): Type {
    if (!type.typeArguments || type.typeArguments.length === 0) {
      return l;
    }
    l.push("<");
    for (const arg of type.typeArguments) {
      l.push(...arg.visit(this));
      l.push(", ");
    }
    l.pop();
    l.push(">");
    return l;
  }

  conditional(type: ConditionalType): Type {
    throw new Error("Not implemented");
  }
  indexedAccess(type: IndexedAccessType): Type {
    throw new Error("Not implemented");
  }
  inferred(type: InferredType): Type {
    throw new Error("Not implemented");
  }
  intersection(type: IntersectionType): Type {
    throw new Error("Not implemented");
  }
  intrinsic(type: IntrinsicType): Type {
    return [intrinsicType(type.name)];
  }
  literal(type: LiteralType): Type {
    if (type.value === "null") {
      return [intrinsicType("null")];
    }
    if (typeof type.value === "number") {
      return [intrinsicType("number")];
    }
    throw new Error("Not implemented");
  }
  mapped(type: MappedType): Type {
    throw new Error("Not implemented");
  }
  optional(type: OptionalType): Type {
    throw new Error("Not implemented");
  }
  predicate(type: PredicateType): Type {
    return [
      intrinsicType("boolean"),
      " (typeguard for ",
      ...type.targetType!.visit(this),
      ")",
    ];
  }
  query(type: QueryType): Type {
    throw new Error("Not implemented");
  }
  reference(type: ReferenceType): Type {
    if (type.isIntentionallyBroken()) {
      return this.addTypeParams(type, [type.name]);
    }
    // TODO: should we pass down app.serializer? app?
    const fakeSerializer = { projectRoot: process.cwd() } as Serializer;
    const fileInfo = type.symbolId?.toObject(fakeSerializer);
    if (type.package) {
      const res: TypeXRefExternal = {
        name: type.name,
        package: type.package!,
        qualifiedName: fileInfo?.qualifiedName,
        sourcefilename: fileInfo?.sourceFileName,
        type: "external",
      };
      return this.addTypeParams(type, [res]);
    }
    const path = this.reflToPath.get(type.reflection as DeclarationReflection);
    if (!path) {
      throw new Error(
        `Broken internal xref to ${type.reflection?.toStringHierarchy()}`,
      );
    }
    const res: TypeXRefInternal = {
      name: type.name,
      path,
      type: "internal",
    };
    return this.addTypeParams(type, [res]);
  }
  reflection(type: ReflectionType): Type {
    if (type.declaration.kind === ReflectionKind.TypeLiteral) {
      return this.renderTypeLiteral(type.declaration);
    }
    if (type.declaration.kind === ReflectionKind.Constructor) {
      const result = this.renderSignature(type.declaration.signatures![0]);
      result.unshift("{new ");
      result.push("}");
      return result;
    }
    if (
      [ReflectionKind.Function, ReflectionKind.Method].includes(
        type.declaration.kind,
      )
    ) {
      return this.renderSignature(type.declaration.signatures![0]);
    }
    throw new Error("Not implemented");
  }
  rest(type: RestType): Type {
    throw new Error("Not implemented");
  }
  templateLiteral(type: TemplateLiteralType): Type {
    throw new Error("Not implemented");
  }
  tuple(type: TupleType): Type {
    throw new Error("Not implemented");
  }
  namedTupleMember(type: NamedTupleMember): Type {
    throw new Error("Not implemented");
  }
  typeOperator(type: TypeOperatorType): Type {
    throw new Error("Not implemented");
  }
  union(type: UnionType): Type {
    throw new Error("Not implemented");
  }
  unknown(type: UnknownType): Type {
    return [type.name];
  }
  array(t: ArrayType): Type {
    const res = t.elementType.visit(this);
    if (t.elementType.needsParenthesis(TypeContext.arrayElement)) {
      return ["(", ...res, ")[]"];
    }
    return [...res, "[]"];
  }

  renderSignature(sig: SignatureReflection): Type {
    const result: Type = ["("];
    for (const param of sig.parameters || []) {
      result.push(param.name + ": ");
      result.push(...(param.type?.visit(this) || []));
      result.push(",");
    }
    if (sig.parameters?.length) {
      result.pop();
    }
    result.push(") => ");
    if (sig.type) {
      result.push(...sig.type.visit(this));
    } else {
      result.push(intrinsicType("void"));
    }
    return result;
  }

  renderTypeLiteral(lit: DeclarationReflection): Type {
    if (lit.signatures) {
      return this.renderSignature(lit.signatures[0]);
    }
    const result: Type = ["{ "];
    const index_sig = lit.indexSignature;
    if (index_sig) {
      if (index_sig.parameters?.length !== 1) {
        throw new Error("oops");
      }
      const key = index_sig.parameters[0];
      result.push("[", key.name, ": ");
      result.push(...(key.type?.visit(this) || []));
      result.push("]", ": ");
      result.push(...(index_sig.type?.visit(this) || []));
      result.push("; ");
    }
    for (const child of lit.children || []) {
      result.push(child.name);
      if (child.flags.isOptional) {
        result.push("?:");
      } else {
        result.push(": ");
      }
      result.push(...(child.type?.visit(this) || []));
      result.push("; ");
    }
    result.push("}");
    return result;
  }
}

export function renderType(
  reflToPath: Map<DeclarationReflection | SignatureReflection, string[]>,
  a: SomeType,
): Type {
  return a.visit(new TypeRenderer(reflToPath));
}
