"""Ephemeral per-session page index (in-process; resets on worker restart)."""

from __future__ import annotations

import asyncio
from collections import defaultdict


_locks: defaultdict[str, asyncio.Lock] = defaultdict(lambda: asyncio.Lock())
_pages: dict[str, int] = {}


def _key(session_id: str) -> str:
    return str(session_id)


async def get_current_page(session_id: str) -> int:
    return _pages.get(_key(session_id), 1)


async def set_current_page(session_id: str, page: int) -> None:
    sid = _key(session_id)
    async with _locks[sid]:
        _pages[sid] = page


async def adjust_page(session_id: str, delta: int, total_pages: int) -> tuple[int, bool]:
    """Apply delta and clamp to [1, total_pages]. Returns (new_page, changed)."""
    sid = _key(session_id)
    async with _locks[sid]:
        current = _pages.get(sid, 1)
        new_page = max(1, min(total_pages, current + delta))
        changed = new_page != current
        _pages[sid] = new_page
        return new_page, changed


async def reset_page(session_id: str) -> None:
    sid = _key(session_id)
    async with _locks[sid]:
        _pages[sid] = 1


def get_current_page_sync(session_id: str) -> int:
    """Read last known page for HTTP views (best-effort, same worker only)."""
    return _pages.get(_key(session_id), 1)
