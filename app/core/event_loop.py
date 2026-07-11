# app/core/event_loop.py

"""
Asyncio event loop helper for Windows + psycopg 3 compatibility.

Usage in any test script or standalone script:

    from app.core.event_loop import run_async

    async def my_function():
        ...

    run_async(my_function())
"""

import asyncio
import selectors
import sys


def run_async(coro):
    """
    Run an async coroutine with proper event loop for Windows.

    Replaces: asyncio.run(coro)
    Works on Windows (psycopg 3 needs SelectorEventLoop).
    Works on Linux/macOS (uses default loop).
    No deprecation warnings.
    """
    if sys.platform == "win32":
        loop_factory = lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        return asyncio.run(coro, loop_factory=loop_factory)
    return asyncio.run(coro)
