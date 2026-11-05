"""Plain-HTTP MCP endpoint for ARM Compiler 5 experiments on a trusted LAN."""
from __future__ import annotations

import os

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from worker_core import ARMCCWorker

worker = ARMCCWorker()
mcp = MCPServer(
    "armcc5-windows",
    instructions="Compile Cortex-M3 C probes and inspect/link outputs using ARM Compiler 5 on x1-carbon. Use compiler_capabilities to identify MDK-Lite license restrictions.",
)


@mcp.tool()
def compiler_info() -> dict:
    """Report compiler paths, version, and observed license capabilities."""
    return worker.compiler_info()


@mcp.tool()
def compiler_capabilities() -> dict:
    """Show measured MDK-Lite capabilities and explicit license-blocked operations."""
    return worker.compiler_capabilities()


@mcp.tool()
def compile_c(source: str, optimization: int = 2, debug: bool = True,
              extra_flags: list[str] | None = None) -> dict:
    """Compile C source to a native ARMCC5 object; return artifact ID and diagnostics."""
    return worker.compile_c(source, optimization, debug, extra_flags)


@mcp.tool()
def compile_files(files: list[dict], sources: list[str] | None = None,
                  include_paths: list[str] | None = None, optimization: int = 2,
                  debug: bool = True, extra_flags: list[str] | None = None,
                  mode: str = "object") -> dict:
    """Stage C sources/headers; compile separate objects, preprocess -E, or request a license-limited -S assembly listing.

    files: [{path: 'src/main.c', content: '...'}, {path: 'include/foo.h', content: '...'}].
    sources: optional paths of C files to compile; defaults to all staged .c files.
    include_paths: directories under staged tree, e.g. ['include'].
    mode: object | preprocess | assembly. Assembly is blocked by MDK-ARM Lite (C3921U).
    """
    return worker.compile_files(files, sources, include_paths, optimization, debug, extra_flags, mode)


@mcp.tool()
def inspect_object(artifact_id: str, view: str = "all", save_output: bool = False) -> dict:
    """Use fromelf: code, symbols, relocations, sections, all, or source-interleaved disassembly ('source').

    save_output=True registers complete text output as a downloadable artifact.
    """
    return worker.inspect_object(artifact_id, view, save_output)


@mcp.tool()
def export_elf(artifact_id: str) -> dict:
    """Try fromelf --elf on an object. MDK-Lite rejects ORC conversion (Q3921U); returns the exact license diagnostic."""
    return worker.export_elf(artifact_id)


@mcp.tool()
def link_probe(object_ids: list[str], entry_symbol: str = "main",
               scatter_content: str | None = None) -> dict:
    """Link ARMCC5 objects with --map and a downloadable map artifact; optionally supply a scatter file."""
    return worker.link_probe(object_ids, entry_symbol, scatter_content)


@mcp.tool()
def get_artifact(artifact_id: str, offset: int = 0, max_bytes: int = 65536) -> dict:
    """Download original binary or text artifact in base64 chunks, with native format indication."""
    return worker.get_artifact(artifact_id, offset, max_bytes)


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.getenv("ARMCC_HTTP_HOST", "0.0.0.0"),
        port=int(os.getenv("ARMCC_HTTP_PORT", "8765")),
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
