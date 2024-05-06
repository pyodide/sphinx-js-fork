from textwrap import dedent, indent
from typing import Any

import pytest
from sphinx.util import rst

from sphinx_js.ir import (
    Attribute,
    Class,
    DescriptionCode,
    DescriptionText,
    Exc,
    Function,
    Interface,
    Param,
    Return,
    TypeParam,
    TypeXRefExternal,
    TypeXRefInternal,
)
from sphinx_js.renderers import (
    AutoAttributeRenderer,
    AutoFunctionRenderer,
    render_description,
)


def setindent(txt):
    return indent(dedent(txt), " " * 3)


def test_render_description():
    assert render_description(
        [
            DescriptionText(text="Code 1 had "),
            DescriptionCode(code="`single ticks around it`"),
            DescriptionText(text=".\nCode 2 has "),
            DescriptionCode(code="``double ticks around it``"),
            DescriptionText(text=".\nCode 3 has a :sphinx:role:"),
            DescriptionCode(code="`before it`"),
            DescriptionText(text=".\n\n"),
            DescriptionCode(code="```js\nA JS code pen!\n```"),
            DescriptionText(text="\nAnd some closing words."),
        ]
    ) == dedent(
        """\
        Code 1 had ``single ticks around it``.
        Code 2 has ``double ticks around it``.
        Code 3 has a :sphinx:role:`before it`.


        .. code-block:: js

            A JS code pen!



        And some closing words."""
    )


def ts_xref_formatter(config, xref, kind):
    if isinstance(xref, TypeXRefInternal):
        name = rst.escape(xref.name)
        return f":js:{kind}:`{name}`"
    else:
        return xref.name


@pytest.fixture()
def function_renderer():
    class _config:
        pass

    class _app:
        config = _config

    def lookup_object(self, partial_path: list[str]):
        return self.objects[partial_path[-1]]

    renderer = AutoFunctionRenderer.__new__(AutoFunctionRenderer)
    renderer._app = _app
    renderer._explicit_formal_params = None
    renderer._content = []
    renderer._set_type_xref_formatter(ts_xref_formatter)
    renderer._add_span = False
    renderer.lookup_object = lookup_object.__get__(renderer)
    renderer.objects = {}
    return renderer


@pytest.fixture()
def attribute_renderer():
    class _config:
        pass

    class _app:
        config = _config

    renderer = AutoAttributeRenderer.__new__(AutoAttributeRenderer)
    renderer._app = _app
    renderer._explicit_formal_params = None
    renderer._content = []
    renderer._set_type_xref_formatter(ts_xref_formatter)
    renderer._add_span = False
    return renderer


@pytest.fixture()
def function_render(function_renderer) -> Any:
    def function_render(partial_path=None, use_short_name=False, objects=None, **args):
        if objects is None:
            objects = {}
        if not partial_path:
            partial_path = ["blah"]
        function_renderer.objects = objects
        return function_renderer.rst(
            partial_path, make_function(**args), use_short_name
        )

    return function_render


@pytest.fixture()
def attribute_render(attribute_renderer) -> Any:
    def attribute_render(partial_path=None, use_short_name=False, **args):
        if not partial_path:
            partial_path = ["blah"]
        return attribute_renderer.rst(
            partial_path, make_attribute(**args), use_short_name
        )

    return attribute_render


top_level_dict = dict(
    name="",
    path=[],
    filename="",
    deppath="",
    description="",
    line=0,
    deprecated="",
    examples=[],
    see_alsos=[],
    properties=[],
    exported_from=None,
)

member_dict = dict(
    is_abstract=False,
    is_optional=False,
    is_static=False,
    is_private=False,
)

members_and_supers_dict = dict(members=[], supers=[])

class_dict = (
    top_level_dict
    | members_and_supers_dict
    | dict(constructor_=None, is_abstract=False, interfaces=[], type_params=[])
)
interface_dict = top_level_dict | members_and_supers_dict | dict(type_params=[])
function_dict = (
    top_level_dict
    | member_dict
    | dict(
        is_async=False,
        params=[],
        exceptions=[],
        returns=[],
    )
)
attribute_dict = top_level_dict | member_dict | dict(type="")


def make_class(**args):
    return Class(**(class_dict | args))


def make_interface(**args):
    return Interface(**(interface_dict | args))


def make_function(**args):
    return Function(**(function_dict | args))


def make_attribute(**args):
    return Attribute(**(attribute_dict | args))


DEFAULT_RESULT = ".. js:function:: blah()\n"


def test_func_render_simple(function_render):
    assert function_render() == DEFAULT_RESULT


def test_func_render_shortnames(function_render):
    assert function_render(["a.", "b.", "c"]) == ".. js:function:: a.b.c()\n"
    assert (
        function_render(["a.", "b.", "c"], use_short_name=True)
        == ".. js:function:: c()\n"
    )


def test_func_render_flags(function_render):
    # is_abstract is ignored? Maybe only makes sense if it is a class method??
    # TODO: look into this.
    assert function_render(is_abstract=True) == DEFAULT_RESULT
    assert function_render(is_optional=True) == ".. js:function:: blah?()\n"
    assert function_render(is_static=True) == ".. js:function:: blah()\n   :static:\n"
    assert function_render(is_async=True) == ".. js:function:: blah()\n   :async:\n"
    assert (
        function_render(is_async=True, is_static=True)
        == ".. js:function:: blah()\n   :static:\n   :async:\n"
    )
    assert function_render(is_private=True) == DEFAULT_RESULT


def test_func_render_description(function_render):
    assert function_render(
        description="this is a description"
    ) == DEFAULT_RESULT + setindent(
        """
        this is a description
        """,
    )


