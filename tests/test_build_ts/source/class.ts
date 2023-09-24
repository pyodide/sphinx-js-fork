/**
 * A definition of a class
 */
export class ClassDefinition {
    field: string;

    /**
     * ClassDefinition constructor
     * @param simple A parameter with a simple type
     */
    constructor(simple: number) {

    }

    /**
     * This is a method without return type
     * @param simple A parameter with a simple type
     */
    method1(simple: number) : void {

    }

    /**
     * This is a method (should be before method 'method1', but after fields)
     */
    anotherMethod() {

    }
}

export interface Interface {
}

export abstract class ClassWithSupersAndInterfacesAndAbstract extends ClassDefinition implements Interface {
  /** I construct. */
  constructor() {
    super(8);
  }
}

export interface InterfaceWithSupers extends Interface {
}

export class ExportedClass {
  constructor() {
  }
}

export class ConstructorlessClass {
}

export interface OptionalThings {
  foop?(): void;
  boop?: boolean;
}

/**
 * Words words words
 * @param a An optional thing
 * @returns The result
 */
export function blah(a: OptionalThings) : ConstructorlessClass {
  return 0 as ConstructorlessClass;
}

export function thunk(b : typeof blah) {

}

/**
 * A problematic self referential function
 * @param b troublemaker
 */
export function quik(b: typeof quik)  {}
