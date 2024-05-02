import {
  Application,
  ArgumentsReader,
  TypeDocReader,
  PackageJsonReader,
  TSConfigReader,
} from "typedoc";
import { writeFile } from "fs/promises";
import { Converter } from "./convertTopLevel.ts";
import { SphinxJsConfig } from "./sphinxJsConfig.ts";

const ExitCodes = {
  Ok: 0,
  OptionError: 1,
  CompileError: 3,
  ValidationError: 4,
  OutputError: 5,
  ExceptionThrown: 6,
  Watching: 7,
};

async function bootstrapAppTypedoc0_25(args: string[]): Promise<Application> {
  return await Application.bootstrapWithPlugins({}, [
    new ArgumentsReader(0, args),
    new TypeDocReader(),
    new PackageJsonReader(),
    new TSConfigReader(),
    new ArgumentsReader(300, args),
  ]);
}

async function loadConfig(args: string[]): Promise<SphinxJsConfig> {
  const configIndex = args.indexOf("--sphinx-js-config");
  if (configIndex === -1) {
    return {};
  }
  if (configIndex === args.length) {
    console.error("Expected --sphinx-js-config to have an argument");
    process.exit(1);
  }
  const [_option, value] = args.splice(configIndex, 2);
  const configModule = await import(value);
  return configModule.config;
}

async function main() {
  // Most of this stuff is copied from typedoc/src/lib/cli.ts
  const start = Date.now();
  const args = process.argv.slice(2);
  const config = await loadConfig(args);
  let app = await bootstrapAppTypedoc0_25(args);
  if (app.options.getValue("version")) {
    console.log(app.toString());
    return ExitCodes.Ok;
  }

  const project = await app.convert();
  if (!project) {
    return ExitCodes.CompileError;
  }
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
  const basePath = app.options.getValue("basePath");
  const converter = new Converter(project, basePath, config);
  converter.computePaths();
  const space = app.options.getValue("pretty") ? "\t" : "";
  const res = JSON.stringify(converter.convertAll(), null, space);
  await writeFile(json, res);
  app.logger.info(`JSON written to ${json}`);
  app.logger.verbose(`JSON rendering took ${Date.now() - start}ms`);
}

process.exit(await main());
