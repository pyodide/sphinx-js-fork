"""These are the actual Sphinx directives we provide, but they are skeletal.

The real meat is in their parallel renderer classes, in renderers.py. The split
is due to the unfortunate trick we need here of having functions return the
directive classes after providing them the ``app`` symbol, where we store the
JSDoc output, via closure. The renderer classes, able to be top-level classes,
can access each other and collaborate.

"""
import re
from collections.abc import Iterable
from os.path import join, relpath
from typing import Any

from docutils import nodes
from docutils.nodes import Node
from docutils.parsers.rst import Directive
from docutils.parsers.rst import Parser as RstParser
from docutils.parsers.rst.directives import flag
from docutils.utils import new_document
from sphinx import addnodes
from sphinx.application import Sphinx
from sphinx.domains.javascript import JSCallable

from .renderers import (
    AutoAttributeRenderer,
    AutoClassRenderer,
    AutoFunctionRenderer,
    AutoModuleRenderer,
    AutoSummaryRenderer,
    Renderer,
)


def unescape(escaped: str) -> str:
    # For some reason the string we get has a bunch of null bytes in it??
    # Remove them...
    escaped = escaped.replace("\x00", "")
    # For some reason the extra slash before spaces gets lost between the .rst
    # source and when this directive is called. So don't replace "\<space>" =>
    # "<space>"
    return re.sub(r"\\([^ ])", r"\1", escaped)


def _members_to_exclude(arg: str | None) -> set[str]:
    """Return a set of members to exclude given a comma-delim list of them.

    Exclude none if none are passed. This differs from autodocs' behavior,
    which excludes all. That seemed useless to me.

    """
    return set(a.strip() for a in (arg or "").split(","))


def sphinx_js_type_role(role, rawtext, text, lineno, inliner, options=None, content=None):  # type: ignore[no-untyped-def]
    """
    The body should be escaped rst. This renders its body as rst and wraps the
    result in <span class="sphinx_js-type"> </span>
    """
    unescaped = unescape(text)
    doc = new_document("", inliner.document.settings)
    RstParser().parse(unescaped, doc)
    n = nodes.inline(text)
    n["classes"].append("sphinx_js-type")
    n += doc.children[0].children
    return [n], []


class JsDirective(Directive):
    """Abstract directive which knows how to pull things out of our IR"""

    has_content = True
    required_arguments = 1
    optional_arguments = 0
    final_argument_whitespace = True

    option_spec = {"short-name": flag}

    def _run(self, renderer_class: type[Renderer], app: Sphinx) -> list[Node]:
        renderer = renderer_class.from_directive(self, app)
        note_dependencies(app, renderer.dependencies())
        return renderer.rst_nodes()


class JsDirectiveWithChildren(JsDirective):
    option_spec = JsDirective.option_spec.copy()
    option_spec.update(
        {
            "members": lambda members: (
                [m.strip() for m in members.split(",")] if members else []
            ),
            "exclude-members": _members_to_exclude,
            "private-members": flag,
        }
    )


def note_dependencies(app: Sphinx, dependencies: Iterable[str]) -> None:
    """Note dependencies of current document.

    :arg app: Sphinx application object
    :arg dependencies: iterable of filename strings relative to root_for_relative_paths
    """
    for fn in dependencies:
        # Dependencies in the IR are relative to `root_for_relative_paths`, itself
        # relative to the configuration directory.
        analyzer = app._sphinxjs_analyzer  # type:ignore[attr-defined]
        abs = join(analyzer._base_dir, fn)
        # Sphinx dependencies are relative to the source directory.
        rel = relpath(abs, app.srcdir)
        app.env.note_dependency(rel)


def auto_function_directive_bound_to_app(app: Sphinx) -> type[Directive]:
    class AutoFunctionDirective(JsDirective):
        """js:autofunction directive, which spits out a js:function directive

        Takes a single argument which is a JS function name combined with an
        optional formal parameter list, all mashed together in a single string.

        """

        def run(self) -> list[Node]:
            return self._run(AutoFunctionRenderer, app)

    return AutoFunctionDirective


def auto_class_directive_bound_to_app(app: Sphinx) -> type[Directive]:
    class AutoClassDirective(JsDirectiveWithChildren):
        """js:autoclass directive, which spits out a js:class directive

        Takes a single argument which is a JS class name combined with an
        optional formal parameter list for the constructor, all mashed together
        in a single string.

        """

        def run(self) -> list[Node]:
            return self._run(AutoClassRenderer, app)

    return AutoClassDirective


def auto_attribute_directive_bound_to_app(app: Sphinx) -> type[Directive]:
    class AutoAttributeDirective(JsDirective):
        """js:autoattribute directive, which spits out a js:attribute directive

        Takes a single argument which is a JS attribute name.

        """

        def run(self) -> list[Node]:
            return self._run(AutoAttributeRenderer, app)

    return AutoAttributeDirective


class JSFunction(JSCallable):
    option_spec = {
        **JSCallable.option_spec,
        "static": flag,
        "async": flag,
    }

    def get_display_prefix(
        self,
    ) -> list[Any]:
        result = []
        for name in ["static", "async"]:
            if name in self.options:
                result.extend(
                    [
                        addnodes.desc_sig_keyword(name, name),
                        addnodes.desc_sig_space(),
                    ]
                )
        return result


def auto_module_directive_bound_to_app(app: Sphinx) -> type[Directive]:
    class AutoModuleDirective(JsDirectiveWithChildren):
        """TODO: words here"""

        required_arguments = 1

        def run(self) -> list[Node]:
            return self._run(AutoModuleRenderer, app)

    return AutoModuleDirective


def auto_summary_directive_bound_to_app(app: Sphinx) -> type[Directive]:
    class JsDocSummary(JsDirective):
        required_arguments = 1

        def run(self) -> list[Node]:
            return self._run(AutoSummaryRenderer, app)

    return JsDocSummary
