from pathlib import Path

import nox
from nox.sessions import Session


@nox.session(python=["3.10", "3.11"])
def tests(session: Session) -> None:
    session.install("-r", "requirements_dev.txt")
    session.run("npm", "i", "--no-save", "jsdoc@4.0.0", "typedoc@0.20", external=True)
    session.run("pytest", "--junitxml=test-results.xml")


@nox.session(python=["3.10", "3.11"])
@nox.parametrize("typedoc", ["0.20", "0.21", "0.22", "0.23", "0.24", "0.25"])
def test_typedoc(session: Session, typedoc: str) -> None:

    session.install("-r", "requirements_dev.txt")
    venvroot = Path(session.bin).parent
    (venvroot / "node_modules").mkdir()
    with session.chdir(venvroot):
        session.run(
            "npm", "i", "--no-save", "jsdoc@4.0.0", f"typedoc@{typedoc}", external=True
        )
    session.run("pytest", "--junitxml=test-results.xml", "-k", "not js")
