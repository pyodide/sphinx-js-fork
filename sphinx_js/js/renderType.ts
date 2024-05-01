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
import { parseFilePath } from "./convertTopLevel.ts";
import { ReadonlySymbolToType } from "./redirectPrivateAliases.ts";

/**
 * Render types into a list of strings and XRefs.
 *
 * Most visitor nodes should be similar to the implementation of getTypeString
 * on the same type.
 *
 * TODO: implement the remaining not implemented cases and add test coverage.
 */
class TypeRenderer implements TypeVisitor<Type> {
  private readonly basePath: string;
  // For resolving XRefs.
  private readonly reflToPath: ReadonlyMap<
    DeclarationReflection | SignatureReflection,
    string[]
  >;
  private readonly symbolToType: ReadonlySymbolToType;

  constructor(
    basePath: string,
    reflToPath: ReadonlyMap<
      DeclarationReflection | SignatureReflection,
      string[]
    >,
    symbolToType: ReadonlySymbolToType,
  ) {
    this.basePath = basePath;
    this.reflToPath = reflToPath;
    this.symbolToType = symbolToType;
  }

  /**
   * Helper for inserting type parameters
   */
  addTypeArguments(
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

  /**
   * Render the type, maybe add parentheses
   */
  render(type: SomeType, context: TypeContext): Type {
    const result = type.visit(this);
    if (type.needsParenthesis(context)) {
      result.unshift("(");
      result.push(")");
    }
    return result;
  }

  conditional(type: ConditionalType): Type {
    throw new Error("Not implemented");
  }
  indexedAccess(type: IndexedAccessType): Type {
    // TODO: switch to correct impl
    return ["<TODO: not implemented indexedAccess>"];
    return [
      ...this.render(type.objectType, TypeContext.indexedObject),
      "[",
      ...this.render(type.indexType, TypeContext.indexedIndex),
      "]",
    ];
  }
  inferred(type: InferredType): Type {
    throw new Error("Not implemented");
  }
  intersection(type: IntersectionType): Type {
    const result: Type = [];
    for (const elt of type.types) {
      result.push(...this.render(elt, TypeContext.intersectionElement));
      result.push(" & ");
    }
    result.pop();
    return result;
  }
  intrinsic(type: IntrinsicType): Type {
    return [intrinsicType(type.name)];
  }
  literal(type: LiteralType): Type {
    if (type.value === null) {
      return [intrinsicType("null")];
    }
    return [JSON.stringify(type.value)];
  }
  mapped(type: MappedType): Type {
    throw new Error("Not implemented");
  }
  optional(type: OptionalType): Type {
    throw new Error("Not implemented");
  }
  predicate(type: PredicateType): Type {
    // Consider using typedoc's representation for this instead of this custom
    // string.
    return [
      intrinsicType("boolean"),
      " (typeguard for ",
      ...type.targetType!.visit(this),
      ")",
    ];
  }
  query(type: QueryType): Type {
    return [
      "typeof ",
      ...this.render(type.queryType, TypeContext.queryTypeTarget),
    ];
  }
  /**
   * If it's a reference to a private type alias, replace it with a reflection.
   * Otherwise return undefined.
   */
  convertPrivateReferenceToReflection(type: ReferenceType): Type | undefined {
    if (type.reflection) {
      const refl = type.reflection as DeclarationReflection;

      // If it's private, we don't really want to emit an XRef to it. In the
      // typedocPlugin.ts we tried to calculate Reflections for these, so now
      // we try to look it up. I couldn't get the line+column numbers to match
      // up so in this case we index on file name and reference name.

      // Another place where we incorrectly handle merged declarations
      const src = refl.sources![0];
      const newTarget = this.symbolToType.get(
        `${src.fullFileName}:${refl.name}`,
      );
      if (newTarget) {
        // TODO: this doesn't handle parentheses correctly.
        return newTarget.visit(this);
      }
      return undefined;
    }

    if (!type.symbolId) {
      throw new Error("This should not happen");
    }
    // See if this refers to a private type. In that case we should inline the
    // type reflection rather than referring to the non-exported name.
    const newTarget = this.symbolToType.get(
      `${type.symbolId.fileName}:${type.symbolId.pos}`,
    );
    if (newTarget) {
      // TODO: this doesn't handle parentheses correctly.
      return newTarget.visit(this);
    }
    return undefined;
  }
  /**
   * Convert a reference type to either an XRefExternal or an XRefInternal. It
   * works on things that `convertPrivateReferenceToReflection` but it will
   * throw an error if the type `isIntentionallyBroken`.
   *
   * This logic is also used for relatedTypes for classes (extends and
   * implements).
   * TODO: handle type arguments in extends and implements.
   */
  convertReferenceToXRef(type: ReferenceType): Type {
    if (type.isIntentionallyBroken()) {
      throw new Error("Bad type");
    }

    if (type.reflection) {
      const path = this.reflToPath.get(
        type.reflection as DeclarationReflection,
      );
      if (!path) {
        throw new Error(
          `Broken internal xref to ${type.reflection?.toStringHierarchy()}`,
        );
      }
      const xref: TypeXRefInternal = {
        name: type.name,
        path,
        type: "internal",
      };
      return this.addTypeArguments(type, [xref]);
    }

    if (!type.symbolId) {
      throw new Error("This shouldn't happen");
    }

    const path = parseFilePath(type.symbolId.fileName, this.basePath);
    if (path.includes("node_modules/")) {
      // External reference
      const xref: TypeXRefExternal = {
        name: type.name,
        package: type.package!,
        qualifiedName: type.symbolId.qualifiedName || null,
        sourcefilename: type.symbolId.fileName || null,
        type: "external",
      };
      return this.addTypeArguments(type, [xref]);
    } else {
      const xref: TypeXRefInternal = {
        name: type.name,
        path,
        type: "internal",
      };
      return this.addTypeArguments(type, [xref]);
    }
  }

  reference(type: ReferenceType): Type {
    let res;
    if (type.isIntentionallyBroken()) {
      // If it's intentionally broken, don't add an xref. It's probably a type
      // parameter.
      return this.addTypeArguments(type, [type.name]);
    } else {
      // if we got a reflection use that. It's not all that clear how to deal
      // with type arguments here though...
      res = this.convertPrivateReferenceToReflection(type);
      // else use convertReferenceToXRef
      if (res) {
        return res;
      }
      return this.convertReferenceToXRef(type);
    }
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
    const result: Type = [];
    for (const elt of type.elements) {
      result.push(...this.render(elt, TypeContext.tupleElement));
      result.push(", ");
    }
    result.pop();
    result.unshift("[");
    result.push("]");
    return result;
  }
  namedTupleMember(type: NamedTupleMember): Type {
    const result: Type = [`${type.name}${type.isOptional ? "?" : ""}: `];
    result.push(...this.render(type.element, TypeContext.tupleElement));
    return result;
  }
  typeOperator(type: TypeOperatorType): Type {
    return [
      type.operator,
      " ",
      ...this.render(type.target, TypeContext.typeOperatorTarget),
    ];
  }
  union(type: UnionType): Type {
    const result: Type = [];
    for (const elt of type.types) {
      result.push(...this.render(elt, TypeContext.unionElement));
      result.push(" | ");
    }
    result.pop();
    return result;
  }
  unknown(type: UnknownType): Type {
    // I'm not sure how we get here: generally nobody explicitly annotates
    // unknown, maybe it's inferred sometimes?
    return [type.name];
  }
  array(t: ArrayType): Type {
    const res = this.render(t.elementType, TypeContext.arrayElement);
    res.push("[]");
    return res;
  }

  renderSignature(sig: SignatureReflection): Type {
    const result: Type = ["("];
    for (const param of sig.parameters || []) {
      result.push(param.name + ": ");
      result.push(...(param.type?.visit(this) || []));
      result.push(", ");
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
      // There's no exact TypeContext for indexedAccess b/c typedoc doesn't
      // render it like this. mappedParameter and mappedTemplate look quite
      // similar:
      // [k in mappedParam]: mappedTemplate
      //  vs
      // [k: keyType]: valueType
      const keyType = this.render(key.type!, TypeContext.mappedParameter);
      const valueType = this.render(
        index_sig.type!,
        TypeContext.mappedTemplate,
      );
      result.push("[", key.name, ": ");
      result.push(...keyType);
      result.push("]", ": ");
      result.push(...valueType);
      result.push("; ");
    }
    for (const child of lit.children || []) {
      result.push(child.name);
      if (child.flags.isOptional) {
        result.push("?: ");
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
  basePath: string,
  reflToPath: ReadonlyMap<
    DeclarationReflection | SignatureReflection,
    string[]
  >,
  symbolToType: ReadonlySymbolToType,
  type: SomeType,
  context: TypeContext = TypeContext.none,
): Type {
  const renderer = new TypeRenderer(basePath, reflToPath, symbolToType);
  return renderer.render(type, context);
}

export function referenceToXRef(
  basePath: string,
  reflToPath: ReadonlyMap<
    DeclarationReflection | SignatureReflection,
    string[]
  >,
  symbolToType: ReadonlySymbolToType,
  type: ReferenceType,
): Type {
  const renderer = new TypeRenderer(basePath, reflToPath, symbolToType);
  return renderer.convertReferenceToXRef(type);
}
