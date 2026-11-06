"""Graceful MCP server lifecycle without overriding Uvicorn's signal handlers.

Uvicorn owns SIGINT (Ctrl+C) and SIGTERM while serving. Once MCPServer.run()
returns, release worker-owned resources explicitly. Some MCP/ASGI versions
propagate KeyboardInterrupt instead of returning; support either behavior.
"""
from __future__ import annotations

import sys
from typing import Callable


def run_with_cleanup(run_server: Callable[[], None],
                     close_worker: Callable[[], None]) -> None:
    """Drain the HTTP server, then release temporary compiler resources.

    On Windows, let Uvicorn handle console Ctrl+C rather than installing a
    competing Python SIGINT handler. The worker also registers an atexit
    fallback for ordinary interpreter exits.
    """
    try:
        run_server()
    except KeyboardInterrupt:
        # Uvicorn often handles Ctrl+C itself; other transports may propagate.
        pass
    finally:
        print("Shutting down ARMCC5 MCP worker...", file=sys.stderr, flush=True)
        close_worker()
        print("ARMCC5 MCP worker stopped.", file=sys.stderr, flush=True)
