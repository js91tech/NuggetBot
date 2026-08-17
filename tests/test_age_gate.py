"""Tests for GoonBot 18+ age gate and NSFW channel policy helpers."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from utils import age_gate
from utils.goon_theme import BOT_DISPLAY_NAME, brand_color


class AgeGateHelpersTests(unittest.IsolatedAsyncioTestCase):
    async def test_is_age_verified_delegates_to_db(self) -> None:
        db = MagicMock()
        db.get_age_verified = AsyncMock(return_value=True)
        self.assertTrue(await age_gate.is_age_verified(db, 1, 2))
        db.get_age_verified.assert_awaited_once_with(1, 2)

    async def test_nsfw_channel_required_defaults_true_on_missing_setting(self) -> None:
        db = MagicMock()
        db.get_config_value = AsyncMock(side_effect=KeyError("nsfw_channel_only"))
        self.assertTrue(await age_gate.nsfw_channel_required(db, 99))

    async def test_nsfw_channel_required_respects_zero(self) -> None:
        db = MagicMock()
        db.get_config_value = AsyncMock(return_value=0.0)
        self.assertFalse(await age_gate.nsfw_channel_required(db, 99))

    def test_channel_is_nsfw_flag(self) -> None:
        self.assertTrue(age_gate.channel_is_nsfw(SimpleNamespace(nsfw=True)))
        self.assertFalse(age_gate.channel_is_nsfw(SimpleNamespace(nsfw=False)))

    async def test_check_blocks_dm(self) -> None:
        db = MagicMock()
        interaction = MagicMock()
        interaction.guild_id = None
        interaction.response.send_message = AsyncMock()
        ok = await age_gate.check_interaction(interaction, db)
        self.assertFalse(ok)
        interaction.response.send_message.assert_awaited()

    async def test_check_allows_components(self) -> None:
        db = MagicMock()
        interaction = MagicMock()
        interaction.guild_id = 1
        interaction.type = discord.InteractionType.component
        ok = await age_gate.check_interaction(interaction, db)
        self.assertTrue(ok)

    async def test_check_blocks_non_nsfw_when_required(self) -> None:
        db = MagicMock()
        db.get_config_value = AsyncMock(return_value=1.0)
        interaction = MagicMock()
        interaction.guild_id = 10
        interaction.type = discord.InteractionType.application_command
        interaction.channel = SimpleNamespace(nsfw=False, parent=None)
        interaction.user = SimpleNamespace(
            id=5,
            guild_permissions=SimpleNamespace(administrator=False),
        )
        interaction.response.send_message = AsyncMock()
        ok = await age_gate.check_interaction(interaction, db)
        self.assertFalse(ok)
        args, kwargs = interaction.response.send_message.await_args
        self.assertIn("NSFW", args[0])

    async def test_check_prompts_age_gate_when_unverified(self) -> None:
        db = MagicMock()
        db.get_config_value = AsyncMock(return_value=0.0)
        db.get_age_verified = AsyncMock(return_value=False)
        interaction = MagicMock()
        interaction.guild_id = 10
        interaction.type = discord.InteractionType.application_command
        interaction.channel = SimpleNamespace(nsfw=True)
        interaction.user = SimpleNamespace(id=7)
        interaction.response.send_message = AsyncMock()
        ok = await age_gate.check_interaction(interaction, db)
        self.assertFalse(ok)
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(kwargs.get("ephemeral"))
        self.assertIsNotNone(kwargs.get("view"))
        self.assertIn("18+", kwargs["embed"].title)

    async def test_check_allows_verified_user(self) -> None:
        db = MagicMock()
        db.get_config_value = AsyncMock(return_value=0.0)
        db.get_age_verified = AsyncMock(return_value=True)
        interaction = MagicMock()
        interaction.guild_id = 10
        interaction.type = discord.InteractionType.application_command
        interaction.channel = SimpleNamespace(nsfw=True)
        interaction.user = SimpleNamespace(id=7)
        ok = await age_gate.check_interaction(interaction, db)
        self.assertTrue(ok)

    def test_theme_brand(self) -> None:
        self.assertEqual(BOT_DISPLAY_NAME, "GoonBot")
        self.assertEqual(age_gate.age_gate_embed().color, brand_color())


if __name__ == "__main__":
    unittest.main()
