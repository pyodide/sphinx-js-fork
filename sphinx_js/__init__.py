from functools import cache
from os.path import join, normpath
from pathlib import Path
from textwrap import dedent
from typing import Any

from sphinx.application import Sphinx
from sphinx.errors import SphinxError

from .directives import (
    JSFunction,
    auto_attribute_directive_bound_to_app,
    auto_class_directive_bound_to_app,
    auto_function_directive_bound_to_app,
    auto_module_directive_bound_to_app,
    sphinx_js_type_role,
)
from .jsdoc import Analyzer as JsAnalyzer
from .typedoc import Analyzer as TsAnalyzer


# Cache this to guarantee it only runs once.
@cache
def fix_js_make_xref() -> None:
    """Monkeypatch to fix sphinx.domains.javascript TypedField and GroupedField

    Fixes https://github.com/sphinx-doc/sphinx/issues/11021

    """
    from docutils import nodes
    from sphinx.domains import javascript
    from sphinx.locale import _
    from sphinx.util.docfields import GroupedField, TypedField

    class JSXrefMixin:
        def make_xref(
            self,
            rolename: Any,
            domain: Any,
            target: Any,
            innernode: Any = nodes.emphasis,
            contnode: Any = None,
            env: Any = None,
            inliner: Any = None,
            location: Any = None,
        ) -> Any:
            # Set inliner to None just like the PythonXrefMixin does so the
            # xref doesn't get rendered as a function.
            return super().make_xref(  # type:ignore[misc]
                rolename,
                domain,
                target,
                innernode,
                contnode,
                env,
                inliner=None,
                location=None,
            )

    class JSTypedField(JSXrefMixin, TypedField):
        pass

    class JSGroupedField(JSXrefMixin, GroupedField):
        pass

    # Replace javascript module
    javascript.TypedField = JSTypedField  # type:ignore[attr-defined]
    javascript.GroupedField = JSGroupedField  # type:ignore[attr-defined]

    # Fix the one place TypedField and GroupedField are used in the javascript
    # module
    javascript.JSCallable.doc_field_types = [
        JSTypedField(
            "arguments",
            label=_("Arguments"),
            names=("argument", "arg", "parameter", "param"),
            typerolename="func",
            typenames=("paramtype", "type"),
        ),
        JSGroupedField(
            "errors",
            label=_("Throws"),
            rolename="func",
            names=("throws",),
            can_collapse=True,
        ),
    ] + javascript.JSCallable.doc_field_types[2:]


# Cache this to guarantee it only runs once.
@cache
def fix_staticfunction_objtype() -> None:
    """Override js:function directive with one that understands static and async
    prefixes
    """
    from sphinx.domains.javascript import JavaScriptDomain

    JavaScriptDomain.directives["function"] = JSFunction


@cache
def add_type_param_field_to_directives() -> None:
    from sphinx.domains.javascript import (  # type: ignore[attr-defined]
        GroupedField,
        JSCallable,
        JSConstructor,
    )

    typeparam_field = GroupedField(
        "typeparam",
        label="Type parameters",
        rolename="func",
        names=("typeparam",),
        can_collapse=True,
    )

    JSCallable.doc_field_types.insert(0, typeparam_field)
    JSConstructor.doc_field_types.insert(0, typeparam_field)


SPHINX_JS_CSS = "sphinx_js.css"


def make_css_file(app: Sphinx) -> None:
    dst = Path(app.outdir) / "_static" / SPHINX_JS_CSS
    text = ""
    if app.config.ts_type_bold:
        text = dedent(
            """\
            .sphinx_js-type {
                font-weight: bolder;
            }
            """
        )
    dst.write_text(text)


def on_build_finished(app: Sphinx, exc: Exception | None) -> None:
    if exc or app.builder.format != "html":
        return
    make_css_file(app)


def setup(app: Sphinx) -> None:
    fix_js_make_xref()
    fix_staticfunction_objtype()
    add_type_param_field_to_directives()

    # I believe this is the best place to run jsdoc. I was tempted to use
    # app.add_source_parser(), but I think the kind of source it's referring to
    # is RSTs.
    app.connect("builder-inited", analyze)

    app.add_directive_to_domain(
        "js", "autofunction", auto_function_directive_bound_to_app(app)
    )
    app.add_directive_to_domain(
        "js", "autoclass", auto_class_directive_bound_to_app(app)
    )
    app.add_directive_to_domain(
        "js", "autoattribute", auto_attribute_directive_bound_to_app(app)
    )
    app.add_directive_to_domain(
        "js", "automodule", auto_module_directive_bound_to_app(app)
    )

    # TODO: We could add a js:module with app.add_directive_to_domain().

    app.add_config_value("js_language", default="javascript", rebuild="env")
    app.add_config_value(
        "js_source_path", default=["../"], rebuild="env", types=[str, list]
    )
    app.add_config_value("jsdoc_config_path", default=None, rebuild="env")
    app.add_config_value("ts_type_xref_formatter", None, "env")
    app.add_config_value("ts_type_bold", False, "env")
    app.add_config_value("ts_should_destructure_arg", None, "env")
    app.add_config_value("ts_post_convert", None, "env")
    app.add_role("sphinx_js_type", sphinx_js_type_role)
    app.add_css_file(SPHINX_JS_CSS)
    app.connect("build-finished", on_build_finished)

    # We could use a callable as the "default" param here, but then we would
    # have had to duplicate or build framework around the logic that promotes
    # js_source_path to a list and calls abspath() on it. It's simpler this way
    # until we need to access js_source_path from more than one place.
    app.add_config_value("root_for_relative_js_paths", None, "env")


def analyze(app: Sphinx) -> None:
    """Run JSDoc or another analysis tool across a whole codebase, and squirrel
    away its results in a language-specific Analyzer."""
    # Normalize config values:
    source_paths = (
        [app.config.js_source_path]
        if isinstance(app.config.js_source_path, str)
        else app.config.js_source_path
    )
    abs_source_paths = [normpath(join(app.confdir, path)) for path in source_paths]
    root_for_relative_paths = root_or_fallback(
        normpath(join(app.confdir, app.config.root_for_relative_js_paths))
        if app.config.root_for_relative_js_paths
        else None,
        abs_source_paths,
    )

    # Pick analyzer:
    try:
        analyzer: Any = {"javascript": JsAnalyzer, "typescript": TsAnalyzer}[
            app.config.js_language
        ]
    except KeyError:
        raise SphinxError(
            "Unsupported value of js_language in config: %s" % app.config.js_language
        )

    # Analyze source code:
    app._sphinxjs_analyzer = analyzer.from_disk(  # type:ignore[attr-defined]
        abs_source_paths, app, root_for_relative_paths
    )


def root_or_fallback(
    root_for_relative_paths: str | None, abs_source_paths: list[str]
) -> str:
    """Return the path that relative JS entity paths in the docs are relative to.

    Fall back to the sole JS source path if the setting is unspecified.

    :arg root_for_relative_paths: The absolute-ized root_for_relative_js_paths
        setting. None if the user hasn't specified it.
    :arg abs_source_paths: Absolute paths of dirs to scan for JS code

    """
    if root_for_relative_paths:
        return root_for_relative_paths
    else:
        if len(abs_source_paths) > 1:
            raise SphinxError(
                "Since more than one js_source_path is specified in conf.py, root_for_relative_js_paths must also be specified. This allows paths beginning with ./ or ../ to be unambiguous."
            )
        else:
            return abs_source_paths[0]
