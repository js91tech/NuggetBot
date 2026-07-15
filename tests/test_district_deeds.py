"""Exclusive district deed ownership, buyouts, tenant mult, and rent."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import config
from database import Database
from items import get_item
from utils.combat_engine import apply_armor_mitigation, roll_player_damage
from utils.districts import (
    buyout_payout,
    deed_claim_cost,
    effective_district_mult,
)


class DistrictDeedHelperTests(unittest.TestCase):
    def test_claim_costs_scale_by_district(self) -> None:
        self.assertEqual(deed_claim_cost("residential"), 25_000_000.0)
        self.assertEqual(deed_claim_cost("industrial"), 32_500_000.0)

    def test_tenant_mult_is_half_bonus(self) -> None:
        self.assertAlmostEqual(effective_district_mult("industrial", is_owner=True), 1.30)
        self.assertAlmostEqual(effective_district_mult("industrial", is_owner=False), 1.15)

    def test_buyout_burn_split(self) -> None:
        owner, burn, pays = buyout_payout(1_000.0)
        self.assertAlmostEqual(owner, 1_000.0 * 24 * 5)
        self.assertAlmostEqual(burn, owner * 0.15)
        self.assertAlmostEqual(pays, owner + burn)


class DistrictDeedDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.sqlite3"))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 42
        self.owner = 1001
        self.tenant = 1002
        self.buyer = 1003
        for uid in (self.owner, self.tenant, self.buyer):
            await self.db.ensure_user(uid, self.guild_id)
            await self.db.conn.execute(
                "UPDATE users SET wallet = 500000000 WHERE user_id = ? AND guild_id = ?",
                (uid, self.guild_id),
            )
        await self.db.conn.commit()
        for uid in (self.owner, self.tenant, self.buyer):
            err = await self.db.create_business(uid, self.guild_id)
            self.assertIsNone(err)
            await self.db.conn.execute(
                """
                UPDATE user_businesses
                SET tier = 7, tier_id = 'corporation', district_id = 'industrial'
                WHERE user_id = ? AND guild_id = ?
                """,
                (uid, self.guild_id),
            )
        await self.db.conn.commit()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_claim_and_double_claim(self) -> None:
        cost, err = await self.db.claim_district_deed(self.owner, self.guild_id, "industrial")
        self.assertIsNone(err)
        self.assertEqual(cost, deed_claim_cost("industrial"))
        deed = await self.db.get_district_deed(self.guild_id, "industrial")
        self.assertIsNotNone(deed)
        self.assertEqual(int(deed["owner_user_id"]), self.owner)
        _, err2 = await self.db.claim_district_deed(self.tenant, self.guild_id, "industrial")
        self.assertEqual(err2, "already_owned")

    async def test_buyout_pays_owner_and_burns(self) -> None:
        await self.db.claim_district_deed(self.owner, self.guild_id, "industrial")
        # Freeze accrual so settle-during-buyout does not mix rent into balances.
        now = time.time()
        for uid in (self.owner, self.buyer, self.tenant):
            await self.db.conn.execute(
                """
                UPDATE user_businesses
                SET stored_income = 0, last_income_at = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (now, uid, self.guild_id),
            )
        await self.db.conn.commit()
        owner_before = await self.db.get_balance(self.owner, self.guild_id)
        buyer_before = await self.db.get_balance(self.buyer, self.guild_id)
        paid, received, burned, err = await self.db.buyout_district_deed(
            self.buyer, self.guild_id, "industrial",
        )
        self.assertIsNone(err)
        self.assertGreater(paid, 0)
        self.assertAlmostEqual(paid, received + burned)
        self.assertAlmostEqual(burned, received * config.DISTRICT_BUYOUT_BURN)
        owner_after = await self.db.get_balance(self.owner, self.guild_id)
        buyer_after = await self.db.get_balance(self.buyer, self.guild_id)
        self.assertAlmostEqual(owner_after - owner_before, received, delta=1.0)
        self.assertAlmostEqual(buyer_before - buyer_after, paid, delta=1.0)
        deed = await self.db.get_district_deed(self.guild_id, "industrial")
        self.assertEqual(int(deed["owner_user_id"]), self.buyer)

    async def test_tenant_rent_on_accrual(self) -> None:
        await self.db.claim_district_deed(self.owner, self.guild_id, "industrial")
        owner_before = await self.db.get_balance(self.owner, self.guild_id)
        now = time.time()
        await self.db.conn.execute(
            """
            UPDATE user_businesses
            SET stored_income = 0, last_income_at = ?
            WHERE user_id = ? AND guild_id = ?
            """,
            (now - 3600, self.tenant, self.guild_id),
        )
        await self.db.conn.commit()
        row = await self.db._settle_business_income_no_lock(
            self.tenant, self.guild_id, now=now,
        )
        self.assertIsNotNone(row)
        stored = float(row["stored_income"])
        self.assertGreater(stored, 0)
        owner_after = await self.db.get_balance(self.owner, self.guild_id)
        rent = owner_after - owner_before
        self.assertGreater(rent, 0)
        # Rent is 20% of gross accrual; tenant kept the rest.
        self.assertAlmostEqual(rent / (stored + rent), config.DISTRICT_TENANT_RENT_RATE, places=5)


