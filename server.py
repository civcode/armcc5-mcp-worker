"""Plain-HTTP MCP endpoint for ARM Compiler 5 experiments on a trusted LAN."""
from __future__ import annotations

import os

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from worker_core import ARMCCWorker

worker = ARMCCWorker()
mcp = MCPServer(
    "armcc5-windows",
    instructions="Compile Cortex-M3 C probes and inspect/link outputs using ARM Compiler 5.06u6 on x1-carbon.",
)


@mcp.tool()
def compiler_info() -> dict:
    """Show ARMCC5, fromelf, armlink paths and the installed compiler version."""
    return worker.compiler_info()


@mcp.tool()
def compile_c(source: str, optimization: int = 2, debug: bool = True,
              extra_flags: list[str] | None = None) -> dict:
    """Compile C source to a Cortex-M3 ARMCC5 object; return artifact ID and diagnostics."""
    return worker.compile_c(source, optimization, debug, extra_flags)


@mcp.tool()
def inspect_object(artifact_id: str, view: str = "all") -> dict:
    """Run fromelf on a generated object/AXF; view: code, symbols, relocations, sections or all."""
    return worker.inspect_object(artifact_id, view)


@mcp.tool()
def link_probe(object_ids: list[str], entry_symbol: str = "main") -> dict:
    """Link a small ARMCC5 object experiment (Lite linker limit still applies)."""
    return worker.link_probe(object_ids, entry_symbol)


@mcp.tool()
def get_artifact(artifact_id: str, offset: int = 0, max_bytes: int = 65536) -> dict:
    """Download an object/AXF as a base64 chunk; repeat with next_offset if needed."""
    return worker.get_artifact(artifact_id, offset, max_bytes)


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.getenv("ARMCC_HTTP_HOST", "0.0.0.0"),
        port=int(os.getenv("ARMCC_HTTP_PORT", "8765")),
        stateless_http=True,
        json_response=True,
        # Deliberately unrestricted Host/Origin for a plain trusted-LAN deployment.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
