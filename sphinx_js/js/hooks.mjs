// An import hook to pick up packages in the node_modules that typedoc is
// installed into

export async function resolve(specifier, context, nextResolve) {
  // Take an `import` or `require` specifier and resolve it to a URL.
  try {
    return await nextResolve(specifier, context);
  } catch (e) {
    if (e.code !== "ERR_MODULE_NOT_FOUND") {
      // Unusual error let it propagate
      throw e;
    }
  }
  // Try resolving again with respect to the directory that typedoc is installed
  // into
  context.parentURL = `file:${process.env["TYPEDOC_NODE_MODULES"]}/`;
  return await nextResolve(specifier, context);
}
