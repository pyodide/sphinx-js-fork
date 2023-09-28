import textwrap
from collections.abc import Callable, Iterator
from functools import partial
from re import sub
from typing import Any, Literal

from docutils.nodes import Node
from docutils.parsers.rst import Directive
from docutils.parsers.rst import Parser as RstParser
from docutils.statemachine import StringList
from docutils.utils import new_document
from jinja2 import Environment, PackageLoader
from sphinx.application import Sphinx
from sphinx.config import Config
from sphinx.errors import SphinxError
from sphinx.util import logging, rst

from .analyzer_utils import dotted_path
from .ir import (
    Attribute,
    Class,
    Description,
    DescriptionText,
    Exc,
    Function,
    Interface,
    Param,
    Pathname,
    Return,
    TopLevel,
    Type,
    TypeParam,
    TypeXRef,
)
from .jsdoc import Analyzer as JsAnalyzer
from .parsers import PathVisitor
from .suffix_tree import SuffixAmbiguous, SuffixNotFound
from .typedoc import Analyzer as TsAnalyzer

Analyzer = TsAnalyzer | JsAnalyzer

logger = logging.getLogger(__name__)


class JsRenderer:
    """Abstract superclass for renderers of various sphinx-js directives

    Provides an inversion-of-control framework for rendering and bridges us
    from the hidden, closed-over JsDirective subclasses to top-level classes
    that can see and use each other. Handles parsing of a single, all-consuming
    argument that consists of a JS/TS entity reference and an optional formal
    parameter list.

    """

    _renderer_type: Literal["function", "class", "attribute"]
    _template: str
    _type_xref_formatter: Callable[[TypeXRef], str]
    _type_text_formatter: Callable[[str], str]
    _partial_path: list[str]
    _explicit_formal_params: str
    _content: list[str]
    _options: dict[str, Any]

    def _template_vars(self, name: str, obj: TopLevel) -> dict[str, Any]:
        raise NotImplementedError

    def _set_type_xref_formatter(
        self, formatter: Callable[[Config, TypeXRef], str] | None
    ) -> None:
        if formatter:
            self._type_xref_formatter = partial(formatter, self._app.config)
            return

        def default_type_xref_formatter(xref: TypeXRef) -> str:
            return xref.name

    def _set_type_text_formatter(
        self, formatter: Callable[[Config, str], str] | None
    ) -> None:
        if formatter:
            self._type_text_formatter = partial(formatter, self._app.config)
            return

        def default_type_text_formatter(text: str) -> str:
            return text

        self._type_text_formatter = default_type_text_formatter

    def __init__(
        self,
        directive: Directive,
        app: Sphinx,
        arguments: list[str],
        content: list[str] | None = None,
        options: dict[str, Any] | None = None,
    ):
        # Fix crash when calling eval_rst with CommonMarkParser:
        if not hasattr(directive.state.document.settings, "tab_width"):
            directive.state.document.settings.tab_width = 8

        self._directive = directive
        self._app = app
        self._set_type_xref_formatter(app.config.ts_type_xref_formatter)

        def bold_formatter(conf: Config, text: str) -> str:
            parts = ["**" + part + "**" for part in text.split(" ") if part]
            return " ".join(parts).strip()

        if app.config.ts_type_bold:
            self._set_type_text_formatter(bold_formatter)
        else:
            self._set_type_text_formatter(None)

        # content, arguments, options, app: all need to be accessible to
        # template_vars, so we bring them in on construction and stow them away
        # on the instance so calls to template_vars don't need to concern
        # themselves with what it needs.
        (
            self._partial_path,
            self._explicit_formal_params,
        ) = PathVisitor().parse(arguments[0])
        self._content = content or StringList()
        self._options = options or {}

    @classmethod
    def from_directive(cls, directive: Directive, app: Sphinx) -> "JsRenderer":
        """Return one of these whose state is all derived from a directive.

        This is suitable for top-level calls but not for when a renderer is
        being called from a different renderer, lest content and such from the
        outer directive be duplicated in the inner directive.

        :arg directive: The associated Sphinx directive
        :arg app: The Sphinx global app object. Some methods need this.

        """
        return cls(
            directive,
            app,
            arguments=directive.arguments,
            content=directive.content,
            options=directive.options,
        )

    def get_object(self) -> TopLevel:
        """Return the IR object rendered by this renderer."""
        try:
            analyzer: Analyzer = (
                self._app._sphinxjs_analyzer  # type:ignore[attr-defined]
            )
            obj = analyzer.get_object(self._partial_path, self._renderer_type)
            return obj
        except SuffixNotFound as exc:
            raise SphinxError(
                'No documentation was found for object "%s" or any path ending with that.'
                % "".join(exc.segments)
            )
        except SuffixAmbiguous as exc:
            raise SphinxError(
                'More than one object matches the path suffix "%s". Candidate paths have these segments in front: %s'
                % ("".join(exc.segments), exc.next_possible_keys)
            )

    def dependencies(self) -> set[str]:
        """Return a set of path(s) to the file(s) that the IR object
        rendered by this renderer is from.  Each path is absolute or
        relative to `root_for_relative_js_paths`.

        """
        try:
            obj = self.get_object()
            if obj.deppath:
                return set([obj.deppath])
        except SphinxError as exc:
            logger.exception("Exception while retrieving paths for IR object: %s" % exc)
        return set([])

    def rst_nodes(self) -> list[Node]:
        """Render into RST nodes a thing shaped like a function, having a name
        and arguments.

        Fill in args, docstrings, and info fields from stored JSDoc output.

        """
        obj = self.get_object()
        rst = self.rst(
            self._partial_path, obj, use_short_name="short-name" in self._options
        )

        # Parse the RST into docutils nodes with a fresh doc, and return
        # them.
        #
        # Not sure if passing the settings from the "real" doc is the right
        # thing to do here:
        doc = new_document(
            f"{obj.filename}:{obj.path}({obj.line})",
            settings=self._directive.state.document.settings,
        )
        RstParser().parse(rst, doc)
        return doc.children

    def rst(
        self, partial_path: list[str], obj: TopLevel, use_short_name: bool = False
    ) -> str:
        """Return rendered RST about an entity with the given name and IR
        object."""
        dotted_name = partial_path[-1] if use_short_name else dotted_path(partial_path)

        # Render to RST using Jinja:
        env = Environment(loader=PackageLoader("sphinx_js", "templates"))
        template = env.get_template(self._template)
        result = template.render(**self._template_vars(dotted_name, obj))
        result = result.strip()
        had_blank = False
        lines = []
        for line in result.splitlines():
            if line.strip():
                had_blank = False
                lines.append(line.rstrip())
            elif not had_blank:
                lines.append("")
                had_blank = True
        result = "\n".join(lines) + "\n"
        return result

    def _formal_params(self, obj: Function | Class) -> str:
        """Return the JS function or class params, looking first to any
        explicit params written into the directive and falling back to those in
        comments or JS code.

        Return a ReST-escaped string ready for substitution into the template.

        """
        if self._explicit_formal_params:
            return self._explicit_formal_params

        formals = []
        used_names = set()

        for param in obj.params:
            # Turn "@param p2.subProperty" into just p2. We wouldn't want to
            # add subproperties to the flat formal param list:
            name = param.name.split(".")[0]

            # Add '...' to the parameter name if it's a variadic argument
            if param.is_variadic:
                name = "..." + name

            if name not in used_names:
                # We don't rst.escape() anything here, because, empirically,
                # the js:function directive (or maybe directive params in
                # general) automatically ignores markup constructs in its
                # parameter (though not its contents).
                formals.append(
                    name if not param.has_default else f"{name}={param.default}"
                )
                used_names.add(name)

        return "({})".format(", ".join(formals))

    def render_description(self, description: Description) -> str:
        """Construct a single comment string from a fancy object."""
        if isinstance(description, str):
            return description
        content = []
        prev = ""
        for s in description:
            if isinstance(s, DescriptionText):
                prev = s.text
                content.append(prev)
                continue
            # code
            if s.code.startswith("```") and s.code.count("\n") >= 1:
                # A code pen
                first_line, rest = s.code.split("\n", 1)
                rest = rest.removesuffix("```")
                code_type = first_line.removeprefix("```")
                start = f".. code-block:: {code_type}\n\n"
                codeblock = textwrap.indent(rest, " " * 4)
                end = "\n\n"
                content.append("\n" + start + codeblock + end)
                continue

            if s.code.startswith("``"):
                # Sphinx-style escaped, leave it alone.
                content.append(s.code)
                continue
            if prev.endswith(":"):
                # A sphinx role, leave it alone
                content.append(s.code)
                continue

            if prev.endswith(" ") and not s.code.endswith(">`"):
                # Used single uptick with code, put double upticks
                content.append(f"`{s.code}`")
                continue
            content.append(s.code)
        return "".join(content)

    def render_type(self, type: Type, escape: bool = False, bold: bool = True) -> str:
        if not type:
            return ""
        if isinstance(type, str):
            if bold:
                type = "**%s**" % type
            if escape:
                type = rst.escape(type)
            return type
        it = iter(type)

        def strs() -> Iterator[str]:
            for elem in it:
                if isinstance(elem, str):
                    yield elem
                else:
                    xref.append(elem)
                    return

        res = []
        while True:
            xref: list[TypeXRef] = []
            s = "".join(strs())
            if s:
                s = self._type_text_formatter(s)
            if escape:
                s = rst.escape(s)
            if s:
                res.append(s)
            if not xref:
                break
            res.append(self.render_xref(xref[0], escape))

        return r"\ ".join(res)

    def render_xref(self, s: TypeXRef, escape: bool = False) -> str:
        result = self._type_xref_formatter(s)
        if escape:
            result = rst.escape(result)
        return result

    def _return_formatter(self, return_: Return) -> tuple[list[str], str]:
        """Derive heads and tail from ``@returns`` blocks."""
        tail = []
        if return_.type:
            tail.append(self.render_type(return_.type, escape=False))
        if return_.description:
            tail.append(self.render_description(return_.description))
        return ["returns"], " -- ".join(tail)

    def _type_param_formatter(self, tparam: TypeParam) -> tuple[list[str], str] | None:
        v = tparam.name
        if tparam.extends:
            v += " extends " + self.render_type(tparam.extends)
        heads = ["typeparam", v]
        return heads, self.render_description(tparam.description)

    def _param_formatter(self, param: Param) -> tuple[list[str], str] | None:
        """Derive heads and tail from ``@param`` blocks."""
        if not param.type and not param.description:
            # There's nothing worth saying about this param.
            return None
        heads = ["param"]
        heads.append(param.name)

        tail = self.render_description(param.description)
        return heads, tail

    def _param_type_formatter(self, param: Param) -> tuple[list[str], str] | None:
        """Generate types for function parameters specified in field."""
        if not param.type:
            return None
        heads = ["type", param.name]
        tail = self.render_type(param.type)
        return heads, tail

    def _exception_formatter(self, exception: Exc) -> tuple[list[str], str]:
        """Derive heads and tail from ``@throws`` blocks."""
        heads = ["throws"]
        if exception.type:
            heads.append(self.render_type(exception.type, bold=False))
        tail = self.render_description(exception.description)
        return heads, tail

    def _fields(self, obj: TopLevel) -> Iterator[tuple[list[str], str]]:
        """Return an iterable of "info fields" to be included in the directive,
        like params, return values, and exceptions.

        Each field consists of a tuple ``(heads, tail)``, where heads are
        words that go between colons (as in ``:param string href:``) and
        tail comes after.

        """
        FIELD_TYPES: list[tuple[str, Callable[[Any], tuple[list[str], str] | None]]] = [
            ("type_params", self._type_param_formatter),
            ("params", self._param_formatter),
            ("params", self._param_type_formatter),
            ("properties", self._param_formatter),
            ("properties", self._param_type_formatter),
            ("exceptions", self._exception_formatter),
            ("returns", self._return_formatter),
        ]
        for collection_attr, callback in FIELD_TYPES:
            for instance in getattr(obj, collection_attr, []):
                result = callback(instance)
                if result:
                    heads, tail = result
                    # If there are line breaks in the tail, the RST parser will
                    # end the field list prematurely.
                    #
                    # TODO: Instead, indent multi-line tails juuuust right, and
                    # we can enjoy block-level constructs within tails:
                    # https://docutils.sourceforge.io/docs/ref/rst/
                    # restructuredtext.html#field-lists.
                    yield [rst.escape(h) for h in heads], unwrapped(tail)


