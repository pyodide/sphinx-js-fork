import {
  Comment,
  CommentDisplayPart,
  DeclarationReflection,
  ParameterReflection,
  ProjectReflection,
  ReflectionKind,
  ReflectionVisitor,
  SignatureReflection,
  SomeType,
} from "typedoc";
import { renderType } from "./renderType.ts";
import {
  NO_DEFAULT,
  Attribute,
  Class,
  Description,
  DescriptionItem,
  Interface,
  IRFunction,
  Member,
  Param,
  Pathname,
  Return,
  TopLevelIR,
  TopLevel,
  Type,
} from "./ir.ts";
import { delimiter, relative } from "path";

type ConvertResult = [
  TopLevelIR | undefined,
  DeclarationReflection[] | undefined,
];

function parseFilePath(path: string, base_dir: string): string[] {
  const rel = relative(base_dir, path);
  let pathSegments: string[];
  if (!rel.startsWith("..")) {
    pathSegments = rel.split(delimiter);
  } else {
    pathSegments = path.split(delimiter);
    pathSegments.reverse();
    const idx = pathSegments.indexOf("node_modules");
    if (idx !== -1) {
      pathSegments = pathSegments.slice(0, idx + 1);
    }
    pathSegments.reverse();
  }
  let lastEntry = pathSegments.pop();
  if (lastEntry !== undefined) {
    pathSegments.push(lastEntry.slice(0, lastEntry.lastIndexOf(".")));
  }
  pathSegments.unshift(".");
  for (let i = 0; i < pathSegments.length - 1; i++) {
    pathSegments[i] += "/";
  }
  return pathSegments;
}

class PathComputer implements ReflectionVisitor {
  parentKind: ReflectionKind | undefined;
  parentSegments: string[];
  pathMap: Map<DeclarationReflection | SignatureReflection, Pathname>;
  filePathMap: Map<DeclarationReflection | SignatureReflection, Pathname>;
  basePath: string;
  filePath: string[];
  constructor(
    pathMap: Map<DeclarationReflection | SignatureReflection, Pathname>,
    filePathMap: Map<DeclarationReflection | SignatureReflection, Pathname>,
    basePath: string,
  ) {
    this.pathMap = pathMap;
    this.filePathMap = filePathMap;
    this.basePath = basePath;
    this.parentKind = undefined;
    this.parentSegments = [];
    this.filePath = [];
  }

  fixSymbolName(refl: DeclarationReflection | SignatureReflection) {
    const SYMBOL_PREFIX = "[Symbol\u2024";
    if (refl.name.startsWith("[") && !refl.name.startsWith(SYMBOL_PREFIX)) {
      // # a symbol.
      // # \u2024 looks like a period but is not a period.
      // # This isn't ideal, but otherwise the coloring is weird.
      refl.name = SYMBOL_PREFIX + refl.name.slice(1);
    }
  }

  computePath(
    node: DeclarationReflection | SignatureReflection,
    parentKind: string,
    parentSegments: string[],
    filePath: string[],
  ): Pathname {
    let delimiter = ".";
    if (!node.flags.isStatic && this.parentKind === ReflectionKind.Class) {
      delimiter = "#";
    }
    parentSegments = parentSegments.length > 0 ? parentSegments : filePath;
    let segs = [node.name];
    if (
      [
        ReflectionKind.Module,
        ReflectionKind.ConstructorSignature,
        ReflectionKind.CallSignature,
      ].includes(node.kind)
    ) {
      segs = [];
    }
    console.log("parentSegments", node.name, parentSegments, "segs", segs);
    let segments;
    if (segs.length && parentSegments.length) {
      segments = Array.from(parentSegments);
      segments[segments.length - 1] += delimiter;
      segments.push(...segs);
    } else {
      segments = segs.length ? segs : parentSegments;
    }
    this.pathMap.set(node, segments);
    this.filePathMap.set(node, this.filePath);
    return segments;
  }

  project(rel: ProjectReflection) {
    rel.children?.forEach((x) => x.visit(this));
  }
  declaration(refl: DeclarationReflection) {
    this.fixSymbolName(refl);
    if (refl.sources) {
      this.filePath = parseFilePath(refl.sources![0].fileName, this.basePath);
    }
    const origParentSegs = this.parentSegments;
    const origParentKind = this.parentKind;
    this.parentSegments = this.computePath(
      refl,
      "",
      origParentSegs,
      this.filePath,
    );
    this.parentKind = refl.kind;
    refl.children?.forEach((child) => child.visit(this));
    refl.signatures?.forEach((child) => child.visit(this));
    this.parentSegments = origParentSegs;
    this.parentKind = origParentKind;
    // throw new Error("Not implemented");
  }
  signature(refl: SignatureReflection) {
    this.computePath(refl, "", this.parentSegments, this.filePath);
    this.fixSymbolName(refl);
    // throw new Error("Not implemented");
  }
}

