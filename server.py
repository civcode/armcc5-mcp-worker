"""Plain-HTTP MCP endpoint for ARM Compiler 5 experiments on a trusted LAN."""
from __future__ import annotations

import os

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.transport_security import TransportSecuritySettings

from request_guard import checked_call
from worker_core import ARMCCWorker

worker = ARMCCWorker()
mcp = MCPServer(
    "armcc5-windows",
    instructions=("Authentic ARMCC5 compile/assemble/link/inspect tools. "
                  "Call compiler_capabilities for MDK-Lite limits. All tool arguments are strict; "
                  "use extra_flags, link_flags, or raw_compiler_flags, not a generic flags parameter."),
)


def checked(ctx: Context, tool: str, accepted: str, fn, *args) -> dict:
    return checked_call(ctx, tool, set(accepted.split()), fn, *args)


@mcp.tool()
def compiler_info(ctx: Context) -> dict:
    """Tool paths, ARMCC version, and license capability summary."""
    return checked(ctx, "compiler_info", "", worker.compiler_info)


@mcp.tool()
def compiler_capabilities(ctx: Context) -> dict:
    """Measured Lite features; blocked options are still attempted when called."""
    return checked(ctx, "compiler_capabilities", "", worker.compiler_capabilities)


@mcp.tool()
def compile_c(ctx: Context, source: str, optimization: int = 2, debug: bool = True,
              extra_flags: list[str] | None = None) -> dict:
    """Compile one UTF-8 C source (max 256 KiB); extra_flags max 20 x 120 chars. No generic flags parameter."""
    return checked(ctx, "compile_c", "source optimization debug extra_flags",
                   worker.compile_c, source, optimization, debug, extra_flags)


@mcp.tool()
def compile_files(ctx: Context, files: list[dict], sources: list[str] | None = None,
                  include_paths: list[str] | None = None, optimization: int = 2,
                  debug: bool = True, extra_flags: list[str] | None = None,
                  mode: str = "object", raw_compiler_flags: list[str] | None = None
                  ) -> dict:
    """Stage up to 128 C/headers, UTF-8 content OR byte-identical content_base64; object/preprocess/assembly modes.

    Up to 4 MiB total, 1 MiB/file, 24 .c sources, 32 include dirs, 32 extra_flags x 240 chars.
    raw_compiler_flags replaces default --cpu/--thumb/--c99/-O/-g (and cannot combine extra_flags).
    """
    return checked(ctx, "compile_files", "files sources include_paths optimization debug extra_flags mode raw_compiler_flags",
                   worker.compile_files, files, sources, include_paths, optimization, debug, extra_flags, mode, raw_compiler_flags)


@mcp.tool()
def compile_asm(ctx: Context, source: str = "", content_base64: str = "", extra_flags: list[str] | None = None
                ) -> dict:
    """Assemble native ARMASM5 AREA/THUMB .s source (UTF-8 or original bytes Base64), up to 256 KiB."""
    return checked(ctx, "compile_asm", "source content_base64 extra_flags",
                   worker.compile_asm, source, content_base64, extra_flags)


@mcp.tool()
def inspect_object(ctx: Context, artifact_id: str, view: str = "all", save_output: bool = False
                   ) -> dict:
    """Use fromelf code/symbols/relocations/sections/all/source; source annotations need -g and source. Full report via save_output."""
    return checked(ctx, "inspect_object", "artifact_id view save_output",
                   worker.inspect_object, artifact_id, view, save_output)


@mcp.tool()
def inspect_structured(ctx: Context, artifact_id: str) -> dict:
    """Parse complete native fromelf output; check complete and parse_warnings. Not equivalent to native ELF metadata."""
    return checked(ctx, "inspect_structured", "artifact_id", worker.inspect_structured, artifact_id)


@mcp.tool()
def compare_objects(ctx: Context, a: str, b: str) -> dict:
    """Compare vendor/candidate with the SAME fromelf parser: sections, symbols, relocs. Exact .text bytes unknown for ORC."""
    return checked(ctx, "compare_objects", "a b", worker.compare_objects, a, b)


@mcp.tool()
def export_elf(ctx: Context, artifact_id: str) -> dict:
    """Attempt native fromelf --elf; ORC conversion under MDK-Lite returns Q3921U license error."""
    return checked(ctx, "export_elf", "artifact_id", worker.export_elf, artifact_id)


@mcp.tool()
def upload_artifact(ctx: Context, filename: str, content_base64: str, sha256: str = ""
                    ) -> dict:
    """Upload original ORC/ELF/vendor binary as Base64 (max 8 MiB); optional checksum verification."""
    return checked(ctx, "upload_artifact", "filename content_base64 sha256",
                   worker.upload_artifact, filename, content_base64, sha256)


@mcp.tool()
def list_artifacts(ctx: Context, offset: int = 0, limit: int = 100) -> dict:
    """List artifact IDs (max 500/page); set ARMCC_ARTIFACT_DIR for persistence across restarts."""
    return checked(ctx, "list_artifacts", "offset limit", worker.list_artifacts, offset, limit)


@mcp.tool()
def link_probe(ctx: Context, object_ids: list[str], entry_symbol: str = "main",
               scatter_content: str | None = None, link_flags: list[str] | None = None
               ) -> dict:
    """Link up to 12 objects/libs; full map with --xref --info=unused --symbols; optional scatter_content and link_flags.

    --partial and --reloc are experimental and not lossless object conversion. HEX conversion is convert_image.
    """
    return checked(ctx, "link_probe", "object_ids entry_symbol scatter_content link_flags",
                   worker.link_probe, object_ids, entry_symbol, scatter_content, link_flags)


@mcp.tool()
def convert_image(ctx: Context, artifact_id: str, format: str = "i32combined") -> dict:
    """Convert linked ELF/AXF to Intel HEX using fromelf --i32/--i32combined (not armlink)."""
    return checked(ctx, "convert_image", "artifact_id format", worker.convert_image, artifact_id, format)


@mcp.tool()
def tool_help(ctx: Context, tool: str = "armcc") -> dict:
    """Return authentic --help for armcc, armasm, armlink or fromelf without requiring output object."""
    return checked(ctx, "tool_help", "tool", worker.tool_help, tool)


@mcp.tool()
def list_flags(ctx: Context, tool: str = "armcc") -> dict:
    """Alias for tool_help: complete native tool --help as downloadable report."""
    return checked(ctx, "list_flags", "tool", worker.tool_help, tool)


@mcp.tool()
def get_artifact(ctx: Context, artifact_id: str, offset: int = 0, max_bytes: int = 65536
                 ) -> dict:
    """Download original object/map/report as Base64 chunks (1..131072 bytes); errors are structured."""
    return checked(ctx, "get_artifact", "artifact_id offset max_bytes",
                   worker.get_artifact, artifact_id, offset, max_bytes)


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.getenv("ARMCC_HTTP_HOST", "0.0.0.0"),
        port=int(os.getenv("ARMCC_HTTP_PORT", "8765")),
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
