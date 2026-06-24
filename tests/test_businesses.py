"""Business Empire: income math and database create/collect/upgrade flows."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import config
from database import Database
from utils.business_competition import (
    bonus_to_multiplier,
    effective_penalty,
    penalty_to_multiplier,
    security_mitigation,
)
from utils.businesses import (
    BUSINESS_TIERS,
    accrue_income,
    capacity_for_level,
    hourly_income,
    hourly_income_from_row,
    next_tier_def,
    tier_def,
    tier_def_by_id,
    upgrade_cost,
    upgrade_income_delta,
)
from utils.districts import DISTRICT_MAP, district_income_mult
from utils.drugs import DRUGS, drug_by_id, roll_yield
from utils.mega_projects import MEGA_PROJECTS, income_bonus_from_completed
from utils.stock_market import sell_proceeds, share_price


class BusinessMathTests(unittest.TestCase):
    def test_seven_tiers_increasing(self) -> None:
        self.assertEqual(len(BUSINESS_TIERS), 7)
        costs = [t.purchase_cost for t in BUSINESS_TIERS]
        incomes = [t.base_income_per_hour for t in BUSINESS_TIERS]
        self.assertEqual(costs, sorted(costs))
        self.assertEqual(incomes, sorted(incomes))

    def test_tier_lookup(self) -> None:
        self.assertEqual(tier_def(1).tier_id, "lemon_stand")
        self.assertEqual(tier_def_by_id("corporation").tier, 7)
        self.assertIsNone(tier_def(99))
        self.assertIsNone(next_tier_def(7))
        self.assertEqual(next_tier_def(1).tier, 2)

    def test_hourly_scales_with_upgrades(self) -> None:
        base = hourly_income(tier=1)
        self.assertAlmostEqual(base, 20.0)
        boosted = hourly_income(tier=1, efficiency_level=5, reputation_level=5)
        self.assertGreater(boosted, base)

    def test_growth_branch_boosts_income(self) -> None:
        base = hourly_income(tier=4)
        grown = hourly_income(tier=4, growth_branch_level=3)
        self.assertGreater(grown, base)

    def test_production_branch_boosts_income(self) -> None:
        base = hourly_income(tier=4)
        produced = hourly_income(tier=4, production_branch_level=3)
        self.assertGreater(produced, base)

    def test_satisfaction_swing(self) -> None:
        low = hourly_income(tier=3, satisfaction=0)
        neutral = hourly_income(tier=3, satisfaction=50)
        high = hourly_income(tier=3, satisfaction=100)
        self.assertLess(low, neutral)
        self.assertGreater(high, neutral)

    def test_accrue_caps_at_capacity(self) -> None:
        cap = capacity_for_level(1, 0)
        result = accrue_income(stored=0.0, capacity=cap, hourly=1_000_000.0, elapsed_seconds=99999.0)
        self.assertLessEqual(result, cap)

    def test_accrue_adds_partial(self) -> None:
        result = accrue_income(stored=0.0, capacity=10_000.0, hourly=3600.0, elapsed_seconds=3600.0)
        self.assertAlmostEqual(result, 3600.0, places=2)

    def test_upgrade_cost_grows(self) -> None:
        c0 = upgrade_cost(1, 0)
        c1 = upgrade_cost(1, 1)
        self.assertGreater(c1, c0)


class BusinessDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def test_create_requires_funds(self) -> None:
        guild_id, uid = 1, 100
        await self.db.ensure_user(uid, guild_id)
        err = await self.db.create_business(uid, guild_id)
        self.assertEqual(err, "insufficient_funds")

    async def test_create_debits_and_persists(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 1_000.0, apply_bonuses=False)
        err = await self.db.create_business(uid, guild_id)
        self.assertIsNone(err)
        self.assertAlmostEqual(await self.db.get_balance(uid, guild_id), 500.0)
        row = await self.db.get_business(uid, guild_id)
        self.assertIsNotNone(row)
        self.assertEqual(int(row["tier"]), 1)

    async def test_create_twice_blocked(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 2_000.0, apply_bonuses=False)
        self.assertIsNone(await self.db.create_business(uid, guild_id))
        self.assertEqual(await self.db.create_business(uid, guild_id), "already_owns")

    async def test_collect_moves_income_to_wallet(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 1_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        # Force stored income by backdating last_income_at by an hour.
        async with self.db._write_lock:
            await self.db.conn.execute(
                "UPDATE user_businesses SET last_income_at = last_income_at - 3600 "
                "WHERE user_id = ? AND guild_id = ?",
                (uid, guild_id),
            )
            await self.db.conn.commit()
        before = await self.db.get_balance(uid, guild_id)
        amount, err = await self.db.collect_business_income(uid, guild_id)
        self.assertIsNone(err)
        self.assertGreater(amount, 0)
        after = await self.db.get_balance(uid, guild_id)
        self.assertAlmostEqual(after, before + amount, places=2)

    async def test_collect_without_business(self) -> None:
        guild_id, uid = 1, 100
        amount, err = await self.db.collect_business_income(uid, guild_id)
        self.assertEqual(err, "no_business")
        self.assertEqual(amount, 0.0)

    async def test_collect_immediately_is_negligible(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 1_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        amount, err = await self.db.collect_business_income(uid, guild_id)
        # A freshly created business has only milliseconds of accrued income.
        self.assertTrue(err is None or err == "empty")
        self.assertLess(amount, 1.0)

    async def test_tier_up(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 10_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        err, new_tier = await self.db.tier_up_business(uid, guild_id)
        self.assertIsNone(err)
        self.assertEqual(new_tier, 2)
        row = await self.db.get_business(uid, guild_id)
        self.assertEqual(str(row["tier_id"]), "food_cart")

    async def test_tier_up_insufficient(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 600.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        err, _ = await self.db.tier_up_business(uid, guild_id)
        self.assertEqual(err, "insufficient_funds")

    async def test_upgrade_attribute(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 5_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        row = await self.db.get_business(uid, guild_id)
        before = self.db._business_hourly_from_row(row)
        cost, err = await self.db.upgrade_business_attribute(uid, guild_id, "efficiency")
        self.assertIsNone(err)
        self.assertGreater(cost, 0)
        row = await self.db.get_business(uid, guild_id)
        self.assertEqual(int(row["efficiency"]), 1)
        after = self.db._business_hourly_from_row(row)
        self.assertGreater(after, before)

    async def test_upgrade_reputation_increases_hourly(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 5_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        row = await self.db.get_business(uid, guild_id)
        delta = upgrade_income_delta(row, "reputation")
        self.assertIsNotNone(delta)
        self.assertGreater(delta, 0)
        _, err = await self.db.upgrade_business_attribute(uid, guild_id, "reputation")
        self.assertIsNone(err)
        row = await self.db.get_business(uid, guild_id)
        self.assertAlmostEqual(
            self.db._business_hourly_from_row(row),
            hourly_income_from_row(row),
        )

    async def test_security_upgrade_does_not_change_hourly(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 5_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        before = self.db._business_hourly_from_row(await self.db.get_business(uid, guild_id))
        _, err = await self.db.upgrade_business_attribute(uid, guild_id, "security")
        self.assertIsNone(err)
        after = self.db._business_hourly_from_row(await self.db.get_business(uid, guild_id))
        self.assertEqual(after, before)

    async def test_upgrade_branch_growth(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 5_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        _, err = await self.db.upgrade_business_attribute(uid, guild_id, "branch_growth")
        self.assertIsNone(err)
        row = await self.db.get_business(uid, guild_id)
        self.assertEqual(int(row["branch_growth"]), 1)

    async def test_branch_cap_enforced(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 5_000_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        for _ in range(config.BUSINESS_BRANCH_MAX):
            _, err = await self.db.upgrade_business_attribute(uid, guild_id, "branch_production")
            self.assertIsNone(err)
        _, err = await self.db.upgrade_business_attribute(uid, guild_id, "branch_production")
        self.assertEqual(err, "max_level")

    async def test_upgrade_invalid_attribute(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 5_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        _, err = await self.db.upgrade_business_attribute(uid, guild_id, "nope")
        self.assertEqual(err, "invalid_attribute")


class DistrictTests(unittest.TestCase):
    def test_five_districts(self) -> None:
        self.assertEqual(len(DISTRICT_MAP), 5)
        for defn in DISTRICT_MAP.values():
            self.assertGreaterEqual(defn.income_mult, 1.0)

    def test_income_mult_lookup(self) -> None:
        self.assertEqual(district_income_mult(None), 1.0)
        self.assertEqual(district_income_mult("nope"), 1.0)
        self.assertEqual(district_income_mult("financial"), DISTRICT_MAP["financial"].income_mult)


class BusinessDistrictDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def test_relocate_applies_income_bonus(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 5_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        cost, err = await self.db.relocate_business(uid, guild_id, "financial")
        self.assertIsNone(err)
        self.assertGreater(cost, 0)
        row = await self.db.get_business(uid, guild_id)
        self.assertEqual(str(row["district_id"]), "financial")
        hourly = self.db._business_hourly_from_row(row)
        base = hourly_income(tier=1)
        self.assertAlmostEqual(hourly, base * DISTRICT_MAP["financial"].income_mult, places=4)

    async def test_relocate_same_district_blocked(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 5_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        await self.db.relocate_business(uid, guild_id, "downtown")
        _, err = await self.db.relocate_business(uid, guild_id, "downtown")
        self.assertEqual(err, "already_here")

    async def test_relocate_invalid_district(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 5_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        _, err = await self.db.relocate_business(uid, guild_id, "atlantis")
        self.assertEqual(err, "invalid_district")

    async def test_expand_influence(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 50_000.0, apply_bonuses=False)
        cost, new_inf, err = await self.db.expand_district_influence(uid, guild_id, "downtown", 10)
        self.assertIsNone(err)
        self.assertGreater(cost, 0)
        self.assertEqual(new_inf, 10.0)
        ranking = await self.db.list_district_influence(guild_id, "downtown")
        self.assertEqual(ranking[0][1], str(uid))

    async def test_influence_capped(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 10_000_000.0, apply_bonuses=False)
        _, new_inf, _ = await self.db.expand_district_influence(uid, guild_id, "downtown", 999)
        self.assertLessEqual(new_inf, float(config.BUSINESS_DISTRICT_INFLUENCE_MAX))


class CompetitionMathTests(unittest.TestCase):
    def test_security_mitigation_increases_with_rating(self) -> None:
        self.assertLess(security_mitigation(0), security_mitigation(50))
        self.assertLess(security_mitigation(50), security_mitigation(500))

    def test_effective_penalty_reduced_by_security(self) -> None:
        base = 0.10
        self.assertEqual(effective_penalty(base, 0), base)
        self.assertLess(effective_penalty(base, 200), base)

    def test_multiplier_conversions(self) -> None:
        self.assertAlmostEqual(penalty_to_multiplier(0.10), 0.90)
        self.assertAlmostEqual(bonus_to_multiplier(0.10), 1.10)


class BusinessCompetitionDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def _setup_business(self, uid: int, guild_id: int, funds: float) -> None:
        await self.db.credit_wallet(uid, guild_id, funds + 500.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)

    async def test_self_buff_applies(self) -> None:
        guild_id, uid = 1, 100
        await self._setup_business(uid, guild_id, 20_000.0)
        result = await self.db.perform_business_action(uid, guild_id, "marketing_campaign")
        self.assertIsNone(result["error"])
        buffs = await self.db.list_active_business_buffs(uid, guild_id)
        self.assertEqual(len(buffs), 1)
        self.assertGreater(float(buffs[0]["multiplier"]), 1.0)

    async def test_action_cooldown(self) -> None:
        guild_id, uid = 1, 100
        await self._setup_business(uid, guild_id, 50_000.0)
        await self.db.perform_business_action(uid, guild_id, "marketing_campaign")
        again = await self.db.perform_business_action(uid, guild_id, "marketing_campaign")
        self.assertEqual(again["error"], "cooldown")

    async def test_attack_and_defend(self) -> None:
        guild_id, attacker, defender = 1, 100, 200
        await self._setup_business(attacker, guild_id, 20_000.0)
        await self._setup_business(defender, guild_id, 5_000.0)
        result = await self.db.perform_business_action(
            attacker, guild_id, "price_war", target_id=defender,
        )
        self.assertIsNone(result["error"])
        self.assertEqual(result["kind"], "attack")
        buffs = await self.db.list_active_business_buffs(defender, guild_id)
        self.assertEqual(len(buffs), 1)
        penalty_before = 1.0 - float(buffs[0]["multiplier"])
        self.assertGreater(penalty_before, 0)

        defend = await self.db.defend_business(defender, guild_id)
        self.assertIsNone(defend["error"])
        buffs_after = await self.db.list_active_business_buffs(defender, guild_id)
        penalty_after = 1.0 - float(buffs_after[0]["multiplier"])
        self.assertLess(penalty_after, penalty_before)

    async def test_attack_requires_target_business(self) -> None:
        guild_id, attacker = 1, 100
        await self._setup_business(attacker, guild_id, 20_000.0)
        result = await self.db.perform_business_action(
            attacker, guild_id, "price_war", target_id=999,
        )
        self.assertEqual(result["error"], "target_no_business")

    async def test_defend_without_attack(self) -> None:
        guild_id, uid = 1, 100
        await self._setup_business(uid, guild_id, 5_000.0)
        result = await self.db.defend_business(uid, guild_id)
        self.assertEqual(result["error"], "no_attack")

    async def test_market_expansion_requires_district(self) -> None:
        guild_id, uid = 1, 100
        await self._setup_business(uid, guild_id, 20_000.0)
        result = await self.db.perform_business_action(uid, guild_id, "market_expansion")
        self.assertEqual(result["error"], "no_district")

    async def test_market_expansion_grants_influence(self) -> None:
        guild_id, uid = 1, 100
        await self._setup_business(uid, guild_id, 20_000.0)
        await self.db.relocate_business(uid, guild_id, "downtown")
        result = await self.db.perform_business_action(uid, guild_id, "market_expansion")
        self.assertIsNone(result["error"])
        self.assertGreater(float(result["influence"]), 0)

    async def test_market_expansion_territory_bonus(self) -> None:
        guild_id, uid = 1, 100
        await self._setup_business(uid, guild_id, 300_000.0)
        await self.db.join_crew(uid, guild_id, "Acme")
        await self.db.deposit_crew_treasury(uid, guild_id, 200_000.0)
        await self.db.buy_corporate_upgrade(uid, guild_id, "territory")
        await self.db.relocate_business(uid, guild_id, "downtown")
        before = await self.db.get_user_district_influence(uid, guild_id, "downtown")
        result = await self.db.perform_business_action(uid, guild_id, "market_expansion")
        self.assertIsNone(result["error"])
        after = await self.db.get_user_district_influence(uid, guild_id, "downtown")
        gain = after - before
        expected = config.BUSINESS_ACTION_MARKET_EXPANSION_INFLUENCE * 1.02
        self.assertAlmostEqual(gain, expected, places=4)

    async def test_buff_affects_income(self) -> None:
        guild_id, uid = 1, 100
        await self._setup_business(uid, guild_id, 20_000.0)
        await self.db.perform_business_action(uid, guild_id, "marketing_campaign")
        row = await self.db.get_business(uid, guild_id)
        base = self.db._business_hourly_from_row(row)
        mult = await self.db._active_buff_multiplier_no_lock(uid, guild_id, __import__("time").time())
        self.assertGreater(base * mult, base)


class CorporationDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def _setup_crew(self, uid: int, guild_id: int, crew: str, treasury: float) -> None:
        await self.db.credit_wallet(uid, guild_id, treasury + 1_000.0, apply_bonuses=False)
        await self.db.join_crew(uid, guild_id, crew)
        await self.db.deposit_crew_treasury(uid, guild_id, treasury)

    async def test_buy_corporate_upgrade(self) -> None:
        guild_id, uid = 1, 100
        await self._setup_crew(uid, guild_id, "Acme", 200_000.0)
        cost, err = await self.db.buy_corporate_upgrade(uid, guild_id, "income")
        self.assertIsNone(err)
        self.assertGreater(cost, 0)
        levels = await self.db.get_corporate_upgrades(guild_id, "Acme")
        self.assertEqual(levels.get("income"), 1)

    async def test_corporate_income_bonus_applies(self) -> None:
        guild_id, uid = 1, 100
        await self._setup_crew(uid, guild_id, "Acme", 200_000.0)
        await self.db.credit_wallet(uid, guild_id, 1_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        row = await self.db.get_business(uid, guild_id)
        base_breakdown = await self.db.get_business_income_breakdown(uid, guild_id, row)
        assert base_breakdown is not None
        await self.db.buy_corporate_upgrade(uid, guild_id, "income")
        mult = await self.db._corporate_income_mult_no_lock(uid, guild_id)
        self.assertGreater(mult, 1.0)
        upgraded = await self.db.get_business_income_breakdown(uid, guild_id, row)
        assert upgraded is not None
        self.assertGreater(upgraded.effective_hourly, base_breakdown.effective_hourly)
        self.assertEqual(upgraded.base_hourly, base_breakdown.base_hourly)

    async def test_corporate_upgrade_insufficient_treasury(self) -> None:
        guild_id, uid = 1, 100
        await self._setup_crew(uid, guild_id, "Acme", 100.0)
        _, err = await self.db.buy_corporate_upgrade(uid, guild_id, "income")
        self.assertEqual(err, "insufficient_treasury")

    async def test_contribute_completes_project(self) -> None:
        guild_id, uid = 1, 100
        await self.db.join_crew(uid, guild_id, "Acme")
        await self.db.credit_wallet(uid, guild_id, 3_000_000.0, apply_bonuses=False)
        result = await self.db.contribute_to_corporate_project(uid, guild_id, "mega_mall", 2_000_000.0)
        self.assertIsNone(result["error"])
        self.assertTrue(result["completed"])
        self.assertGreater(float(result["reward"]), 0)

    async def test_contribute_partial(self) -> None:
        guild_id, uid = 1, 100
        await self.db.join_crew(uid, guild_id, "Acme")
        await self.db.credit_wallet(uid, guild_id, 50_000.0, apply_bonuses=False)
        result = await self.db.contribute_to_corporate_project(uid, guild_id, "mega_mall", 50_000.0)
        self.assertIsNone(result["error"])
        self.assertFalse(result["completed"])

    async def test_war_standings(self) -> None:
        guild_id = 1
        await self._setup_crew(100, guild_id, "Acme", 500_000.0)
        await self._setup_crew(200, guild_id, "Beta", 100_000.0)
        standings = await self.db.get_corporate_war_standings(guild_id)
        self.assertEqual(standings[0][0], "Acme")


class StockMarketMathTests(unittest.TestCase):
    def test_price_rises_with_treasury(self) -> None:
        low = share_price(0.0, 1)
        high = share_price(1_000_000.0, 1)
        self.assertGreater(high, low)

    def test_price_floor(self) -> None:
        self.assertGreaterEqual(share_price(0.0, 0), config.STOCK_MIN_PRICE)

    def test_sell_tax_applied(self) -> None:
        gross = 100.0 * 10
        self.assertLess(sell_proceeds(100.0, 10), gross)


class StockMarketDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def _setup_corp(self, uid: int, guild_id: int, crew: str, treasury: float) -> None:
        await self.db.credit_wallet(uid, guild_id, treasury + 1_000_000.0, apply_bonuses=False)
        await self.db.join_crew(uid, guild_id, crew)
        await self.db.deposit_crew_treasury(uid, guild_id, treasury)

    async def test_buy_and_sell_shares(self) -> None:
        guild_id, uid = 1, 100
        await self._setup_corp(uid, guild_id, "Acme", 100_000.0)
        total, err = await self.db.buy_shares(uid, guild_id, "Acme", 5)
        self.assertIsNone(err)
        self.assertGreater(total, 0)
        holdings = await self.db.get_stock_holdings(uid, guild_id)
        self.assertEqual(int(holdings[0]["shares"]), 5)
        proceeds, err = await self.db.sell_shares(uid, guild_id, "Acme", 5)
        self.assertIsNone(err)
        self.assertGreater(proceeds, 0)

    async def test_sell_more_than_owned(self) -> None:
        guild_id, uid = 1, 100
        await self._setup_corp(uid, guild_id, "Acme", 100_000.0)
        _, err = await self.db.sell_shares(uid, guild_id, "Acme", 10)
        self.assertEqual(err, "insufficient_shares")

    async def test_buy_unknown_corp(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 100_000.0, apply_bonuses=False)
        _, err = await self.db.buy_shares(uid, guild_id, "Ghost", 5)
        self.assertEqual(err, "unknown_corp")

    async def test_market_listing(self) -> None:
        guild_id = 1
        await self._setup_corp(100, guild_id, "Acme", 500_000.0)
        market = await self.db.list_stock_market(guild_id)
        self.assertTrue(any(m["crew_name"] == "Acme" for m in market))

    async def test_market_event_changes_price(self) -> None:
        guild_id = 1
        await self._setup_corp(100, guild_id, "Acme", 100_000.0)
        base = await self.db.get_share_price(guild_id, "Acme")
        await self.db.set_stock_market_event(
            guild_id, "tech_boom", config.STOCK_MARKET_EVENTS["tech_boom"],
            __import__("time").time() + 3600,
        )
        boosted = await self.db.get_share_price(guild_id, "Acme")
        self.assertGreater(boosted, base)


class MegaProjectMathTests(unittest.TestCase):
    def test_income_bonus_sum(self) -> None:
        ids = {p.project_id for p in MEGA_PROJECTS}
        total = income_bonus_from_completed(ids)
        self.assertAlmostEqual(total, sum(p.income_bonus for p in MEGA_PROJECTS))

    def test_unknown_ignored(self) -> None:
        self.assertEqual(income_bonus_from_completed({"nope"}), 0.0)


class EndgameDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def _maxed_business(self, uid: int, guild_id: int) -> None:
        await self.db.credit_wallet(uid, guild_id, 50_000_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        for _ in range(6):
            err, _ = await self.db.tier_up_business(uid, guild_id)
            self.assertIsNone(err)

    async def test_prestige_requires_max_tier(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 1_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        err, _ = await self.db.prestige_business(uid, guild_id)
        self.assertEqual(err, "not_max_tier")

    async def test_prestige_resets_and_increments(self) -> None:
        guild_id, uid = 1, 100
        await self._maxed_business(uid, guild_id)
        err, new_prestige = await self.db.prestige_business(uid, guild_id)
        self.assertIsNone(err)
        self.assertEqual(new_prestige, 1)
        row = await self.db.get_business(uid, guild_id)
        self.assertEqual(int(row["tier"]), 1)
        self.assertEqual(int(row["business_prestige"]), 1)

    async def test_seasonal_event_affects_income(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 1_000.0, apply_bonuses=False)
        await self.db.create_business(uid, guild_id)
        base = await self.db._business_event_mult_no_lock(guild_id)
        self.assertEqual(base, 1.0)
        await self.db.set_guild_event(guild_id, "holiday_rush", 1.25, __import__("time").time() + 3600)
        boosted = await self.db._business_event_mult_no_lock(guild_id)
        self.assertGreater(boosted, 1.0)

    async def test_mega_project_completion_grants_bonus(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 2_000_000_000.0, apply_bonuses=False)
        result = await self.db.contribute_to_mega_project(uid, guild_id, "space_program", 1_000_000_000.0)
        self.assertIsNone(result["error"])
        self.assertTrue(result["completed"])
        mult = await self.db._mega_income_mult_no_lock(uid, guild_id)
        self.assertGreater(mult, 1.0)

    async def test_mega_project_partial(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 1_000_000.0, apply_bonuses=False)
        result = await self.db.contribute_to_mega_project(uid, guild_id, "space_program", 1_000_000.0)
        self.assertIsNone(result["error"])
        self.assertFalse(result["completed"])


class DrugMathTests(unittest.TestCase):
    def test_catalog(self) -> None:
        self.assertEqual(len(DRUGS), 27)
        self.assertEqual(drug_by_id("blue_dream").name, "Blue Dream")
        self.assertEqual(drug_by_id("greenleaf").name, "Blue Dream")
        self.assertEqual(drug_by_id("cocaine").category, "stimulant")
        self.assertEqual(drug_by_id("addies").category, "stimulant")
        self.assertEqual(drug_by_id("wockhardt").category, "lean")
        self.assertEqual(drug_by_id("prometh_codeine").category, "codeine")
        self.assertIsNone(drug_by_id("nope"))

    def test_category_grouping_includes_new_lines(self) -> None:
        from utils.drugs import DRUG_CATEGORY_LABELS, drugs_by_category, drugs_for_category

        grouped = drugs_by_category()
        self.assertIn("codeine", grouped)
        self.assertIn("lean", grouped)
        self.assertEqual(len(drugs_for_category("lean")), 6)
        self.assertEqual(len(drugs_for_category("codeine")), 4)
        self.assertIn("addies", {d.drug_id for d in drugs_for_category("stimulant")})
        self.assertIn("lean", DRUG_CATEGORY_LABELS)

    def test_yield_bonus(self) -> None:
        defn = DRUGS[0]
        import random

        rng = random.Random(1)
        base = roll_yield(defn, yield_bonus=0.0, rng=random.Random(1))
        bonus = roll_yield(defn, yield_bonus=1.0, rng=rng)
        self.assertGreaterEqual(bonus, base)


class DrugDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def test_plant_requires_funds(self) -> None:
        guild_id, uid = 1, 100
        await self.db.ensure_user(uid, guild_id)
        _, err = await self.db.plant_drug(uid, guild_id, "blue_dream")
        self.assertEqual(err, "insufficient_funds")

    async def test_plant_and_harvest(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 10_000.0, apply_bonuses=False)
        cost, err = await self.db.plant_drug(uid, guild_id, "blue_dream")
        self.assertIsNone(err)
        self.assertGreater(cost, 0)
        # Fast-forward the grow.
        async with self.db._write_lock:
            await self.db.conn.execute(
                "UPDATE drug_grows SET ready_at = ready_at - 100000 WHERE user_id = ? AND guild_id = ?",
                (uid, guild_id),
            )
            await self.db.conn.commit()
        harvested = await self.db.harvest_drugs(uid, guild_id)
        self.assertIn("blue_dream", harvested)
        inv = await self.db.get_drug_inventory(uid, guild_id)
        self.assertGreater(inv.get("blue_dream", 0), 0)

    async def test_fertilizer_boosts_yield_and_shortens_grow(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 50_000.0, apply_bonuses=False)
        await self.db.buy_item(uid, guild_id, "fertilizer", 1)
        defn = drug_by_id("blue_dream")
        assert defn is not None
        _, err = await self.db.plant_drug(uid, guild_id, "blue_dream", fertilizer_id="fertilizer")
        self.assertIsNone(err)
        grows = await self.db.list_drug_grows(uid, guild_id)
        self.assertEqual(len(grows), 1)
        self.assertAlmostEqual(float(grows[0]["yield_mult"]), 1.5)
        remaining = float(grows[0]["ready_at"]) - __import__("time").time()
        self.assertLess(remaining, defn.grow_seconds * 0.8)

    async def test_apply_fertilizer_to_existing_crop(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 50_000.0, apply_bonuses=False)
        await self.db.buy_item(uid, guild_id, "xl_fertilizer", 1)
        _, err = await self.db.plant_drug(uid, guild_id, "blue_dream")
        self.assertIsNone(err)
        grows = await self.db.list_drug_grows(uid, guild_id)
        grow_id = int(grows[0]["grow_id"])
        before_ready = float(grows[0]["ready_at"])
        err = await self.db.apply_fertilizer_to_grow(uid, guild_id, grow_id, "xl_fertilizer")
        self.assertIsNone(err)
        grows = await self.db.list_drug_grows(uid, guild_id)
        self.assertAlmostEqual(float(grows[0]["yield_mult"]), 2.0)
        self.assertLess(float(grows[0]["ready_at"]), before_ready)

    async def test_legacy_stash_alias(self) -> None:
        guild_id, uid = 1, 100
        await self.db.ensure_user(uid, guild_id)
        await self._stock_product(uid, guild_id, "greenleaf", 3)
        inv = await self.db.get_drug_inventory(uid, guild_id)
        self.assertEqual(inv.get("blue_dream"), 3)

    async def test_lab_slot_cap(self) -> None:
        guild_id, uid = 1, 100
        await self.db.credit_wallet(uid, guild_id, 100_000.0, apply_bonuses=False)
        for _ in range(config.DRUG_LAB_SLOTS):
            _, err = await self.db.plant_drug(uid, guild_id, "blue_dream")
            self.assertIsNone(err)
        _, err = await self.db.plant_drug(uid, guild_id, "blue_dream")
        self.assertEqual(err, "no_slots")

    async def _stock_product(self, uid: int, guild_id: int, drug_id: str, qty: int) -> None:
        async with self.db._write_lock:
            await self.db.conn.execute(
                """
                INSERT INTO drug_inventory (user_id, guild_id, drug_id, quantity)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id, drug_id) DO UPDATE SET quantity = drug_inventory.quantity + excluded.quantity
                """,
                (uid, guild_id, drug_id, qty),
            )
            await self.db.conn.commit()

    async def test_street_sell(self) -> None:
        guild_id, uid = 1, 100
        await self.db.ensure_user(uid, guild_id)
        await self._stock_product(uid, guild_id, "blue_dream", 10)
        # Force no-raid by patching the raid chance to 0 via config is global; retry a few times.
        import config as cfg

        old = cfg.DRUG_RAID_CHANCE
        cfg.DRUG_RAID_CHANCE = 0.0
        try:
            result = await self.db.sell_drugs_street(uid, guild_id, "blue_dream", 5)
        finally:
            cfg.DRUG_RAID_CHANCE = old
        self.assertIsNone(result["error"])
        self.assertFalse(result["raided"])
        self.assertGreater(float(result["total"]), 0)

    async def test_street_sell_insufficient(self) -> None:
        guild_id, uid = 1, 100
        await self.db.ensure_user(uid, guild_id)
        result = await self.db.sell_drugs_street(uid, guild_id, "blue_dream", 5)
        self.assertEqual(result["error"], "insufficient_product")

    async def test_consume_drug(self) -> None:
        guild_id, uid = 1, 100
        await self.db.ensure_user(uid, guild_id)
        await self._stock_product(uid, guild_id, "og_kush", 2)
        result = await self.db.consume_drug(uid, guild_id, "og_kush", max_hp=200.0)
        self.assertIsNone(result["error"])
        self.assertEqual(result["name"], "OG Kush")
        self.assertGreater(float(result["heal_amount"]), 0)
        inv = await self.db.get_drug_inventory(uid, guild_id)
        self.assertEqual(inv.get("og_kush"), 1)

    async def test_consume_legacy_stash_id(self) -> None:
        guild_id, uid = 1, 100
        await self.db.ensure_user(uid, guild_id)
        await self._stock_product(uid, guild_id, "greenleaf", 1)
        result = await self.db.consume_drug(uid, guild_id, "blue_dream", max_hp=200.0)
        self.assertIsNone(result["error"])
        self.assertEqual(result["name"], "Blue Dream")
        inv = await self.db.get_drug_inventory(uid, guild_id)
        self.assertEqual(inv.get("blue_dream", 0), 0)

    async def test_pending_drug_buff(self) -> None:
        guild_id, uid = 1, 100
        await self.db.ensure_user(uid, guild_id)
        await self._stock_product(uid, guild_id, "girl_scout_cookies", 1)
        consumed = await self.db.consume_drug(uid, guild_id, "girl_scout_cookies", max_hp=200.0)
        self.assertIsNone(consumed["error"])
        pending = await self.db.peek_pending_drug_buff(uid, guild_id)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["name"], "Girl Scout Cookies")
        self.assertGreater(float(pending["boss_mult"]), 1.0)
        taken = await self.db.take_pending_drug_buff(uid, guild_id)
        self.assertIsNotNone(taken)
        still_active = await self.db.peek_pending_drug_buff(uid, guild_id)
        self.assertIsNotNone(still_active)

    async def test_market_list_and_buy(self) -> None:
        guild_id, seller, buyer = 1, 100, 200
        await self.db.ensure_user(seller, guild_id)
        await self.db.credit_wallet(buyer, guild_id, 100_000.0, apply_bonuses=False)
        await self._stock_product(seller, guild_id, "blue_dream", 10)
        err = await self.db.create_drug_listing(seller, guild_id, "blue_dream", 5, 200.0)
        self.assertIsNone(err)
        listings = await self.db.list_drug_market(guild_id)
        self.assertEqual(len(listings), 1)
        listing_id = int(listings[0]["listing_id"])
        result = await self.db.buy_drug_listing(buyer, guild_id, listing_id, 5)
        self.assertIsNone(result["error"])
        buyer_inv = await self.db.get_drug_inventory(buyer, guild_id)
        self.assertEqual(buyer_inv.get("blue_dream"), 5)

    async def test_market_cannot_buy_own(self) -> None:
        guild_id, seller = 1, 100
        await self.db.ensure_user(seller, guild_id)
        await self._stock_product(seller, guild_id, "blue_dream", 10)
        await self.db.create_drug_listing(seller, guild_id, "blue_dream", 5, 200.0)
        listings = await self.db.list_drug_market(guild_id)
        result = await self.db.buy_drug_listing(seller, guild_id, int(listings[0]["listing_id"]), 1)
        self.assertEqual(result["error"], "own_listing")

    async def test_cancel_listing_returns_product(self) -> None:
        guild_id, seller = 1, 100
        await self.db.ensure_user(seller, guild_id)
        await self._stock_product(seller, guild_id, "blue_dream", 10)
        await self.db.create_drug_listing(seller, guild_id, "blue_dream", 5, 200.0)
        listings = await self.db.list_drug_market(guild_id)
        err = await self.db.cancel_drug_listing(seller, guild_id, int(listings[0]["listing_id"]))
        self.assertIsNone(err)
        inv = await self.db.get_drug_inventory(seller, guild_id)
        self.assertEqual(inv.get("blue_dream"), 10)

    async def test_list_user_drug_listings(self) -> None:
        guild_id, seller, other = 1, 100, 200
        await self.db.ensure_user(seller, guild_id)
        await self.db.ensure_user(other, guild_id)
        await self._stock_product(seller, guild_id, "wockhardt", 8)
        await self._stock_product(other, guild_id, "blue_dream", 5)
        await self.db.create_drug_listing(seller, guild_id, "wockhardt", 3, 500.0)
        await self.db.create_drug_listing(other, guild_id, "blue_dream", 2, 150.0)
        mine = await self.db.list_user_drug_listings(seller, guild_id)
        self.assertEqual(len(mine), 1)
        self.assertEqual(str(mine[0]["drug_id"]), "wockhardt")
        self.assertEqual(int(mine[0]["quantity"]), 3)


if __name__ == "__main__":
    unittest.main()
