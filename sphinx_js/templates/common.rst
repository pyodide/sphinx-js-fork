{% macro deprecated(message) %}
{% if message -%}
.. note::

   Deprecated
   {%- if message is string -%}: {{ message }}{% else %}.{% endif -%}
{%- endif %}
{% endmacro %}

{% macro examples(items) %}
{% if items -%}
.. admonition:: Example {%- if items|length > 1 -%} s {%- endif %}

   {% for example in items -%}
   .. code-block:: js

      {{ example|indent(6) }}

   {% endfor %}
{%- endif %}
{% endmacro %}

{% macro see_also(items) %}
{% if items -%}
.. seealso::

   {% for reference in items -%}
   - :any:`{{ reference }}`
   {% endfor %}
{%- endif %}
{% endmacro %}

{% macro exported_from(pathname) %}
{% if pathname -%}
    *exported from* :js:mod:`{{ pathname.dotted() }}`
{%- endif %}
{% endmacro %}
