"""Dealer rank and title tests."""
from __future__ import annotations

import unittest

from utils.dealer_ranks import dealer_rank, dealer_reputation, dealer_title_for_stats, rank_title


class DealerRankTests(unittest.TestCase):
    def test_cultivation_counts_toward_rank(self) -> None:
        self.assertEqual(
            dealer_rank(units_sold=0, units_harvested=60_000),
            10,
        )
        self.assertEqual(rank_title(10), "Cartel")

    def test_sales_only_legacy_progress_still_counts(self) -> None:
        self.assertEqual(dealer_reputation(units_sold=500, units_harvested=0), 500)

    def test_cartel_title_unlock(self) -> None:
        title = dealer_title_for_stats({"units_sold": 0, "units_harvested": 60_000})
        self.assertEqual(title, "Cartel")
        self.assertIsNone(dealer_title_for_stats({"units_sold": 10, "units_harvested": 10}))


if __name__ == "__main__":
    unittest.main()
