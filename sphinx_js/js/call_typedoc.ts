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
import { fileURLToPath } from "url";
import { redirectPrivateTypes } from "./redirectPrivateAliases.ts";

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
  return await Application.bootstrapWithPlugins(
    {
      plugin: [fileURLToPath(import.meta.resolve("./typedocPlugin.ts"))],
    },
    [
      new ArgumentsReader(0, args),
      new TypeDocReader(),
      new PackageJsonReader(),
      new TSConfigReader(),
      new ArgumentsReader(300, args),
    ],
  );
}

async function loadConfig(
  configPath: string | undefined,
): Promise<SphinxJsConfig> {
  if (!configPath) {
    return {};
  }
  const configModule = await import(configPath);
  return configModule.config;
}

async function main() {
  // Most of this stuff is copied from typedoc/src/lib/cli.ts
  const start = Date.now();
  const args = process.argv.slice(2);
  let app = await bootstrapAppTypedoc0_25(args);
  if (app.options.getValue("version")) {
    console.log(app.toString());
    return ExitCodes.Ok;
  }
  app.extraData = {};
  app.options.getValue("modifierTags").push("@hidetype");
  const userConfigPath = app.options.getValue("sphinxJsConfig");
  const config = await loadConfig(userConfigPath);
  app.logger.info(`Loaded user config from ${userConfigPath}`);
  const symbolToType = redirectPrivateTypes(app);
  await config.preConvert?.(app);

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

  const basePath = app.options.getValue("basePath");
  const converter = new Converter(project, basePath, config, symbolToType);
  converter.computePaths();
  const space = app.options.getValue("pretty") ? "\t" : "";
  const result = converter.convertAll();
  await config.postConvert?.(app, project, converter.typedocToIRMap);
  const res = JSON.stringify([result, app.extraData], null, space);
  const json = app.options.getValue("json");
  await writeFile(json, res);
  app.logger.info(`JSON written to ${json}`);
  app.logger.verbose(`JSON rendering took ${Date.now() - start}ms`);
}

process.exit(await main());
