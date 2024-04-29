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
  // First we want to know if path is under base_dir.
  // Get directions from base_dir to the path
  const rel = relative(base_dir, path);
  let pathSegments: string[];
  if (!rel.startsWith("..")) {
    // We don't have to go up so path is under base_dir
    pathSegments = rel.split(delimiter);
  } else {
    // It's not under base_dir... maybe it's in a global node_modules or
    // something? This makes it look the same as if it were under a local
    // node_modules.
    pathSegments = path.split(delimiter);
    pathSegments.reverse();
    const idx = pathSegments.indexOf("node_modules");
    if (idx !== -1) {
      pathSegments = pathSegments.slice(0, idx + 1);
    }
    pathSegments.reverse();
  }
  // Remove the file suffix from the last entry if it exists. If there is no .,
  // then this will leave it alone.
  let lastEntry = pathSegments.pop();
  if (lastEntry !== undefined) {
    pathSegments.push(lastEntry.slice(0, lastEntry.lastIndexOf(".")));
  }
  // Add a . to the start and a / after every entry so that if we join the
  // entries it looks like the correct relative path.
  // Hopefully it was actually a relative path of some sort...
  pathSegments.unshift(".");
  for (let i = 0; i < pathSegments.length - 1; i++) {
    pathSegments[i] += "/";
  }
  return pathSegments;
}

/**
 * A ReflectionVisitor that computes the path for each reflection for us.
 *
 * We want to compute the paths for both DeclarationReflections and
 * SignatureReflections.
 */
class PathComputer implements ReflectionVisitor {
  // The maps we're trying to fill in.
  readonly pathMap: Map<DeclarationReflection | SignatureReflection, Pathname>;
  readonly filePathMap: Map<
    DeclarationReflection | SignatureReflection,
    Pathname
  >;
  readonly basePath: string;
  // State for the visitor
  parentKind: ReflectionKind | undefined;
  parentSegments: string[];
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

  /**
   * If the name of the reflection is supposed to be a symbol, it should look
   * something like [Symbol.iterator] but typedoc just shows it as [iterator].
   * Downstream lexers to color the docs split on dots, but we don't want that
   * because here the dot is part of the name. Instead, we add a dot lookalike.
   */
  static fixSymbolName(refl: DeclarationReflection | SignatureReflection) {
    const SYMBOL_PREFIX = "[Symbol\u2024";
    if (refl.name.startsWith("[") && !refl.name.startsWith(SYMBOL_PREFIX)) {
      // Probably a symbol (are there other reasons the name would start with "["?)
      // \u2024 looks like a period but is not a period.
      // This isn't ideal, but otherwise the coloring is weird.
      refl.name = SYMBOL_PREFIX + refl.name.slice(1);
    }
  }

  /**
   * The main logic for this visitor. static for easier readability.
   */
  static computePath(
    refl: DeclarationReflection | SignatureReflection,
    parentKind: ReflectionKind,
    parentSegments: string[],
    filePath: string[],
  ): Pathname {
    // If no parentSegments, this is a "root", use the file path as the
    // parentSegments.
    // We have to copy the segments because we're going to mutate it.
    const segments = Array.from(
      parentSegments.length > 0 ? parentSegments : filePath,
    );
    const suppressReflName = [
      // Module names are redundant with the file path
      ReflectionKind.Module,
      // Signature names are redundant with the callable. TODO: do we want to
      // handle callables with multiple signatures?
      ReflectionKind.ConstructorSignature,
      ReflectionKind.CallSignature,
    ].includes(refl.kind);
    if (suppressReflName) {
      return segments;
    }
    if (segments.length > 0) {
      // Add delimiter. For most things use a . e.g., parent.name but for
      // nonstatic class members we write Class#member
      const delimiter =
        parentKind === ReflectionKind.Class && !refl.flags.isStatic ? "#" : ".";
      segments[segments.length - 1] += delimiter;
    }
    // Add the name of the current reflection to the list
    segments.push(refl.name);
    return segments;
  }

