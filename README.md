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

The process listens on `0.0.0.0:8765` and serves MCP at `http://x1-carbon:8765/mcp`; close the terminal or press Ctrl+C to stop it. The first connection may prompt for a Windows Defender Firewall exception for the private LAN. If necessary, set `ARMCC_ROOT`, `ARMCC_HTTP_HOST`, or `ARMCC_HTTP_PORT` before running `start.cmd`.

No firmware repository checkout or SDK libraries are required on Windows. Only source snippets sent by the client are compiled and kept in temporary per-job folders.

## Oberon: connect Pi

```bash
pi mcp add armcc5 --url http://x1-carbon:8765/mcp
pi mcp list
```

If `x1-carbon` is not resolvable on the LAN, use the Windows machine's IP address. Alternatively, merge `pi-mcp.json` into `~/.pi/agent/mcp.json`. In Pi, `/mcp` shows connection status.

Example request to Pi:

> Use armcc5 to show the compiler version. Compile `float pdoa_scale(float p) { return p * 0.5f; }` with optimization 2, then inspect symbols and disassembly. Transfer the object to Oberon and compare it with ATfE output.

## Tools

| Tool | Purpose |
| --- | --- |
| `compiler_info` | Check ARMCC5, armlink, and fromelf availability and compiler version |
| `compile_c` | Compile one Cortex-M3 C translation unit and return output diagnostics plus an artifact ID and SHA-256 |
| `inspect_object` | Inspect a generated object or AXF using `fromelf` (`code`, `symbols`, `relocations`, `sections`, `all`) |
| `link_probe` | Link a small experiment using `armlink`; subject to the installed compiler's license and link-size limitations |
| `get_artifact` | Return generated binary artifacts in base64-encoded chunks with offsets |

Object IDs persist in memory until the worker process exits. Generated artifacts live in a temporary directory and are removed on worker exit. This version has no persistent job queue, file sharing, or arbitrary remote command execution.

## Tests

From the repository root, on Linux or Windows:

```bash
python -m unittest discover -s tests -v
python -m py_compile server.py worker_core.py
```

The tests use fake compiler executables, so they verify the orchestration logic but **do not** establish real ARMCC5/Keil behavior or an end-to-end Windows-to-Pi connection. The MCP transport itself requires `pip install -r requirements.txt`.

## Why two toolchains?

On **Oberon**, ATfE 21.1.1 (Clang/LLD) builds the complete firmware. On **x1-carbon**, the historical ARMCC5 compiler compiles small test cases to compare generated code and ABI details with the original proprietary libraries. The ARMCC linker is used only for license-permitted small test programs, not for building the full firmware.

Some Keil Lite/evaluation configurations emit objects that `ld.lld` cannot consume. Transfer the generated object to Oberon and test its ELF format before attempting mixed-toolchain linking; a successful `armcc -c` does not guarantee LLD compatibility.

## Trusted-LAN deployment

This server intentionally has **no authentication or encryption**. It accepts user-supplied C source and selected compiler flags and runs compiler subprocesses. Keep port 8765 off public or untrusted networks. A small flag and file-size validation exists to prevent accidental misuse, not to provide a secure execution sandbox. The MCP SDK's DNS rebinding protection is explicitly disabled for LAN hostname access.

`mcp>=2.1,<3` targets the MCP Python SDK v2 API (`MCPServer`, transport configuration on `run()`). If you use a different SDK major version, adjust the imports/transport setup accordingly.

## Repository integration

Canonical source: [civcode/armcc5-mcp-worker](https://github.com/civcode/armcc5-mcp-worker).

The `ATfE-port` branch of [civcode/STM32F103-BU0x_SDK](https://github.com/civcode/STM32F103-BU0x_SDK/tree/ATfE-port) references this independent worker with a pinned Git submodule. See [PUBLISHING.md](PUBLISHING.md) for cloning and deployment instructions. There are no GitHub Actions workflows in the worker repository.
