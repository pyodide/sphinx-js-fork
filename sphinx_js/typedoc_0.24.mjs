import { writeFile } from "fs/promises";


let td;

// Locate the kind IDs, look up the corresponding kindStrings, and add them to
// the JSON
function walk(o){
    if("kind" in o) {
        try {
            o["kindString"] = td.ReflectionKind.singularString(o["kind"]);
        } catch(e) {}
    }
    for(let v of Object.values(o)) {
        if (v && typeof v === "object") {
            walk(v);
        }
    }
}

async function main() {
    // Locate the node_modules folder that we calculated in typedoc.py and
    // import from there
    td = await import(process.env["TYPEDOC_NODE_MODULES"] + "/typedoc/dist/index.js");
    // Most of this stuff is copied from typedoc/src/lib/cli.ts
    const app = new td.Application();

    app.options.addReader(new td.ArgumentsReader(0));
    app.options.addReader(new td.TypeDocReader());
    app.options.addReader(new td.PackageJsonReader());
    app.options.addReader(new td.TSConfigReader());
    app.options.addReader(new td.ArgumentsReader(300));
    const start = Date.now();
    await app.bootstrapWithPlugins();
    const project = app.convert();
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
