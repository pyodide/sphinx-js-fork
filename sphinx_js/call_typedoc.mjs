import { writeFile } from "fs/promises";

const ExitCodes = {
  Ok: 0,
  OptionError: 1,
  CompileError: 3,
  ValidationError: 4,
  OutputError: 5,
  ExceptionThrown: 6,
  Watching: 7,
};

let td;

// Locate the kind IDs, look up the corresponding kindStrings, and add them to
// the JSON
function walk(o) {
  if ("kind" in o) {
    try {
      o["kindString"] = td.ReflectionKind.singularString(o["kind"]);
    } catch (e) {}
  }
  for (let v of Object.values(o)) {
    if (v && typeof v === "object") {
      walk(v);
    }
  }
}

async function bootstrapAppTypedoc0_25() {
  return await td.Application.bootstrapWithPlugins({}, [
    new td.ArgumentsReader(0),
    new td.TypeDocReader(),
    new td.PackageJsonReader(),
    new td.TSConfigReader(),
    new td.ArgumentsReader(300),
  ]);
}

async function main() {
  // Locate the node_modules folder that we calculated in typedoc.py and
  // import from there
  td = await import(
    process.env["TYPEDOC_NODE_MODULES"] + "/typedoc/dist/index.js"
  );
  // Most of this stuff is copied from typedoc/src/lib/cli.ts
  const start = Date.now();
  let app = await bootstrapAppTypedoc0_25();
  if (app.options.getValue("version")) {
    console.log(app.toString());
    return ExitCodes.Ok;
  }

  const project = await app.convert();
  const preValidationWarnCount = app.logger.warningCount;
  app.validate(project);
  const hadValidationWarnings =
    app.logger.warningCount !== preValidationWarnCount;
  if (app.logger.hasErrors()) {
    return ExitCodes.ValidationError;
  }
  if (
    hadValidationWarnings &&
    (app.options.getValue("treatWarningsAsErrors") ||
      app.options.getValue("treatValidationWarningsAsErrors"))
  ) {
    return ExitCodes.ValidationError;
  }

  const json = app.options.getValue("json");
  const serialized = app.serializer.projectToObject(project, process.cwd());
  // This next line is the only thing we added
  walk(serialized);

  const space = app.options.getValue("pretty") ? "\t" : "";
  await writeFile(json, JSON.stringify(serialized, null, space));
  app.logger.info(`JSON written to ${json}`);
  app.logger.verbose(`JSON rendering took ${Date.now() - start}ms`);
}
await main();