  setPath(refl: DeclarationReflection | SignatureReflection): Pathname {
    PathComputer.fixSymbolName(refl);
    const segments = PathComputer.computePath(
      refl,
      this.parentKind!,
      this.parentSegments,
      this.filePath,
    );
    this.pathMap.set(refl, segments);
    this.filePathMap.set(refl, this.filePath);
    return segments;
  }

  // The visitor methods

  project(rel: ProjectReflection) {
    rel.children?.forEach((x) => x.visit(this));
  }

  declaration(refl: DeclarationReflection) {
    if (refl.sources) {
      this.filePath = parseFilePath(refl.sources![0].fileName, this.basePath);
    }
    const segments = this.setPath(refl);
    // Update state for children
    const origParentSegs = this.parentSegments;
    const origParentKind = this.parentKind;
    this.parentSegments = segments;
    this.parentKind = refl.kind;
    // Visit children
    refl.children?.forEach((child) => child.visit(this));
    refl.signatures?.forEach((child) => child.visit(this));
    // Restore state
    this.parentSegments = origParentSegs;
    this.parentKind = origParentKind;
  }

  signature(refl: SignatureReflection) {
    this.setPath(refl);
  }
}

// Some utilities for manipulating comments

/**
 * Convert CommentDisplayParts from typedoc IR to sphinx-js comment IR.
 * @param content List of CommentDisplayPart
 * @returns
 */
function renderCommentContent(content: CommentDisplayPart[]): Description {
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

function getCommentSummary(c: Comment | undefined): Description {
  if (!c) {
    return [];
  }
  return renderCommentContent(c.summary);
}

/**
 * Compute a map from blockTagName to list of comment descriptions.
 */
function getCommentBlockTags(c: Comment | undefined): {
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
      // If the tag has a name field, add it as a DescriptionName
      content.push({
        type: "name",
        text: tag.name,
      });
    }
    content.push(...renderCommentContent(tag.content));
    result[tagType].push(content);
  }
  return result;
}

/**
 * Main class for creating IR from the ProjectReflection.
 *
 * The main toIr logic is a sort of visitor for ReflectionKinds. We don't use
 * ReflectionVisitor because the division it uses for visitor methods is too
 * coarse.
 *
 * We visit in a breadth-first order, not for any super compelling reason.
 */
export class Converter {
  readonly project: ProjectReflection;
  readonly basePath: string;
  readonly pathMap: Map<DeclarationReflection | SignatureReflection, Pathname>;
  readonly filePathMap: Map<
    DeclarationReflection | SignatureReflection,
    Pathname
  >;

  constructor(project: ProjectReflection, basePath: string) {
    this.project = project;
    this.basePath = basePath;
    this.pathMap = new Map();
    this.filePathMap = new Map();
  }

  computePaths() {
    this.project.visit(
      new PathComputer(this.pathMap, this.filePathMap, this.basePath),
    );
  }

  /**
   * Convert all Reflections.
   */
  convertAll(): TopLevelIR[] {
    const todo = Array.from(this.project.children!);
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

  /**
   * Convert the reflection and return a pair, the conversion result and a list
   * of descendent Reflections to convert. These descendents are either children
   * or signatures.
   *
   * @param object The reflection to convert
   * @returns A pair, a possible result IR object, and a list of descendent
   * Reflections that still need converting.
   */
  toIr(object: DeclarationReflection | SignatureReflection): ConvertResult {
    const kind = ReflectionKind.singularString(object.kind);
    const convertFunc = `convert${kind}` as keyof this;
    if (!this[convertFunc]) {
      throw new Error(`No known converter for kind ${kind}`);
    }
    // @ts-ignore
    return this[convertFunc](object);
  }

  // Reflection visitor methods

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
    return [result, v.children];
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
    // TODO: add a readonly indicator if it's readonly
    const result: Attribute = {
      type: renderType(this.pathMap, prop.type!),
      ...this.memberProps(prop),
      ...this.topLevelProperties(prop),
      description: getCommentSummary(prop.comment),
      kind: "attributes",
    };
    return [result, prop.children];
  }