class SilentPowerAndPriceTests(unittest.TestCase):
    def test_key_prices(self) -> None:
        self.assertEqual(get_item("jail_key").price, 25_000_000)
        self.assertEqual(get_item("pick_key").price, 4_500_000)

    def test_silent_power_helpers(self) -> None:
        from utils.classes import silent_power_damage_mult, silent_power_defense_mult
        from utils.combat_engine import AttackContext

        self.assertEqual(silent_power_damage_mult(config.SILENT_POWER_USER_ID), 1.15)
        self.assertEqual(silent_power_defense_mult(config.SILENT_POWER_USER_ID), 1.15)
        self.assertEqual(silent_power_damage_mult(1), 1.0)
        # Force non-crit ceiling comparison via damage_mult alone.
        ctx = AttackContext(damage_mult=1.0, extra_crit=-1.0)
        plain = [
            roll_player_damage(None, ctx=ctx)[0]
            for _ in range(30)
        ]
        buffed = [
            roll_player_damage(
                None, ctx=ctx, attacker_id=config.SILENT_POWER_USER_ID,
            )[0]
            for _ in range(30)
        ]
        self.assertLessEqual(max(plain), int(config.BOSS_UNARMED_MAX))
        self.assertGreaterEqual(
            max(buffed),
            int(config.BOSS_UNARMED_MAX * config.SILENT_POWER_DAMAGE_MULT),
        )

    def test_silent_power_defense(self) -> None:
        class FakeArmor:
            power = 100
            hp_bonus = 0

        dmg_plain, mit_plain = apply_armor_mitigation(1000, FakeArmor())  # type: ignore[arg-type]
        dmg_buff, mit_buff = apply_armor_mitigation(
            1000, FakeArmor(), defender_id=config.SILENT_POWER_USER_ID,  # type: ignore[arg-type]
        )
        self.assertGreater(mit_buff, mit_plain)
        self.assertLess(dmg_buff, dmg_plain)


class SpyInventoryHelpersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.sqlite3"))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 7
        self.uid = 99
        await self.db.ensure_user(self.uid, self.guild_id)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_grant_and_remove_quantity(self) -> None:
        granted = await self.db.grant_inventory_quantity(
            self.uid, self.guild_id, "jail_key", 3,
        )
        self.assertEqual(granted, 3)
        qty = await self.db.get_inventory_quantity(self.uid, self.guild_id, "jail_key")
        self.assertEqual(qty, 3)
        removed = await self.db.remove_inventory_quantity(
            self.uid, self.guild_id, "jail_key", 2,
        )
        self.assertEqual(removed, 2)
        qty = await self.db.get_inventory_quantity(self.uid, self.guild_id, "jail_key")
        self.assertEqual(qty, 1)


if __name__ == "__main__":
    unittest.main()
