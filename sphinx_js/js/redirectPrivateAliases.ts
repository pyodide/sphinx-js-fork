/**
 * This is very heavily inspired by typedoc-plugin-missing-exports.
 *
 * The goal isn't to document the missing exports, but rather to remove them
 * from the documentation of actually exported stuff. If someone says:
 *
 * ```
 * type MyPrivateAlias = ...
 *
 * function f(a: MyPrivateAlias) {
 *
 * }
 * ```
 *
 * Then the documentation for f should document the value of MyPrivateAlias. We
 * create a ReflectionType for each missing export and stick them in a
 * SymbolToType map which we add to the application. In renderType.ts, if we
 * have a reference type we check if it's in the SymbolToType map and if so we
 * can use the reflection in place of the reference.
 *
 * More or less unrelatedly, we also add the --sphinxJsConfig option to the
 * options parser so we can pass the sphinxJsConfig on the command line.
 */
import {
  Application,
  Context,
  Converter,
  DeclarationReflection,
  ProjectReflection,
  ReferenceType,
  Reflection,
  ReflectionKind,
  SomeType,
} from "typedoc";
import ts from "typescript";

// Map from the Symbol that is the target of the broken reference to the type
// reflection that it should be replaced by. Depending on whether the reference
// type holds a symbolId or a reflection, we use fileName:position or
// fileName:symbolName as the key (respectively). We could always use the
// symbolName but the position is more specific.
type SymbolToTypeKey = `${string}:${number}` | `${string}:${string}`;
export type SymbolToType = Map<SymbolToTypeKey, SomeType>;
export type ReadonlySymbolToType = ReadonlyMap<SymbolToTypeKey, SomeType>;

const ModuleLike: ReflectionKind =
  ReflectionKind.Project | ReflectionKind.Module;

function getOwningModule(context: Context): Reflection {
  let refl = context.scope;
  // Go up the reflection hierarchy until we get to a module
  while (!refl.kindOf(ModuleLike)) {
    refl = refl.parent!;
  }
  return refl;
}

/**
 * @param app The typedoc app
 * @returns The type reference redirect table to be used in renderType.ts
 */
export function redirectPrivateTypes(app: Application): ReadonlySymbolToType {
  const referencedSymbols = new Map<Reflection, Set<ts.Symbol>>();
  const knownPrograms = new Map<Reflection, ts.Program>();
  const symbolToType: SymbolToType = new Map<`${string}:${number}`, SomeType>();
  app.converter.on(
    Converter.EVENT_CREATE_DECLARATION,
    (context: Context, refl: Reflection) => {
      // TypeDoc 0.26 doesn't fire EVENT_CREATE_DECLARATION for project
      // We need to ensure the project has a program attached to it, so
      // do that when the first declaration is created.
      if (knownPrograms.size === 0) {
        knownPrograms.set(refl.project, context.program);
      }
      if (refl.kindOf(ModuleLike)) {
        knownPrograms.set(refl, context.program);
      }
    },
  );

  /**
   * Get the set of ts.symbols referenced from a ModuleReflection or
   * ProjectReflection if there is only one file.
   */
  function getReferencedSymbols(owningModule: Reflection): Set<ts.Symbol> {
    let set = referencedSymbols.get(owningModule);
    if (set) {
      return set;
    }
    set = new Set();
    referencedSymbols.set(owningModule, set);
    return set;
  }

  function discoverMissingExports(
    owningModule: Reflection,
    context: Context,
  ): ts.Symbol[] {
    // An export is missing if it was referenced and is not contained in the
    // documented
    const referenced = getReferencedSymbols(owningModule);
    return Array.from(referenced).filter((s) => {
      const refl = context.project.getReflectionFromSymbol(s);
      return !refl || refl.flags.isPrivate;
    });
  }

  const origCreateSymbolReference = ReferenceType.createSymbolReference;
  ReferenceType.createSymbolReference = function (
    symbol: ts.Symbol,
    context: Context,
    name: string,
  ) {
    const owningModule = getOwningModule(context);
    getReferencedSymbols(owningModule).add(symbol);
    return origCreateSymbolReference.call(this, symbol, context, name);
  };

  function onResolveBegin(context: Context): void {
    const modules: (DeclarationReflection | ProjectReflection)[] =
      context.project.getChildrenByKind(ReflectionKind.Module);
    if (modules.length === 0) {
      // Single entry point, just target the project.
      modules.push(context.project);
    }

    for (const mod of modules) {
      const program = knownPrograms.get(mod);
      if (!program) continue;

      // Nasty hack here that will almost certainly break in future TypeDoc versions.
      context.setActiveProgram(program);

      const missing = discoverMissingExports(mod, context);
      for (const name of missing) {
        const decl = name.declarations![0];
        if (decl.getSourceFile().fileName.includes("node_modules")) {
          continue;
        }
        // TODO: maybe handle things other than TypeAliases?
        if (ts.isTypeAliasDeclaration(decl)) {
          const sf = decl.getSourceFile();
          const fileName = sf.fileName;
          const pos = Application.VERSION.startsWith("0.25")
            ? decl.pos
            : decl.getStart();
          const converted = context.converter.convertType(context, decl.type);
          // Depending on whether we have a symbolId or a reflection in
          // renderType we'll use different keys to look this up.
          symbolToType.set(`${fileName}:${pos}`, converted);
          // Ideally we should be able to key on position rather than file and
          // name when the reflection is present but I couldn't figure out how.
          symbolToType.set(`${fileName}:${decl.name.getText()}`, converted);
        }
      }
      context.setActiveProgram(void 0);
    }
  }

  app.converter.on(Converter.EVENT_RESOLVE_BEGIN, onResolveBegin);
  return symbolToType;
}