  /**
   * An Accessor is a thing with a getter or a setter. It should look exactly
   * like a Property in the rendered docs since the distinction is an
   * implementation detail.
   *
   * Specifically:
   * 1. an Accessor with a getter but no setter should be rendered as a readonly
   *    Property.
   * 2. an Accessor with a getter and a setter should be rendered as a
   *    read/write Property
   * 3. Not really sure what to do with an Accessor with a setter and no getter.
   *    That's kind of weird.
   */
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
    // TODO: add a readonly indicator if there's no setter.
    const result: Attribute = {
      type: renderType(this.pathMap, type),
      ...this.memberProps(prop),
      ...this.topLevelProperties(prop),
      kind: "attributes",
    };
    result.description = getCommentSummary(sig.comment);
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
    // Should we assert that the "descendants" component is empty?
    return this.toIr(child)[0] as IRFunction | Attribute;
  }

  /**
   * Generated the IR for the constructor and members of a class or interface.
   * @param refl Class or Interface
   * @returns
   */
  constructorAndMembers(
    refl: DeclarationReflection,
  ): [IRFunction | null, (IRFunction | Attribute)[]] {
    let constructor: IRFunction | null = null;
    const members: (IRFunction | Attribute)[] = [];
    for (const child of refl.children || []) {
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

  /**
   * Compute common properties for all class members.
   */
  memberProps(refl: DeclarationReflection): Member {
    return {
      is_abstract: refl.flags.isAbstract,
      is_optional: refl.flags.isOptional,
      is_static: refl.flags.isStatic,
      is_private: refl.flags.isPrivate,
    };
  }

  /**
   * Compute common properties for all TopLevels.
   */
  topLevelProperties(
    refl: DeclarationReflection | SignatureReflection,
  ): TopLevel {
    const path = this.pathMap.get(refl);
    if (!path) {
      console.log();
      throw new Error(`Missing path for ${refl.name}`);
    }
    const block_tags = getCommentBlockTags(refl.comment);
    let deprecated: Description | boolean =
      block_tags["deprecated"]?.[0] || false;
    if (deprecated && deprecated.length === 0) {
      deprecated = true;
    }
    return {
      name: refl.name,
      path,
      deppath: this.filePathMap.get(refl)?.join(""),
      filename: "",
      description: getCommentSummary(refl.comment),
      modifier_tags: [],
      block_tags,
      deprecated,
      examples: block_tags["example"] || [],
      properties: [],
      see_alsos: [],
      exported_from: this.filePathMap.get(refl),
      line: refl.sources?.[0]?.line || null,
      // modifier_tags: self.comment.modifierTags,
    };
  }
  /**
   * Convert a signature parameter
   */
  paramToIR(param: ParameterReflection): Param {
    let type: Type = [];
    if (param.type) {
      type = renderType(this.pathMap, param.type);
    }
    let description = getCommentSummary(param.comment);
    if (description.length === 0 && param.type?.type === "reflection") {
      // TODO: isn't this a weird thing to do here?
      description = getCommentSummary(
        param.type.declaration?.signatures?.[0].comment,
      );
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
  /**
   * Convert callables: Function, Method, and Constructor.
   * @param func
   * @returns
   */
  functionToIR(func: DeclarationReflection): IRFunction {
    const first_sig = func.signatures![0];
    const params = first_sig.parameters;
    let returns: Return[] = [];
    let is_async = false;
    // We want to suppress the return type for constructors (it's technically
    // correct that it returns a class instance but it looks weird).
    // Also hide explicit void return type.
    const voidReturnType =
      func.kind === ReflectionKind.Constructor ||
      !first_sig.type ||
      (first_sig.type.type === "intrinsic" && first_sig.type.name === "void");
    const topLevel = this.topLevelProperties(first_sig);
    if (!voidReturnType && first_sig.type) {
      // Compute return comment and return annotation.
      const returnType = renderType(this.pathMap, first_sig.type);
      const description = topLevel.block_tags.returns?.[0] || [];
      returns = [{ type: returnType, description }];
      // Put async in front of the function if it returns a Promise.
      // Question: Is there any important difference between an actual async
      // function and a non-async one that returns a Promise?
      is_async =
        first_sig.type.type === "reference" &&
        first_sig.type.name === "Promise";
    }
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
