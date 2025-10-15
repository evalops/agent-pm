"""Jinja templates for PRD rendering."""

from jinja2 import Template

PRD_TEMPLATE = Template(
    """# PRD: {{ title }}

## Context
{{ context }}

## Problem
{{ problem }}

## Goals / Non-Goals
- Goals:
{% for g in goals %}- {{ g }}{% endfor %}
- Non-Goals:
{% for n in nongoals %}- {{ n }}{% endfor %}

## Users & Use Cases
{{ users }}

## Requirements
{% for req in requirements %}- {{ req }}{% endfor %}

## Acceptance Criteria
{% for ac in acceptance %}- {{ ac }}{% endfor %}

## Risks & Open Questions
{% for r in risks %}- {{ r }}{% endfor %}

{% if related_initiatives %}
## Related Initiatives
{% for item in related_initiatives %}- {{ item.idea }}{% if item.similarity is defined %} (similarity {{ '%.2f' | format(item.similarity) }}){% endif %}: {{ ", ".join(item.overlapping_goals) }}{% endfor %}
{% endif %}
"""
)

__all__ = ["PRD_TEMPLATE"]
