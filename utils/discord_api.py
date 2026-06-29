from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import discord

T = TypeVar("T")


def http_retry_after(exc: discord.HTTPException, attempt: int) -> float:
    """Seconds to wait before retrying a Discord HTTP call."""
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        return max(float(retry_after), 1.0)
    return min(600.0, 5.0 * (2**attempt))


class OutboundGate:
    """Spaces outbound Discord HTTP calls to reduce global rate-limit bursts."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = max(0.0, min_interval)
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def wait(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._next_at - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_at = time.monotonic() + self._min_interval


async def run_with_discord_retry(
    factory: Callable[[], Awaitable[T]],
    *,
    gate: OutboundGate | None = None,
    max_attempts: int = 5,
) -> T | None:
    for attempt in range(max_attempts):
        if gate is not None:
            await gate.wait()
        try:
            return await factory()
        except discord.HTTPException as exc:
            if exc.status != 429 or attempt >= max_attempts - 1:
                raise
            delay = http_retry_after(exc, attempt)
            logging.warning(
                "Discord HTTP 429; backing off %.1fs (attempt %s/%s)",
                delay,
                attempt + 1,
                max_attempts,
            )
            await asyncio.sleep(delay)
    return None


async def safe_channel_send(
    channel: discord.abc.Messageable,
    *args: Any,
    gate: OutboundGate | None = None,
    **kwargs: Any,
) -> discord.Message | None:
    try:
        return await run_with_discord_retry(
            lambda: channel.send(*args, **kwargs),
            gate=gate,
        )
    except discord.HTTPException as exc:
        if exc.status == 429:
            logging.warning("Dropped channel message after rate limit: %s", channel)
            return None
        raise


async def safe_interaction_send(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    ephemeral: bool = True,
    embed: discord.Embed | None = None,
    allowed_mentions: discord.AllowedMentions | None = None,
    gate: OutboundGate | None = None,
) -> bool:
    """Send an interaction response or followup; return False if rate-limited."""

    async def _send() -> None:
        kwargs: dict[str, Any] = {"ephemeral": ephemeral}
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        if allowed_mentions is not None:
            kwargs["allowed_mentions"] = allowed_mentions
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)

    try:
        await run_with_discord_retry(_send, gate=gate, max_attempts=4)
        return True
    except discord.NotFound:
        logging.warning(
            "Could not respond to interaction %s (interaction expired or unknown)",
            getattr(interaction.command, "name", "?"),
        )
        return False
    except discord.HTTPException as exc:
        if exc.status == 429:
            logging.warning(
                "Could not respond to interaction %s (rate limited)",
                getattr(interaction.command, "name", "?"),
            )
            return False
        if exc.status in {400, 404}:
            logging.warning(
                "Could not respond to interaction %s (HTTP %s)",
                getattr(interaction.command, "name", "?"),
                exc.status,
            )
            return False
        raise
