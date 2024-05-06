/**
 * The thing.
 */
export const a = 7;

/**
 * Clutches the bundle
 */
export async function f() {}

export function z(a: number, b: typeof q): number {
  return a;
}

/**
 * This is a summary. This is more info.
 */
export class A {
  async f() {}

  [Symbol.iterator]() {}

  g(a: number): number {
    return a + 1;
  }
}

/**
 * An instance of class A
 */
export let aInstance: A;

/**
 * @typeParam T Description of T
 */
export class Z<T extends A> {
  x: T;
  constructor(a: number, b: number) {}

  z() {}
}

export let zInstance: Z<A>;

/**
 * Another thing.
 */
export const q = { a: "z29", b: 76 };

/**
 * Documentation for the interface I
 */
export interface I {}

/**
 * An instance of the interface
 */
export let interfaceInstance: I = {};

/**
 * A function with a type parameter!
 *
 * We'll refer to ourselves: :js:func:`functionWithTypeParam`
 *
 * @typeParam T The type parameter
 * @typeParam S Another type param
 * @param z A Z of T
 * @returns The x field of z
 */
export function functionWithTypeParam<T extends A>(z: Z<T>): T {
  return z.x;
}
