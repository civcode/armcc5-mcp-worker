# Repository and deployment

Canonical worker source: <https://github.com/civcode/armcc5-mcp-worker> (`main`).

This repository contains only the Windows-hosted plain-HTTP MCP worker. Its source is intentionally separate from the firmware SDK and has **no GitHub Actions workflows**.

## On x1-carbon (Windows)

```bat
git clone https://github.com/civcode/armcc5-mcp-worker.git
cd armcc5-mcp-worker
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
start.cmd
```

## On Oberon (Linux)

```bash
pi mcp add armcc5 --url http://x1-carbon:8765/mcp
pi mcp list
```

Adjust the host name if needed; see [README.md](README.md) for configuration and tooling.

## SDK integration

The SDK branch `civcode/STM32F103-BU0x_SDK@ATfE-port` integrates this tool as a pinned Git submodule under `tools/armcc5-mcp-worker/` and documents the workflow in `docs/ARMCC5_MCP_WORKER.md`. Clone with `--recurse-submodules` when you want the worker source inside the SDK checkout. The worker is **only started manually on Windows**, not invoked by the ATfE build or by CI.
