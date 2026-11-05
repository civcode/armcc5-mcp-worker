"""Reject unrecognized MCP tool arguments before the SDK drops unknown keys.

MCP Python SDK v2 exposes the raw tools/call argument mapping through
ctx.request_context.params['arguments']. This module intentionally does not
import the MCP SDK, so the check can be tested without networking.
"""
from __future__ import annotations


def checked_call(ctx, tool_name: str, allowed: set[str], operation, *args):
    try:
        params = ctx.request_context.params
        if isinstance(params, dict):
            raw = params["arguments"]
        else:
            raw = params.arguments
        if not isinstance(raw, dict):
            raise TypeError("raw arguments are not a dictionary")
    except (AttributeError, TypeError, KeyError) as ex:
        return {"ok": False, "error": f"Cannot validate raw MCP arguments for {tool_name}: {ex}",
                "error_type": "ValidationUnavailable"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        detail = "; use extra_flags for compile_c/compile_files, link_flags for link_probe" if "flags" in unknown else ""
        return {"ok": False, "error": f"Unknown arguments for {tool_name}: {', '.join(unknown)}{detail}",
                "error_type": "UnknownToolArgument", "unknown_arguments": unknown,
                "accepted_arguments": sorted(allowed)}
    try:
        return operation(*args)
    except (ValueError, OSError, FileNotFoundError) as ex:
        return {"ok": False, "error": str(ex), "error_type": type(ex).__name__}