export class Converter {
  pathMap: Map<DeclarationReflection | SignatureReflection, Pathname>;
  filePathMap: Map<DeclarationReflection | SignatureReflection, Pathname>;
  constructor() {
    this.pathMap = new Map();
    this.filePathMap = new Map();
  }

  populateIndex(a: ProjectReflection, basePath: string) {
    a.visit(new PathComputer(this.pathMap, this.filePathMap, basePath));
  }

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
    const convertFunc = `convert${kind}` as keyof this;
    if (!this[convertFunc]) {
      throw new Error(`No known converter for kind ${kind}`);
    }
    // @ts-ignore
    return this[convertFunc](object);
  }

  convertModule(mod: DeclarationReflection): ConvertResult {
    return [undefined, mod.children];
  }

  convertFunction(func: DeclarationReflection): ConvertResult {
    return [this.functionToIR(func), func.children];
  }
  convertMethod(func: DeclarationReflection): ConvertResult {
    return [this.functionToIR(func), func.children];
  }
  convertConstructor(func: DeclarationReflection): ConvertResult {
    return [this.functionToIR(func), func.children];
  }
  convertVariable(v: DeclarationReflection): ConvertResult {
    if (!v.type) {
      throw new Error(`Type of ${v.name} is undefined`);
    }
    const type = renderType(this.pathMap, v.type);
    const result: Attribute = {
      ...this.memberProps(v),
      ...this.topLevelProperties(v),
      kind: "attributes",
      type,
    };
    // ir.Attribute(
    //     type=self.type.render_name(converter),
    //     **self.member_properties(),
    //     **self._top_level_properties(),
    // )
    return [result, v.children];
  }

  convertClass(cls: DeclarationReflection): ConvertResult {
    const [constructor_, members] = this.constructorAndMembers(cls);
    const result: Class = {
      constructor_,
      members,
      supers: this.relatedTypes(cls, "extendedTypes"),
      is_abstract: cls.flags.isAbstract,
      interfaces: this.relatedTypes(cls, "implementedTypes"),
      type_params: [],
      ...this.topLevelProperties(cls),
      kind: "classes",
    };
    return [result, cls.children];
  }
  relatedTypes(
    cls: DeclarationReflection,
    kind: "extendedTypes" | "implementedTypes",
  ): Pathname[] {
    const origTypes = cls[kind] || [];
    const result: Pathname[] = [];
    for (const t of origTypes) {
      if (t.type !== "reference") {
        continue;
      }
      result.push(this.pathMap.get(t.reflection as DeclarationReflection)!);
    }
    return result;
  }

  convertInterface(cls: DeclarationReflection): ConvertResult {
    const [_, members] = this.constructorAndMembers(cls);
    const result: Interface = {
      members,
      supers: this.relatedTypes(cls, "extendedTypes"),
      type_params: [],
      ...this.topLevelProperties(cls),
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
      // return self.type.declaration.to_ir(converter)
    }
    const result: Attribute = {
      type: renderType(this.pathMap, prop.type!),
      ...this.memberProps(prop),
      ...this.topLevelProperties(prop),
      description: this.getCommentDescription(prop.comment),
      kind: "attributes",
    };
    return [result, prop.children];
  }
  convertAccessor(prop: DeclarationReflection): ConvertResult {
    let type: SomeType;
    let sig: SignatureReflection;
    if (prop.getSignature) {
      // There's no signature to speak of for a getter: only a return type.
      sig = prop.getSignature;
      type = sig.type!;
    } else {
      if (!prop.setSignature) {
        throw new Error("???");
      }
      // ES6 says setters have exactly 1 param.
      sig = prop.setSignature;
      type = sig.parameters![0].type!;
    }
    const result: Attribute = {
      type: renderType(this.pathMap, type),
      ...this.memberProps(prop),
      ...this.topLevelProperties(prop),
      kind: "attributes",
    };
    result.description = this.getCommentDescription(sig.comment);
    return [result, prop.children];
  }

  convertClassChild(child: DeclarationReflection): IRFunction | Attribute {
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
    return this.toIr(child)[0] as IRFunction | Attribute;
  }
  constructorAndMembers(
    a: DeclarationReflection,
  ): [IRFunction | null, (IRFunction | Attribute)[]] {
    let constructor: IRFunction | null = null;
    const members: (IRFunction | Attribute)[] = [];
    for (const child of a.children || []) {
      if (child.inheritedFrom) {
        continue;
      }
      if (child.kind === ReflectionKind.Constructor) {
        // This really, really should happen exactly once per class.
        constructor = this.functionToIR(child);
        constructor.returns = [];
        continue;
      }
      members.push(this.convertClassChild(child));
    }
    return [constructor, members];
  }
  renderCommentContent(content: CommentDisplayPart[]) {
    return content.map((x): DescriptionItem => {
      if (x.kind === "code") {
        return { type: "code", code: x.text };
      }
      if (x.kind === "text") {
        return { type: "text", text: x.text };
      }
      throw new Error("Not implemented");
    });
  }

  getCommentDescription(c: Comment | undefined): Description {
    if (!c) {
      return [];
    }
    return this.renderCommentContent(c.summary);
  }
  getCommentBlockTags(c: Comment | undefined): {
    [key: string]: Description[];
  } {
    if (!c) {
      return {};
    }
    const result: { [key: string]: Description[] } = {};
    for (const tag of c.blockTags) {
      const tagType = tag.tag.slice(1);
      if (!(tagType in result)) {
        result[tagType] = [];
      }
      const content: Description = [];
      if (tag.name) {
        content.push({
          type: "name",
          text: tag.name,
        });
      }
      content.push(...this.renderCommentContent(tag.content));
      result[tagType].push(content);
    }
    return result;
  }

  memberProps(a: DeclarationReflection): Member {
    return {
      is_abstract: a.flags.isAbstract,
      is_optional: a.flags.isOptional,
      is_static: a.flags.isStatic,
      is_private: a.flags.isPrivate,
    };
  }

  topLevelProperties(a: DeclarationReflection | SignatureReflection): TopLevel {
    if (!a.sources) {
      // console.log("no sources");
      // console.log(a);
    }
    const path = this.pathMap.get(a);
    if (!path) {
      console.log();
      throw new Error(`Missing path for ${a.name}`);
    }
    const block_tags = this.getCommentBlockTags(a.comment);
    let deprecated: Description | boolean =
      block_tags["deprecated"]?.[0] || false;
    if (deprecated && deprecated.length === 0) {
      deprecated = true;
    }
    return {
      name: a.name,
      path,
      deppath: this.filePathMap.get(a)?.join(""),
      filename: "",
      description: this.getCommentDescription(a.comment),
      modifier_tags: [],
      block_tags,
      deprecated,
      examples: block_tags["example"] || [],
      properties: [],
      see_alsos: [],
      exported_from: this.filePathMap.get(a),
      // description: self.comment.get_description(),
      // modifier_tags: self.comment.modifierTags,
      // block_tags: {tag: self.comment.get_tag_list(tag) for tag in self.comment.tags},
      line: a.sources?.[0]?.line || null,
      // deprecated: deprecated,
      // examples: self.comment.get_tag_list("example"),
    };
  }
  paramToIR(param: ParameterReflection): Param {
    let type: Type = [];
    if (param.type) {
      type = renderType(this.pathMap, param.type);
    }
    let description = this.getCommentDescription(param.comment);
    if (description.length === 0 && param.type?.type === "reflection") {
        // TODO: isn't this a weird thing to do here?
        description = this.getCommentDescription(param.type.declaration?.signatures?.[0].comment);
    }
    return {
      name: param.name,
      has_default: !!param.defaultValue,
      default: param.defaultValue || NO_DEFAULT,
      is_variadic: param.flags.isRest,
      description,
      type,
    };
  }
  functionToIR(func: DeclarationReflection): IRFunction {
    const first_sig = func.signatures![0];
    const params = first_sig.parameters;
    // console.log("params:", params);
    let returns: Return[] = [];
    let is_async = false;
    const voidReturnType =
      func.kind === ReflectionKind.Constructor ||
      !first_sig.type ||
      (first_sig.type.type === "intrinsic" && first_sig.type.name === "void");
    const topLevel = this.topLevelProperties(first_sig);
    if (first_sig.type && !voidReturnType) {
      const returnType = renderType(this.pathMap, first_sig.type);
      const description = topLevel.block_tags.returns?.[0] || [];
      returns = [{ type: returnType, description }];
      is_async =
        first_sig.type.type === "reference" &&
        first_sig.type.name === "Promise";
    }
    let props = {};

    return {
      ...topLevel,
      ...this.memberProps(func),
      is_async,
      params: params?.map(this.paramToIR.bind(this)) || [],
      type_params: [],
      returns,
      exceptions: [],
      kind: "functions",
    };
  }
}
