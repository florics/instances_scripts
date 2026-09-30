#!/usr/bin/env python3
"""Tests for create_two_scenario_lp.py; run with python test_create_two_scenario_lp.py."""

from __future__ import annotations

import gzip
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_two_scenario_lp import InputError, merge  # noqa: E402


MODEL = """\\* source *\\

min
objective:
+10 InvestmentFlowBlock_invest(asset_0)
+1 flow(asset_0_0)
s.t.
c_e_balance(asset_0)_:
+1 flow(asset_0_0)
-1 InvestmentFlowBlock_invest(asset_0)
= 0
c_u_emission_limit_:
+1 flow(asset_0_0)
<= 2
bounds
   0 <= flow(asset_0_0) <= +inf
   0 <= InvestmentFlowBlock_invest(asset_0) <= 7
   0 <= GenericInvestmentStorageBlock_init_content(store) <= +inf
End
"""


class CreateTwoScenarioLpTest(unittest.TestCase):
    def write_model(self, path: Path, model: str = MODEL) -> None:
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(model)

    def test_merges_and_indexes_all_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            first = directory_path / "first.lp.gz"
            second = directory_path / "second.lp.gz"
            output = directory_path / "combined.lp.gz"
            self.write_model(first)
            self.write_model(second)

            self.assertEqual(merge(first, second, output, "dry", "wet"), 1)
            with gzip.open(output, "rt", encoding="utf-8") as handle:
                combined = handle.read()

            self.assertIn("+10 InvestmentFlowBlock_invest(asset_0_dry)", combined)
            self.assertIn("+10 InvestmentFlowBlock_invest(asset_0_wet)", combined)
            self.assertIn("c_e_balance(asset_0_dry)_:", combined)
            self.assertIn("c_u_emission_limit_dry_:", combined)
            self.assertIn("c_u_emission_limit_wet_:", combined)
            self.assertIn("GenericInvestmentStorageBlock_init_content(store_wet)", combined)
            self.assertIn(
                "c_e_nonanticipativity_(InvestmentFlowBlock_invest(asset_0)_dry_wet)_:",
                combined,
            )
            self.assertIn("+1 InvestmentFlowBlock_invest(asset_0_dry)", combined)
            self.assertIn("-1 InvestmentFlowBlock_invest(asset_0_wet)", combined)
            self.assertLess(combined.index("c_e_nonanticipativity_("), combined.index("bounds\n"))
            self.assertIn("= 0\n\nbounds\n", combined)
            self.assertLess(combined.index("bounds\n"), combined.index("0 <= flow(asset_0_0_dry)"))
            self.assertTrue(combined.endswith("End\n"))

    def test_rejects_unmatched_investment_variables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            first = directory_path / "first.lp.gz"
            second = directory_path / "second.lp.gz"
            self.write_model(first)
            self.write_model(second, MODEL.replace("asset_0", "other_0"))

            with self.assertRaises(InputError):
                merge(first, second, directory_path / "combined.lp.gz", "one", "two")

    def test_writes_an_uncompressed_lp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            first = directory_path / "first.lp.gz"
            second = directory_path / "second.lp.gz"
            output = directory_path / "combined.lp"
            self.write_model(first)
            self.write_model(second)

            merge(first, second, output, "one", "two")
            self.assertTrue(output.read_text(encoding="utf-8").endswith("End\n"))


if __name__ == "__main__":
    unittest.main()
