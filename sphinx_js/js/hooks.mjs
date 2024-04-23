export async function resolve(specifier, context, nextResolve) {
  // Take an `import` or `require` specifier and resolve it to a URL.
  try {
    return await nextResolve(specifier, context);
  } catch (e) {
    if (e.code !== "ERR_MODULE_NOT_FOUND") {
      throw e;
    }
  }
  context.parentURL = `file:${process.env["TYPEDOC_NODE_MODULES"]}/`;
  return await nextResolve(specifier, context);
}
