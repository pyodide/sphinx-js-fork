import {
  DeclarationReference,
  DeclarationReflection,
  ParameterReflection,
  ProjectReflection,
  ReflectionKind,
  SignatureReflection,
} from "typedoc";
import { renderType } from "./renderType";

type TypeXRefIntrinsic = {
  name: string;
  type: "intrinsic";
};

export function intrinsicType(name: string): TypeXRefIntrinsic {
  return {
    name,
    type: "intrinsic",
  };
}

export type TypeXRefInternal = {
  name: string;
  path: string[];
  type: "internal";
};

export type TypeXRefExternal = {
  name: string;
  package: string;
  sourcefilename: string | undefined;
  qualifiedName: string | undefined;
  type: "external";
};

type TypeXRef = TypeXRefExternal | TypeXRefInternal | TypeXRefIntrinsic;
export type Type = (string | TypeXRef)[];

type DescriptionName = {
  text: string;
  type: "name";
};

type DescriptionText = {
  text: string;
  type: "text";
};

type DescriptionCode = {
  code: string;
  type: "code";
};

type DescriptionItem = DescriptionName | DescriptionText | DescriptionCode;
type Description = string | DescriptionItem[];

type Pathname = string[];

type NoDefault = { _noDefault: true };
const NO_DEFAULT = {};

type _Member = {
  is_abstract: boolean;
  is_optional: boolean;
  is_static: boolean;
  is_private: boolean;
};

function memberProps(a: DeclarationReflection): _Member {
  return {
    is_abstract: a.flags.isAbstract,
    is_optional: a.flags.isOptional,
    is_static: a.flags.isStatic,
    is_private: a.flags.isPrivate,
  };
}

type TypeParam = {
  name: string;
  extends: Type;
  description: Description;
};

type ParamBase = {
  name: string;
  description: Description;
  is_variadic: boolean;
  type?: Type;
};

type ParamWithDefault = ParamBase & {
  has_default: boolean;
  default: string | undefined;
};

type ParamNoDefault = ParamBase & {
  has_default: false;
  default: NoDefault;
};

type Param = ParamWithDefault | ParamNoDefault;

type Return = {
  type: Type;
  description: Description;
};

type Module = {
  filename: string;
  deppath?: string;
  path: Pathname;
  line: number;
  attributes: TopLevel[];
  functions: IRFunction[];
  classes: Class[];
};

type TopLevel = {
  name: string;
  path: Pathname;
  filename: string;
  deppath?: string;
  description: Description;
  modifier_tags: string[];
  block_tags: { [key: string]: Description[] };
  line?: number;
  deprecated: Description | boolean;
  examples: Description[];
  see_alsos: string[];
  properties: Attribute[];
  exported_from?: Pathname;
  //   kind: string;
};

type Attribute = TopLevel &
  _Member & {
    type: Type;
    kind: "attributes";
  };

type IRFunction = TopLevel &
  _Member & {
    is_async: boolean;
    params: Param[];
    returns: Return[];
    type_params: TypeParam[];
    kind: "functions";
  };

type _MembersAndSupers = {
  members: (IRFunction | Attribute)[];
  supers: Pathname[];
};

type Interface = TopLevel &
  _MembersAndSupers & {
    type_params: TypeParam[];
    kind: "classes";
  };

type Class = TopLevel &
  _MembersAndSupers & {
    constructor_: IRFunction | undefined;
    is_abstract: boolean;
    interfaces: Pathname[];
    type_params: TypeParam[];
    kind: "classes";
  };

function convertClassChild(
  converter: Converter,
  child: DeclarationReflection,
): IRFunction | Attribute {
  if (
    ![
      ReflectionKind.Accessor,
      ReflectionKind.Constructor,
      ReflectionKind.Method,
      ReflectionKind.Property,
    ].includes(child.kind)
  ) {
    throw new TypeError(
      "Expected an Accessor, Constructor, Method, or Property",
    );
  }
  return converter.toIr(child)[0] as IRFunction | Attribute;
}

function constructorAndMembers(
  converter: Converter,
  a: DeclarationReflection,
): [IRFunction | undefined, (IRFunction | Attribute)[]] {
  let constructor: IRFunction | undefined = undefined;
  const members: (IRFunction | Attribute)[] = [];
  for (const child of a.children || []) {
    if (child.inheritedFrom) {
      continue;
    }
    if (child.kind === ReflectionKind.Constructor) {
      // This really, really should happen exactly once per class.
      constructor = functionToIR(child);
      continue;
    }
    members.push(convertClassChild(converter, child));
  }
  return [constructor, members];
}

function topLevelProperties(a: DeclarationReflection): TopLevel {
  if (!a.sources) {
    // console.log("no sources");
    // console.log(a);
  }
  return {
    name: a.name,
    path: [],
    filename: "",
    description: [],
    modifier_tags: [],
    block_tags: {},
    deprecated: false,
    examples: [],
    properties: [],
    see_alsos: [],
    exported_from: [a.sources?.[0]?.fileName || ""],
    // description: self.comment.get_description(),
    // modifier_tags: self.comment.modifierTags,
    // block_tags: {tag: self.comment.get_tag_list(tag) for tag in self.comment.tags},
    // line: self.sources[0].line if self.sources else None,
    // deprecated: deprecated,
    // examples: self.comment.get_tag_list("example"),
  };
}

