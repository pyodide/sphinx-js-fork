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
import { Type, TypeXRefExternal, TypeXRefInternal, intrinsicType } from "./ir";
import { isAbsolute } from "path";

function renderSignature(sig: SignatureReflection): Type {
  const result: Type = ["("];
  for (const param of sig.parameters || []) {
    result.push(param.name + ": ");
    result.push(...(param.type?.visit(new A()) || []));
    result.push(",");
  }
  if (sig.parameters?.length) {
    result.pop();
  }
  result.push(") => ");
  if (sig.type) {
    result.push(...sig.type.visit(new A()));
  } else {
    result.push(intrinsicType("void"));
  }
  return result;
}

function renderTypeLiteral(lit: DeclarationReflection): Type {
  if (lit.signatures) {
    return renderSignature(lit.signatures[0]);
  }
  const result: Type = ["{ "];
  const index_sig = lit.indexSignature;
  if (index_sig) {
    if (index_sig.parameters?.length !== 1) {
      throw new Error("oops");
    }
    const key = index_sig.parameters[0];
    result.push("[", key.name, ": ");
    result.push(...(key.type?.visit(new A()) || []));
    result.push("]", ": ");
    result.push(...(index_sig.type?.visit(new A()) || []));
    result.push("; ");
  }
  for (const child of lit.children || []) {
    result.push(child.name);
    if (child.flags.isOptional) {
      result.push("?:");
    } else {
      result.push(": ");
    }
    result.push(...(child.type?.visit(new A()) || []));
    result.push("; ");
  }
  result.push("}");
  return result;
}

class A implements TypeVisitor<Type> {
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
      ...renderType(type.targetType!),
      ")",
    ];
  }
  query(type: QueryType): Type {
    throw new Error("Not implemented");
  }
  reference(type: ReferenceType): Type {
    if (type.refersToTypeParameter) {
      return [type.name];
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
      return [res];
    }
    const res: TypeXRefInternal = {
      name: type.name,
      path: [],
      type: "internal",
    };
    return [res];
  }
  reflection(type: ReflectionType): Type {
    if (type.declaration.kind === ReflectionKind.TypeLiteral) {
      return renderTypeLiteral(type.declaration);
    }
    if (type.declaration.kind === ReflectionKind.Constructor) {
      const result = renderSignature(type.declaration.signatures![0]);
      result.unshift("{new ");
      result.push("}");
      return result;
    }
    if (
      [ReflectionKind.Function, ReflectionKind.Method].includes(
        type.declaration.kind,
      )
    ) {
      return renderSignature(type.declaration.signatures![0]);
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
}

export function renderType(a: SomeType): Type {
  return a.visit(new A());
}