def test_func_render_params(function_render):
    assert function_render(
        description="this is a description",
        params=[Param("a", description="a description")],
    ) == dedent(
        """\
        .. js:function:: blah(a)

           this is a description

           :param a: a description
        """
    )
    assert function_render(
        description="this is a description",
        params=[Param("a", description="a description"), Param("b", "b description")],
    ) == dedent(
        """\
        .. js:function:: blah(a, b)

           this is a description

           :param a: a description
           :param b: b description
        """
    )


def test_func_render_returns(function_render):
    assert function_render(
        params=[Param("a", description="a description"), Param("b", "b description")],
        returns=[Return("number", "first thing"), Return("string", "second thing")],
    ) == dedent(
        """\
        .. js:function:: blah(a, b)

           :param a: a description
           :param b: b description
           :returns: **number** -- first thing
           :returns: **string** -- second thing
        """
    )


def test_func_render_type_params(function_render):
    assert function_render(
        params=[Param("a", type="T"), Param("b", type="S")],
        type_params=[
            TypeParam("T", "number", "a type param"),
            TypeParam("S", "", "second type param"),
        ],
    ) == dedent(
        """\
        .. js:function:: blah<T, S>(a, b)

           :typeparam T: a type param (extends **number**)
           :typeparam S: second type param
           :param a:
           :param b:
           :type a: **T**
           :type b: **S**
        """
    )


def test_render_xref(function_renderer: AutoFunctionRenderer):
    function_renderer.objects["A"] = make_class()
    assert (
        function_renderer.render_type([TypeXRefInternal(name="A", path=["a.", "A"])])
        == ":js:class:`A`"
    )
    function_renderer.objects["A"] = make_interface()
    assert (
        function_renderer.render_type([TypeXRefInternal(name="A", path=["a.", "A"])])
        == ":js:interface:`A`"
    )
    assert (
        function_renderer.render_type(
            [TypeXRefInternal(name="A", path=["a.", "A"]), "[]"]
        )
        == r":js:interface:`A`\ []"
    )
    xref_external = TypeXRefExternal("A", "blah", "a.ts", "a.A")
    assert function_renderer.render_type([xref_external]) == "A"
    res = []

    def xref_render(config, val, kind):
        res.append([config, val])
        return f"{val.package}::{val.name}::{kind}"

    function_renderer._set_type_xref_formatter(xref_render)
    assert function_renderer.render_type([xref_external]) == "blah::A::None"
    assert res[0][0] == function_renderer._app.config
    assert res[0][1] == xref_external


def test_func_render_param_type(function_render):
    assert function_render(
        description="this is a description",
        params=[Param("a", description="a description", type="xxx")],
    ) == dedent(
        """\
        .. js:function:: blah(a)

           this is a description

           :param a: a description
           :type a: **xxx**
        """
    )
    assert function_render(
        objects={"A": make_interface()},
        params=[
            Param(
                "a",
                description="a description",
                type=[TypeXRefInternal(name="A", path=["a.", "A"])],
            )
        ],
    ) == dedent(
        """\
        .. js:function:: blah(a)

           :param a: a description
           :type a: :js:interface:`A`
        """
    )


def test_func_render_param_options(function_render):
    assert (
        function_render(
            params=[
                Param(
                    "a",
                    has_default=True,
                    default="5",
                )
            ],
        )
        == ".. js:function:: blah(a=5)\n"
    )
    assert function_render(
        params=[
            Param(
                "a",
                is_variadic=True,
            )
        ],
    ) == dedent(".. js:function:: blah(...a)\n")


def test_func_render_param_exceptions(function_render):
    assert function_render(
        description="this is a description", exceptions=[Exc("TypeError", "")]
    ) == dedent(
        """\
        .. js:function:: blah()

           this is a description

           :throws TypeError:
        """
    )


def test_func_render_callouts(function_render):
    assert function_render(deprecated=True) == DEFAULT_RESULT + setindent(
        """
        .. note::

           Deprecated.
        """,
    )
    assert function_render(deprecated="v0.24") == DEFAULT_RESULT + setindent(
        """
        .. note::

           Deprecated: v0.24
        """,
    )
    assert function_render(see_alsos=["see", "this too"]) == DEFAULT_RESULT + setindent(
        """
        .. seealso::

           - :any:`see`
           - :any:`this too`
        """,
    )


def test_all(function_render):
    assert function_render(
        description="description",
        params=[Param("a", "xx")],
        deprecated=True,
        exceptions=[Exc("TypeError", "")],
        examples=["ex1"],
        see_alsos=["see"],
    ) == dedent(
        """\
        .. js:function:: blah(a)

           .. note::

              Deprecated.

           description

           :param a: xx
           :throws TypeError:

           .. admonition:: Example

              ex1

           .. seealso::

              - :any:`see`
       """
    )


def test_examples(function_render):
    assert function_render(examples=["ex1", "ex2"]) == DEFAULT_RESULT + setindent(
        """
        .. admonition:: Example

           ex1

        .. admonition:: Example

           ex2
        """,
    )

    assert function_render(
        examples=[[DescriptionText(text="This is another example.\n")]]
    ) == DEFAULT_RESULT + setindent(
        """
           .. admonition:: Example

              This is another example.
        """
    )

    assert function_render(
        examples=[
            [DescriptionCode(code="```ts\nThis is an example.\n```")],
            [
                DescriptionText(text="This is another example.\n"),
                DescriptionCode(code="```py\nSomething python\n```"),
            ],
        ]
    ) == DEFAULT_RESULT + setindent(
        """
           .. admonition:: Example

              .. code-block:: ts

                  This is an example.

           .. admonition:: Example

              This is another example.

              .. code-block:: py

                  Something python
        """
    )
