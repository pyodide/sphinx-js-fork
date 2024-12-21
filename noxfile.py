from pathlib import Path

import nox
from nox.sessions import Session

PROJECT_ROOT = Path(__file__).parent


@nox.session(python=["3.10", "3.11", "3.12", "3.13"])
def tests(session: Session) -> None:
    session.install("-r", "requirements_dev.txt")
    venvroot = Path(session.bin).parent
    (venvroot / "node_modules").mkdir()
    with session.chdir(venvroot):
        session.run(
            "npm", "i", "--no-save", "jsdoc@4.0.0", "typedoc@0.25", external=True
        )
    session.run(
        "pytest",
        "--junitxml=test-results.xml",
        "--cov=sphinx_js",
        "--cov-report",
        "xml",
    )


@nox.session(python=["3.12"])
@nox.parametrize("typedoc", ["0.25", "0.26"])
def test_typedoc(session: Session, typedoc: str) -> None:
    # Install python dependencies
    session.install("-r", "requirements_dev.txt")
    venvroot = Path(session.bin).parent
    (venvroot / "node_modules").mkdir()
    with session.chdir(venvroot):
        # Install node dependencies
        session.run(
            "npm",
            "i",
            "--no-save",
            "tsx",
            "jsdoc@4.0.0",
            f"typedoc@{typedoc}",
            external=True,
        )
        session.run("npx", "tsc", "--version", external=True)
        session.run("npx", "typedoc", "--version", external=True)
        # Run typescript tests
        test_file = (PROJECT_ROOT / "tests/test.ts").resolve()
        register_import_hook = PROJECT_ROOT / "sphinx_js/js/registerImportHook.mjs"
        session.run(
            "npx",
            "--import",
            register_import_hook,
            "--import",
            "tsx",
            "--test",
            test_file,
            external=True,
        )
    # Run Python tests
    session.run("pytest", "--junitxml=test-results.xml", "-k", "not js")


@nox.session(python=["3.12"])
def test_sphinx_6(session: Session) -> None:
    session.install("sphinx<7")
    session.install("-r", "requirements_dev.txt")
    venvroot = Path(session.bin).parent
    (venvroot / "node_modules").mkdir()
    with session.chdir(venvroot):
        session.run(
            "npm", "i", "--no-save", "jsdoc@4.0.0", "typedoc@0.25", external=True
        )
    session.run("pytest", "--junitxml=test-results.xml", "-k", "not js")