function paramToIR(param: ParameterReflection): Param {
  // default = self.defaultValue or ir.NO_DEFAULT
  // return ir.Param(
  //     name=self.name,
  //     description=self.comment.get_description(),
  //     has_default=self.defaultValue is not None,
  //     is_variadic=self.flags.isRest,
  //     # For now, we just pass a single string in as the type rather than
  //     # a list of types to be unioned by the renderer. There's really no
  //     # disadvantage.
  //     type=self.type.render_name(converter),
  //     default=default,
  // )
  let type = [];
  if (param.type) {
    type = renderType(param.type);
  }
  return {
    name: param.name,
    description: [],
    has_default: !!param.defaultValue,
    default: param.defaultValue || NO_DEFAULT,
    is_variadic: param.flags.isRest,
    type,
  };
}

function functionToIR(func: DeclarationReflection): IRFunction {
  const first_sig = func.signatures![0];
  const params = first_sig.parameters;
  // console.log("params:", params);
  let returnType: Type = [intrinsicType("void")];
  let is_async = false;
  if (first_sig.type) {
    returnType = renderType(first_sig.type);
    is_async =
      first_sig.type.type === "reference" && first_sig.type.name === "Promise";
  }
  const returns: Return[] = [{ type: returnType, description: [] }];
  return {
    ...topLevelProperties(func),
    ...memberProps(func),
    is_async,
    params: params?.map(paramToIR) || [],
    type_params: [],
    returns,
    kind: "functions",
  };
}

type TopLevelIR = Attribute | IRFunction | Class | Interface;
type ConvertResult = [
  TopLevelIR | undefined,
  DeclarationReflection[] | undefined,
];

export class Converter {
  convertAll(a: ProjectReflection): TopLevelIR[] {
    const todo = Array.from(a.children!);
    const result: TopLevelIR[] = [];
    while (todo.length) {
      const node = todo.pop()!;
      const [converted, rest] = this.toIr(node);
      if (converted) {
        result.push(converted);
      }
      todo.push(...(rest || []));
    }
    return result;
  }

  toIr(object: DeclarationReflection | SignatureReflection): ConvertResult {
    const kind = ReflectionKind.singularString(object.kind);
    const convertFunc = `convert${kind}`;
    if (!this[convertFunc]) {
      throw new Error(`No known converter for kind ${kind}`);
    }
    return this[convertFunc](object);
  }

  convertModule(mod: DeclarationReflection): ConvertResult {
    return [undefined, mod.children];
  }

  convertFunction(func: DeclarationReflection): ConvertResult {
    return [functionToIR(func), func.children];
  }
  convertMethod(func: DeclarationReflection): ConvertResult {
    return [functionToIR(func), func.children];
  }
  convertConstructor(func: DeclarationReflection): ConvertResult {
    return [functionToIR(func), func.children];
  }
  convertVariable(v: DeclarationReflection): ConvertResult {
    // if (
    //     self.type.type == "reflection"
    //     and isinstance(self.type.declaration, TypeLiteral)
    //     and self.type.declaration.signatures
    // ):
    //     return self.type.declaration.to_ir(converter)
    const result: Attribute = {
      ...memberProps(v),
      ...topLevelProperties(v),
      kind: "attributes",
      type: undefined,
    };
    // ir.Attribute(
    //     type=self.type.render_name(converter),
    //     **self.member_properties(),
    //     **self._top_level_properties(),
    // )
    return [result, v.children];
  }

  convertClass(cls: DeclarationReflection): ConvertResult {
    const [constructor_, members] = constructorAndMembers(this, cls);
    const result: Class = {
      constructor_,
      members,
      supers: [],
      ...memberProps(cls),
      interfaces: [],
      type_params: [],
      ...topLevelProperties(cls),
      kind: "classes",
    };
    return [result, cls.children];
  }
  convertInterface(cls: DeclarationReflection): ConvertResult {
    const [_, members] = constructorAndMembers(this, cls);
    const result: Interface = {
      members,
      supers: [],
      ...memberProps(cls),
      type_params: [],
      ...topLevelProperties(cls),
      kind: "classes",
    };
    return [result, cls.children];
  }

  convertProperty(prop: DeclarationReflection): ConvertResult {
    if (
      prop.type?.type === "reflection" &&
      prop.type.declaration.kind == ReflectionKind.TypeLiteral &&
      prop.type.declaration.signatures?.length
    ) {
      //     return self.type.declaration.to_ir(converter)
    }
    const result: Attribute = {
      type: [],
      ...memberProps(prop),
      ...topLevelProperties(prop),
      kind: "attributes",
    };
    return [result, prop.children];
  }
  convertAccessor(prop: DeclarationReflection): ConvertResult {
    let type;
    if (prop.getSignature) {
      // There's no signature to speak of for a getter: only a return type.
      type = prop.getSignature.type;
    } else {
      if (!prop.setSignature) {
        throw new Error("???");
      }
      // ES6 says setters have exactly 1 param.
      type = prop.setSignature.parameters![0].type;
    }
    const result: Attribute = {
      type: renderType(type),
      ...memberProps(prop),
      ...topLevelProperties(prop),
      kind: "attributes",
    };
    return [result, prop.children];
  }
}
