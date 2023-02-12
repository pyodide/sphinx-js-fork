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
