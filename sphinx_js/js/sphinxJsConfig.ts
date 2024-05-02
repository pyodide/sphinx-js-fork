import { ParameterReflection } from "typedoc";

export type SphinxJsConfig = {
  shouldDestructureArg?: (p: ParameterReflection) => boolean;
};
