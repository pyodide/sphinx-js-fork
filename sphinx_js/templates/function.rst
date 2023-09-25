{% import 'common.rst' as common %}

.. js:function:: {{ name }}{{ '?' if is_optional else '' }}{{ params }}
   {% if is_static -%}
   :static:
   {% endif %}
   {%- if is_async -%}
   :async:
   {% endif %}

   {{ common.deprecated(deprecated)|indent(3) }}

   {% if description -%}
     {{ description|indent(3) }}
   {%- endif %}

   {% for heads, tail in fields -%}
     :{{ heads|join(' ') }}: {{ tail }}
   {% endfor %}

   {{ common.examples(examples)|indent(3) }}

   {{ content|indent(3) }}

   {{ common.see_also(see_also)|indent(3) }}
