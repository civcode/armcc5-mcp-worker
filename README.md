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

For example, the working installation on **x1-carbon** is at `C:\DevTools\KeilAC5\ARM\arm_compiler_5.06\bin`. From the worker checkout in the **same Command Prompt**:

```bat
C:
cd \workspace\armcc5-mcp-worker
set "ARMCC_ROOT=C:\DevTools\KeilAC5\ARM\arm_compiler_5.06"
"%ARMCC_ROOT%\bin\armcc.exe" --vsn
```

Example of the version output observed on x1-carbon:

```text
Product: MDK-ARM Lite 5.43
Component: ARM Compiler 5.06 update 7 (build 960)
Tool: armcc [4d365
```

This confirms that `armcc.exe` is accessible and reports **5.06 update 7 (build 960)**. The original firmware's vendor libraries were built with **5.06 update 6 (build 750)**, so record the actual compiler version with each reverse-engineering experiment; u7 is useful for experiments but should not be assumed to generate byte-identical u6 code.

After verifying the path, install Python dependencies if needed and start the MCP endpoint **in that same Command Prompt**:

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
start.cmd
```

The `ARMCC_ROOT` setting made with `set` is temporary and applies only to that Command Prompt. Set it again in new sessions, or set it permanently through your Windows environment configuration. To check the resolved paths from Oberon, call the MCP `compiler_info` tool: it reports the compiler root and availability of `armcc.exe`, `armlink.exe`, and `fromelf.exe`.

**Note:** `armcc.exe --vsn` checks compiler availability, not whether a test program will compile/link under the MDK-ARM Lite license.

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

The tests use **Python subprocess mocks**, not real compiler binaries. They create empty `.exe` placeholders only to verify availability checks, then simulate `armcc`, `fromelf`, and `armlink` execution in Python; this makes the suite portable to **Windows and Linux**. They verify the orchestration logic but **do not** establish real ARMCC5/Keil behavior or an end-to-end Windows-to-Pi connection. The MCP transport itself requires `pip install -r requirements.txt`.

**Windows troubleshooting:** Older test revisions wrote Unix `#!` Python scripts with an `.exe` extension and attempted to execute them. Windows then displayed an **Unsupported 16-Bit Application** dialog for a path resembling `%TEMP%\\tmp...\\bin\\armcc.exe`. That path is a *test mock*, not the ARM compiler in `C:\\DevTools\\KeilAC5\\ARM\\arm_compiler_5.06\\bin`. Update the checkout (`git pull`) to get the corrected tests; no compiler reinstall is needed.

## Why two toolchains?

On **Oberon**, ATfE 21.1.1 (Clang/LLD) builds the complete firmware. On **x1-carbon**, the historical ARMCC5 compiler compiles small test cases to compare generated code and ABI details with the original proprietary libraries. The ARMCC linker is used only for license-permitted small test programs, not for building the full firmware.

Some Keil Lite/evaluation configurations emit objects that `ld.lld` cannot consume. Transfer the generated object to Oberon and test its ELF format before attempting mixed-toolchain linking; a successful `armcc -c` does not guarantee LLD compatibility.

## Trusted-LAN deployment

This server intentionally has **no authentication or encryption**. It accepts user-supplied C source and selected compiler flags and runs compiler subprocesses. Keep port 8765 off public or untrusted networks. A small flag and file-size validation exists to prevent accidental misuse, not to provide a secure execution sandbox. The MCP SDK's DNS rebinding protection is explicitly disabled for LAN hostname access.

`mcp>=2.1,<3` targets the MCP Python SDK v2 API (`MCPServer`, transport configuration on `run()`). If you use a different SDK major version, adjust the imports/transport setup accordingly.

## Repository integration

Canonical source: [civcode/armcc5-mcp-worker](https://github.com/civcode/armcc5-mcp-worker).

The `ATfE-port` branch of [civcode/STM32F103-BU0x_SDK](https://github.com/civcode/STM32F103-BU0x_SDK/tree/ATfE-port) references this independent worker with a pinned Git submodule. See [PUBLISHING.md](PUBLISHING.md) for cloning and deployment instructions. There are no GitHub Actions workflows in the worker repository.
