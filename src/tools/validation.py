"""Input/output schema validation helpers for tool invocations.

Uses ``jsonschema`` to validate tool inputs and outputs against the JSON
Schemas declared on each ``ToolDefinition``.  Raises typed errors on failure
so callers can distinguish input problems from output problems.
"""

from __future__ import annotations

from typing import Any

import jsonschema

from src.tools.registry import ToolDefinition


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InputValidationError(Exception):
    """Raised when a tool's inputs fail JSON Schema validation.

    Attributes:
        tool_name: The name of the tool whose input failed validation.
        original_error: The underlying ``jsonschema.ValidationError``.
    """

    def __init__(self, tool_name: str, original_error: jsonschema.ValidationError) -> None:
        self.tool_name = tool_name
        self.original_error = original_error
        super().__init__(
            f"Input validation failed for tool '{tool_name}': {original_error.message}"
        )


class OutputValidationError(Exception):
    """Raised when a tool's output fails JSON Schema validation.

    Attributes:
        tool_name: The name of the tool whose output failed validation.
        original_error: The underlying ``jsonschema.ValidationError``.
    """

    def __init__(self, tool_name: str, original_error: jsonschema.ValidationError) -> None:
        self.tool_name = tool_name
        self.original_error = original_error
        super().__init__(
            f"Output validation failed for tool '{tool_name}': {original_error.message}"
        )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_input(tool: ToolDefinition, inputs: dict[str, Any]) -> None:
    """Validate *inputs* against *tool*'s ``input_schema``.

    Passes silently when validation succeeds.

    Raises:
        InputValidationError: wrapping the underlying ``jsonschema.ValidationError``
            when *inputs* does not conform to the schema.
    """
    try:
        jsonschema.validate(instance=inputs, schema=tool.input_schema)
    except jsonschema.ValidationError as exc:
        raise InputValidationError(tool_name=tool.name, original_error=exc) from exc


def validate_output(tool: ToolDefinition, output: Any) -> None:
    """Validate *output* against *tool*'s ``output_schema``.

    Passes silently when validation succeeds.

    Raises:
        OutputValidationError: wrapping the underlying ``jsonschema.ValidationError``
            when *output* does not conform to the schema.
    """
    try:
        jsonschema.validate(instance=output, schema=tool.output_schema)
    except jsonschema.ValidationError as exc:
        raise OutputValidationError(tool_name=tool.name, original_error=exc) from exc
