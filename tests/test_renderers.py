from textwrap import dedent, indent

import pytest

from sphinx_js.ir import Exc, Function, Param, Return, TypeParam, TypeXRefInternal, DescriptionText, DescriptionCode
from sphinx_js.renderers import AutoFunctionRenderer, JsRenderer


def setindent(txt):
    return indent(dedent(txt), " " * 3)

def test_render_description():
    renderer = JsRenderer.__new__(JsRenderer)
    renderer.render_description([
        DescriptionText(text='Code 1 had '),
        DescriptionCode(code='`single ticks around it`'),
        DescriptionText(text='.\nCode 2 has '),
        DescriptionCode(code='``double ticks around it``'),
        DescriptionText(text='.\nCode 3 has a :sphinx:role:'),
        DescriptionCode(code='`before it`'),
        DescriptionText(text='.\n\n'),
        DescriptionCode(code='```js\nA JS code pen!\n```'),
        DescriptionText(text='\nAnd some closing words.'),
    ]) == dedent(
        """\
        Code 1 had ``single ticks around it``.
        Code 2 has ``double ticks around it``.
        Code 3 has a :sphinx:role:`before it`.

        .. code-block:: js

            A JS code pen!


        And some closing words."""
    )


@pytest.fixture()
def function_render() -> AutoFunctionRenderer:
    renderer = AutoFunctionRenderer.__new__(AutoFunctionRenderer)
    renderer._explicit_formal_params = None
    renderer._content = []

    def function_render(partial_path=None, use_short_name=False, **args):
        if not partial_path:
            partial_path = ["blah"]
        return renderer.rst(partial_path, make_function(**args), use_short_name)

    return function_render


def make_function(**args):
    args = (
        dict(
            is_abstract=False,
            is_optional=False,
            is_static=False,
            is_private=False,
            name="",
            path=[],
            filename="",
            deppath="",
            description="",
            line="",
            deprecated="",
            examples=[],
            see_alsos=[],
            properties=[],
            exported_from=None,
            params=[],
            exceptions=[],
            returns=[],
        )
        | args
    )
    return Function(**args)


#  'is_abstract', 'is_optional', 'is_static', 'is_private', 'name', 'path',
#  'filename', 'deppath', 'description', 'line', 'deprecated', 'examples',
#  'see_alsos', 'properties', 'exported_from', 'params', 'exceptions', and
#  'returns'


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
    assert function_render(is_static=True) == ".. js:staticfunction:: blah()\n"
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
            TypeParam("T", "", "a type param"),
            TypeParam("S", "", "second type param"),
        ],
    ) == dedent(
        """\
        .. js:function:: blah(a, b)

           :typeparam T: a type param
           :typeparam S: second type param
           :param a:
           :param b:
           :type a: **T**
           :type b: **S**
        """
    )


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
           :type a: :js:class:`A`
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
    assert function_render(examples=["ex1", "ex2"]) == DEFAULT_RESULT + setindent(
        """
        **Examples:**

        .. code-block:: js

           ex1

        .. code-block:: js

           ex2
        """,
    )
    assert function_render(see_alsos=["see", "this too"]) == DEFAULT_RESULT + setindent(
        """
        .. seealso::

           - :any:`see`
           - :any:`this too`
        """,
    )
