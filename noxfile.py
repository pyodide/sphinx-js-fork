import nox
from nox.sessions import Session


@nox.session(python=["3.10", "3.11"])
def tests(session: Session) -> None:
    session.install("-r", "requirements_dev.txt")
    session.run("npm", "i", "--no-save", "jsdoc@4.0.0", "typedoc@0.20", external=True)
    session.run("pytest", "--junitxml=test-results.xml")
