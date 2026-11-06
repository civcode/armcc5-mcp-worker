# ARMCC5 MCP worker

Small Windows-hosted compiler worker for ARM Compiler 5.06u6, accessed from the Pi coding agent on a Linux workstation over **plain LAN HTTP** (Streamable HTTP MCP). Designed for Cortex-M3 compiler probes used in the BU03/BU04 PDoA reverse-engineering effort.

This repository is intentionally independent of the STM32F103-BU0x firmware build. There is no GitHub Actions workflow and no Windows service, SSH server, TLS, or account setup.

## x1-carbon: install and start

Requires Python 3.11+ and the existing ARM Compiler installation in its usual Keil directory (default `%LOCALAPPDATA%\Keil_v5\ARM\ARM_Compiler_5.06u6`).

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
start.cmd
```

### Custom ARM Compiler installation path

The worker uses `ARMCC_ROOT` to locate the compiler. Set it to the **installation directory containing the `bin` folder**, not to `bin` itself. The launcher (`start.cmd`) respects this setting and adds `%ARMCC_ROOT%\bin` to `PATH`; no source-code changes or relocation of the compiler are required.

For example, the working installation on **x1-carbon** is at `C:\DevTools\KeilAC5\ARM\ARM_Compiler_5.06u6\bin`. From the worker checkout in the **same Command Prompt**:

```bat
C:
cd \workspace\armcc5-mcp-worker
set "ARMCC_ROOT=C:\DevTools\KeilAC5\ARM\ARM_Compiler_5.06u6"
"%ARMCC_ROOT%\bin\armcc.exe" --vsn
```

Example of the version output observed on x1-carbon:

```text
Product: MDK-ARM Lite 5.43
Component: ARM Compiler 5.06 update 6 (build 750)
Tool: armcc [4d365
```

The Windows capability probe on 2026-11-05 confirms **5.06 update 6 (build 750)** with **MDK-ARM Lite 5.43**. This matches the compiler update/build reported for the original vendor libraries. Record actual version, flags, headers and license behavior for every comparison.

After verifying the path, install Python dependencies if needed and start the MCP endpoint **in that same Command Prompt**:

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
start.cmd
```

The `ARMCC_ROOT` setting made with `set` is temporary and applies only to that Command Prompt. Set it again in new sessions, or set it permanently through your Windows environment configuration. To check the resolved paths from Oberon, call the MCP `compiler_info` tool: it reports the compiler root and availability of `armcc.exe`, `armlink.exe`, and `fromelf.exe`.

**Note:** `armcc.exe --vsn` checks compiler availability, not whether a test program will compile/link under the MDK-ARM Lite license.

The process listens on `0.0.0.0:8765` and serves MCP at `http://x1-carbon.local:8765/mcp`. Press **Ctrl+C once** in the launching terminal for graceful shutdown: Uvicorn stops accepting requests and drains the HTTP server, then the worker explicitly removes its temporary compilation workspace and exits without a Python traceback. The optional `ARMCC_ARTIFACT_DIR` persistent store is **not deleted**. Normal interpreter exit also has an idempotent `atexit` cleanup fallback. Do not use repeated Ctrl+C, Ctrl+Break or force-kill if you need graceful completion. The first connection may prompt for a Windows Defender Firewall exception for the private LAN. If necessary, set `ARMCC_ROOT`, `ARMCC_HTTP_HOST`, or `ARMCC_HTTP_PORT` before running `start.cmd`.

No firmware repository checkout is required on Windows. Client-supplied source/header trees are staged into temporary per-job folders; individual headers, sources and include paths must be supplied in the MCP request.

## Oberon: connect Pi

```bash
pi mcp add armcc5 --url http://x1-carbon.local:8765/mcp
pi mcp list
```

**Verified on Oberon:** `http://x1-carbon.local:8765/mcp` was verified with the previous five MCP tools. The short hostname `x1-carbon` returned `fetch failed`; the `.local` name resolves through mDNS on this LAN. If `.local` is not resolvable on another host, use the Windows machine's LAN IP address. Alternatively, merge `pi-mcp.json` into `~/.pi/agent/mcp.json`. In Pi, `/mcp` shows connection status. This confirms MCP connectivity, not yet successful compilation by the real ARMCC toolchain.

Example request to Pi:

> Use armcc5 to show the compiler version. Compile `float pdoa_scale(float p) { return p * 0.5f; }` with optimization 2, then inspect symbols and disassembly. Transfer the object to Oberon and compare it with ATfE output.

## Tools

| Tool | Purpose |
| --- | --- |
| `compiler_info` | Tool availability, actual armcc version and observed license capabilities |
| `compiler_capabilities` | Explicitly identify compiler capabilities and MDK-Lite license restrictions |
| `compile_c` | Existing single-source compilation interface (unchanged) |
| `compile_files` | Stage multiple sources/headers with relative include paths; mode `object`, `preprocess`, or `assembly` |
| `inspect_object` | fromelf views `code`, `symbols`, `relocations`, `sections`, `all`, and `source`; `save_output=true` stores complete text |
| `inspect_structured` | Return parsed JSON sections/symbols/relocations plus full original fromelf text and parsing warnings |
| `export_elf` | Try native `fromelf --elf` and explain `Q3921U` if MDK-Lite forbids it |
| `link_probe` | Link up to 12 objects; return downloadable `probe.map`; optional `scatter_content` |
| `get_artifact` | Chunked, lossless Base64 artifact download with original ORC/ELF/text format identification |
| `upload_artifact` | Upload original vendor ORC/ELF objects in Base64 (8 MiB limit, optional SHA-256 verification) |
| `list_artifacts` | List session artifacts or SHA-256 persistent artifacts with `ARMCC_ARTIFACT_DIR` |
| `compare_objects` | Symmetric native `fromelf` comparison of sections, symbols, relocations; never invent ORC .text byte parity |
| `compile_asm` | ARMASM5 assembly of source text or exact bytes supplied in Base64 |
| `tool_help` / `list_flags` | Native compiler, assembler, linker or fromelf `--help` output |
| `convert_image` | Convert a linked AXF to Intel HEX via `fromelf --i32` / `--i32combined` |

### Reproducible source-tree compilation

`compile_files` accepts at most 128 staged UTF-8 files (1 MiB each, 4 MiB total).
All paths are relative to an isolated job tree, using forward slashes. Sources
compile separately, so functions/sections are not merged accidentally. Only
include directories inside the uploaded tree are accepted. Up to 24 source files
can be compiled per call. The returned per-source `command`, `source_sha256`
and native artifact ID allow faithful reconstruction of the command line.

Example inputs (MCP parameters shown in JSON):

```json
{
  "files": [
    {"path": "src/node.c", "content": "#include \"node.h\"\nint node(void){return ANSWER;}"},
    {"path": "include/node.h", "content": "#define ANSWER 42\n"}
  ],
  "sources": ["src/node.c"],
  "include_paths": ["include"],
  "optimization": 3,
  "debug": true,
  "extra_flags": ["--apcs=interwork", "-D__MICROLIB"],
  "mode": "object"
}
```

Use `mode="preprocess"` to invoke **ARMCC's native `-E`**, saving the
entire `.i` file for transfer through `get_artifact`. It does not use host
cpp or ARMCC-compatibility shims. Use `inspect_object(view="source",
save_output=true)` for source-interleaved `fromelf` disassembly and download
the complete report via its `report.artifact_id`, avoiding the inline text
size limit. The original native object bytes remain unchanged.

For a structured comparison, call `inspect_structured(artifact_id)`.
It runs native `fromelf --text -c -s -r` and returns JSON arrays named
`sections`, `symbols`, and `relocations`, with typed numeric fields.
It includes both a `raw_report` artifact and a `structured_report`
(JSON text) artifact for complete chunked retrieval. **This is a tested,
best-effort parser of fromelf text, not native fromelf XML or an ELF
conversion**. Always inspect `structured.complete` and
`structured.parse_warnings` before using the JSON for strict ABI comparisons.
The parser was cross-checked against the 2026-11-05 Windows probe output
(40 sections, 30 symbols, 40 relocations, no count warnings); other object
layouts may need additional parsing cases.

### Fidelity, linker reports and vendor-object parity

The worker exposes strict tool parameters. An unknown name such as `flags`
returns a structured `UnknownToolArgument` response instead of silently
falling back to the default `-O2 -g`. Use `extra_flags` with `compile_c`,
or `extra_flags` / `raw_compiler_flags` with `compile_files`.
`raw_compiler_flags` **replaces** the built-in CPU, Thumb, C99,
optimization and debug switches; the full effective command is returned.
Do not combine `raw_compiler_flags` and `extra_flags`.

Each `compile_files` entry accepts either `content` (UTF-8 text) or
`content_base64` (original, unmodified bytes). This preserves GBK source
strings, CRLF and other non-ASCII source content that would change under UTF-8
conversion. The staged project limits are 128 files, 1 MiB per file, 4 MiB
total, up to 24 .c sources, 32 include paths, and 32 flags of 240 characters
(`raw_compiler_flags` up to 64). `compile_c` remains limited to 256 KiB and
20 flags of 120 characters.

`compile_asm(source=...)` assembles an ARMASM5 `.s` source using native
ARMASM syntax (`AREA`, `THUMB`, `EXPORT`, `END`).
Alternatively, `content_base64` supplies original bytes, up to 256 KiB.
Whether a particular assembly/linker option is available under the installed
MDK-Lite license is reported by the underlying executable.

Normal `link_probe` now writes a **full linker report** using
`--map --xref --symbols --info=unused --list=probe.map`, covering section
cross-references and discarded input sections as well as memory placement.
It accepts up to 12 object/library artifact IDs, optional scatter text, and
`link_flags` (up to 40 options, 240 characters each). The worker reserves
its output, listing and scatter paths. `link_flags=["--reloc"]` and
`link_flags=["--partial"]` are independent, experimental invocations, **not
lossless ORC-to-ELF conversions**. Partial output is identified by its actual
file signature and ELF type where possible. Actual Lite-license support for
these options remains unverified on x1-carbon.

`convert_image(artifact_id, format="i32combined")` invokes **fromelf** to
generate Intel HEX from a linked image; it does not pretend that
`armlink --format=ihex` is the same operation. `tool_help(tool="armlink")`
(or `list_flags`) saves the authentic `--help` output as an artifact without
requiring an output object.

`upload_artifact(filename, content_base64, sha256="")` registers the original
vendor object without modifying its bytes. Call `inspect_structured` on both
vendor and candidate artifacts or `compare_objects(a, b)`, which uses the
**same native fromelf pipeline on both inputs**. The structured comparison
normalizes common ARMCC symbol attributes and reports section, symbol,
relocation and data-section metadata differences. For ORC,
`text_identical=null` because exact `.text` bytes are not independently
available; this does not claim byte-identical code.

By default artifacts disappear when the worker stops. To persist them, set
`ARMCC_ARTIFACT_DIR` to a Windows directory **before launching** the server.
IDs then derive from SHA-256 and survive restarts; `list_artifacts` lists
their metadata. Persistence does not retroactively restore old ephemeral UUID
IDs, and the store is not automatically pruned. Unknown artifact IDs now
produce structured error results.

`inspect_object(view="source")` succeeds without debug data but cannot
show source lines then. The response now sets
`source_interleaving_detected=false` and includes a warning when no `;;;`
source annotations are found; build with `debug=true` / `-g` and retain
matching source files for meaningful line mapping.

### License-limited features remain visible

The real Windows probe of ARM Compiler **5.06u6 build 750 / MDK-ARM Lite** on
2026-11-05 established:

| Operation | Observed result | MCP behavior |
| --- | --- | --- |
| ARMCC compilation, `-I`, `-E` | Available | `compile_files(mode="object"/"preprocess")` |
| `fromelf --text -c --interleave=source` | Available | `inspect_object(view="source")` |
| `armcc -S --interleave` or `--asm` | **License denies: C3921U** | `compile_files(mode="assembly")` executes the command and returns `unavailable_reason="license"` with the original diagnostic |
| `fromelf --elf` on symbolic ORC | **License denies: Q3921U** | `export_elf(object_id)` executes it and returns `unavailable_reason="license"` with the original diagnostic |
| `armlink --map --list=...` | Available | `link_probe` returns `axf` and `map` artifacts |

Unsupported-by-license operations are **not hidden or emulated**: the MCP
worker attempts the requested operation and returns the exact ARMCC/fromelf
diagnostic and a practical alternative. A future upgraded license could allow
the same operation to succeed without an MCP code change. Native `.o`
artifacts on the measured Lite installation begin with `F7 4F 52 43`
(ORC); linked AXF is ELF (`7F 45 4C 46`). `get_artifact` never wraps or
converts the original bytes. A linked AXF is not a substitute for the original
relocatable object.

`link_probe` now captures a complete map through `--map --list=...`,
and optionally uses supplied `scatter_content` instead of its default
RO/RW base addresses. Link-size restrictions from the installed license
still apply. A successful map alone does not establish ABI/runtime parity.

Without ARMCC_ARTIFACT_DIR, artifacts are session-local and deleted on shutdown. The worker does not provide arbitrary shell execution.

## Tests

From the repository root, on Linux or Windows:

```bash
python -m unittest discover -s tests -v
python -m compileall -q server.py worker_lifecycle.py worker_core.py compiler_features.py artifact_store.py object_comparison.py request_guard.py structured_inspection.py
```

The tests use **Python subprocess mocks**, not real compiler binaries. They create empty `.exe` placeholders only to verify availability checks, then simulate `armcc`, `fromelf`, and `armlink` execution in Python; this makes the suite portable to **Windows and Linux**. They verify the orchestration logic but **do not** establish real ARMCC5/Keil behavior or an end-to-end Windows-to-Pi connection. The MCP transport itself requires `pip install -r requirements.txt`.

**Windows troubleshooting:** Older test revisions wrote Unix `#!` Python scripts with an `.exe` extension and attempted to execute them. Windows then displayed an **Unsupported 16-Bit Application** dialog for a path resembling `%TEMP%\\tmp...\\bin\\armcc.exe`. That path is a *test mock*, not the ARM compiler in `C:\\DevTools\\KeilAC5\\ARM\\ARM_Compiler_5.06u6\\bin`. Update the checkout (`git pull`) to get the corrected tests; no compiler reinstall is needed.

## Why two toolchains?

On **Oberon**, ATfE 21.1.1 (Clang/LLD) builds the complete firmware. On **x1-carbon**, the historical ARMCC5 compiler compiles small test cases to compare generated code and ABI details with the original proprietary libraries. The ARMCC linker is used only for license-permitted small test programs, not for building the full firmware.

The tested MDK-ARM Lite installation emits native ORC symbolic objects, not relocatable ELF. `export_elf` reports the license error rather than claiming conversion; `fromelf` inspection and the linker map remain available for comparisons.

## Trusted-LAN deployment

This server intentionally has **no authentication or encryption**. It accepts user-supplied C source and selected compiler flags and runs compiler subprocesses. Keep port 8765 off public or untrusted networks. A small flag and file-size validation exists to prevent accidental misuse, not to provide a secure execution sandbox. The MCP SDK's DNS rebinding protection is explicitly disabled for LAN hostname access.

`mcp>=2.1,<3` targets the MCP Python SDK v2 API (`MCPServer`, transport configuration on `run()`). If you use a different SDK major version, adjust the imports/transport setup accordingly.

## Repository integration

Canonical source: [civcode/armcc5-mcp-worker](https://github.com/civcode/armcc5-mcp-worker).

The `ATfE-port` branch of [civcode/STM32F103-BU0x_SDK](https://github.com/civcode/STM32F103-BU0x_SDK/tree/ATfE-port) references this independent worker with a pinned Git submodule. See [PUBLISHING.md](PUBLISHING.md) for cloning and deployment instructions. There are no GitHub Actions workflows in the worker repository.
