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
import { renderType } from "./renderType";
import {
  TopLevelIR,
  IRFunction,
  Attribute,
  TopLevel,
  Description,
  DescriptionItem,
  Param,
  NO_DEFAULT,
  Type,
  intrinsicType,
  Return,
  memberProps,
  Class,
  Interface,
  Pathname,
} from "./ir";
import { delimiter, relative } from "path";

type ConvertResult = [
  TopLevelIR | undefined,
  DeclarationReflection[] | undefined,
];
// def _populate_index_inner(
//     self,
//     node: "IndexType",
//     parent: "IndexType | None",
//     idmap: dict[str, "Target"],
//     filepath: list[str] | None = None,
// ) -> None:
//     if node.id is not None:  # 0 is okay; it's the root node.
//         self.index[node.id] = node

//     parent_kind = parent.kindString if parent else ""
//     parent_segments = parent.path if parent else []
//     if str(node.id) in idmap:
//         filepath = _parse_filepath(
//             idmap[str(node.id)].sourceFileName, self.base_dir
//         )
//     if filepath:
//         node.filepath = filepath
//     self.compute_path(node, parent_kind, parent_segments, filepath)

//     if parent and isinstance(node, Signature):
//         node.parent_member_properties = parent.member_properties()

//     # Burrow into everything that could contain more ID'd items
//     for child in node.children_with_ids():
//         self._populate_index_inner(
//             child, parent=node, idmap=idmap, filepath=filepath
//         )

// def compute_path(
//     self,
//     node: "IndexType",
//     parent_kind: str,
//     parent_segments: list[str],
//     filepath: list[str] | None,
// ) -> None:
//     """Compute the full, unambiguous list of path segments that points to an
//     entity described by a TypeDoc JSON node.

//     Example: ``['./', 'dir/', 'dir/', 'file.', 'object.', 'object#', 'object']``

//     TypeDoc uses a totally different, locality-sensitive resolution mechanism
//     for links: https://typedoc.org/guides/link-resolution/. It seems like a
//     less well thought-out system than JSDoc's namepaths, as it doesn't
//     distinguish between, say, static and instance properties of the same name.
//     (AFAICT, TypeDoc does not emit documentation for inner properties, as for a
//     function nested within another function.) We're sticking with our own
//     namepath-like paths, even if we eventually support {@link} syntax.
//     """
//     delimiter = "."
//     if not node.flags.isStatic and parent_kind == "Class":
//         delimiter = "#"

//     filepath2 = filepath or []
//     parent_segments = parent_segments or filepath2

//     segs = node._path_segments(self.base_dir)

//     if segs and parent_segments:
//         segments = list(parent_segments)
//         segments[-1] += delimiter
//         segments.extend(segs)
//     else:
//         segments = segs or parent_segments

//     node.path = segments

// def _parse_filepath(path: str, base_dir: str) -> list[str]:
//     p = Path(path).resolve()
//     if p.is_relative_to(base_dir):
//         p = p.relative_to(base_dir)
//     else:
//         # It's not under base_dir... maybe it's in a global node_modules or
//         # something? This makes it look the same as if it were under a local
//         # node_modules.
//         for a in p.parents:
//             if a.name == "node_modules":
//                 p = p.relative_to(a.parent)
//                 break

//     if p.name:
//         p = p.with_suffix("")
//     entries = ["."] + list(p.parts)
//     for i in range(len(entries) - 1):
//         entries[i] += "/"
//     return entries

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
    return [this.functionToIR(func), func.children];
  }
  convertMethod(func: DeclarationReflection): ConvertResult {
    return [this.functionToIR(func), func.children];
  }
  convertConstructor(func: DeclarationReflection): ConvertResult {
    return [this.functionToIR(func), func.children];
  }
  convertVariable(v: DeclarationReflection): ConvertResult {
    const type = v.type ? renderType(this.pathMap, v.type) : undefined;
    const result: Attribute = {
      ...memberProps(v),
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
      ...memberProps(prop),
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
      ...memberProps(prop),
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

  topLevelProperties(a: DeclarationReflection | SignatureReflection): TopLevel {
    if (!a.sources) {
      // console.log("no sources");
      // console.log(a);
    }
    if (!this.pathMap.has(a)) {
      console.log("Missing path", a);
      process.exit(1);
    }
    const block_tags = this.getCommentBlockTags(a.comment);
    let deprecated: Description | boolean =
      block_tags["deprecated"]?.[0] || false;
    if (deprecated && deprecated.length === 0) {
      deprecated = true;
    }
    return {
      name: a.name,
      path: this.pathMap.get(a),
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
      line: a.sources?.[0]?.line || -1,
      // deprecated: deprecated,
      // examples: self.comment.get_tag_list("example"),
    };
  }
  paramToIR(param: ParameterReflection): Param {
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
      type = renderType(this.pathMap, param.type);
    }
    return {
      name: param.name,
      description: this.getCommentDescription(param.comment),
      has_default: !!param.defaultValue,
      default: param.defaultValue || NO_DEFAULT,
      is_variadic: param.flags.isRest,
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
      ...memberProps(func),
      is_async,
      params: params?.map(this.paramToIR.bind(this)) || [],
      type_params: [],
      returns,
      exceptions: [],
      kind: "functions",
    };
  }
}