class AutoFunctionRenderer(JsRenderer):
    _template = "function.rst"
    _renderer_type = "function"

    def _template_vars(self, name: str, obj: Function) -> dict[str, Any]:  # type: ignore[override]
        deprecated = obj.deprecated
        if not isinstance(deprecated, bool):
            deprecated = self.render_description(deprecated)
        return dict(
            name=name,
            params=self._formal_params(obj),
            fields=self._fields(obj),
            description=self.render_description(obj.description),
            examples=[self.render_description(x) for x in obj.examples],
            deprecated=deprecated,
            is_optional=obj.is_optional,
            is_static=obj.is_static,
            is_async=obj.is_async,
            see_also=obj.see_alsos,
            content="\n".join(self._content),
        )


class AutoClassRenderer(JsRenderer):
    _template = "class.rst"
    _renderer_type = "class"

    def _template_vars(self, name: str, obj: Class | Interface) -> dict[str, Any]:  # type: ignore[override]
        # TODO: At the moment, we pull most fields (params, returns,
        # exceptions, etc.) off the constructor only. We could pull them off
        # the class itself too in the future.
        if not isinstance(obj, Class) or not obj.constructor:
            # One way or another, it has no constructor, so make a blank one to
            # keep from repeating this long test for every constructor-using
            # line in the dict() call:
            constructor = Function(
                name="",
                path=Pathname([]),
                filename="",
                deppath=None,
                description="",
                line=0,
                deprecated=False,
                examples=[],
                see_alsos=[],
                properties=[],
                exported_from=None,
                is_abstract=False,
                is_optional=False,
                is_static=False,
                is_async=False,
                is_private=False,
                type_params=obj.type_params,
                params=[],
                exceptions=[],
                returns=[],
            )
        else:
            constructor = obj.constructor
        return dict(
            name=name,
            params=self._formal_params(constructor),
            fields=self._fields(constructor),
            examples=[self.render_description(ex) for ex in constructor.examples],
            deprecated=constructor.deprecated,
            see_also=constructor.see_alsos,
            exported_from=obj.exported_from,
            class_comment=self.render_description(obj.description),
            is_abstract=isinstance(obj, Class) and obj.is_abstract,
            interfaces=obj.interfaces if isinstance(obj, Class) else [],
            is_interface=isinstance(
                obj, Interface
            ),  # TODO: Make interfaces not look so much like classes. This will require taking complete control of templating from Sphinx.
            supers=obj.supers,
            constructor_comment=self.render_description(constructor.description),
            content="\n".join(self._content),
            members=self._members_of(
                obj,
                include=self._options["members"],
                exclude=self._options.get("exclude-members", set()),
                should_include_private="private-members" in self._options,
            )
            if "members" in self._options
            else "",
        )

    def _members_of(
        self,
        obj: Class | Interface,
        include: list[str],
        exclude: list[str],
        should_include_private: bool,
    ) -> str:
        """Return RST describing the members of a given class.

        :arg obj Class: The class we're documenting
        :arg include: List of names of members to include. If empty, include
            all.
        :arg exclude: Set of names of members to exclude
        :arg should_include_private: Whether to include private members

        """

        def rst_for(obj: Attribute | Function) -> str:
            renderer = (
                AutoFunctionRenderer
                if isinstance(obj, Function)
                else AutoAttributeRenderer
            )
            return renderer(self._directive, self._app, arguments=["dummy"]).rst(
                [obj.name], obj, use_short_name=False
            )

        def members_to_include(
            include: list[str],
        ) -> list[Attribute | Function]:
            """Return the members that should be included (before excludes and
            access specifiers are taken into account).

            This will either be the ones explicitly listed after the
            ``:members:`` option, in that order; all members of the class; or
            listed members with remaining ones inserted at the placeholder "*".

            """

            def sort_attributes_first_then_by_path(obj: Function | Attribute) -> Any:
                """Return a sort key for IR objects."""
                return isinstance(obj, Function), obj.path.segments

            members = obj.members
            if not include:
                # Specifying none means listing all.
                return sorted(members, key=sort_attributes_first_then_by_path)
            included_set = set(include)

            # If the special name * is included in the list, include all other
            # members, in sorted order.
            if "*" in included_set:
                star_index = include.index("*")
                sorted_not_included_members = sorted(
                    (m for m in members if m.name not in included_set),
                    key=sort_attributes_first_then_by_path,
                )
                not_included = [m.name for m in sorted_not_included_members]
                include = (
                    include[:star_index] + not_included + include[star_index + 1 :]
                )
                included_set.update(not_included)

            # Even if there are 2 members with the same short name (e.g. a
            # static member and an instance one), keep them both. This
            # prefiltering step should make the below sort less horrible, even
            # though I'm calling index().
            included_members = [m for m in members if m.name in included_set]
            # sort()'s stability should keep same-named members in the order
            # JSDoc spits them out in.
            included_members.sort(key=lambda m: include.index(m.name))
            return included_members

        return "\n\n".join(
            rst_for(member)
            for member in members_to_include(include)
            if ((not member.is_private) or should_include_private)
            and member.name not in exclude
        )


class AutoAttributeRenderer(JsRenderer):
    _template = "attribute.rst"
    _renderer_type = "attribute"

    def _template_vars(self, name: str, obj: Attribute) -> dict[str, Any]:  # type: ignore[override]
        return dict(
            name=name,
            description=self.render_description(obj.description),
            deprecated=obj.deprecated,
            is_optional=obj.is_optional,
            see_also=obj.see_alsos,
            examples=[self.render_description(ex) for ex in obj.examples],
            type=self.render_type(obj.type),
            content="\n".join(self._content),
        )


def unwrapped(text: str) -> str:
    """Return the text with line wrapping removed."""
    return sub(r"[ \t]*[\r\n]+[ \t]*", " ", text)
