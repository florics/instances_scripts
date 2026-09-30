#!/usr/bin/env python3
"""Tests for create_multi_scenario_lp.py; run with python test_create_multi_scenario_lp.py."""

from __future__ import annotations

import gzip
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_multi_scenario_lp import InputError, merge  # noqa: E402


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
bounds
   0 <= flow(asset_0_0) <= +inf
   0 <= InvestmentFlowBlock_invest(asset_0) <= 7
End
"""


class CreateMultiScenarioLpTest(unittest.TestCase):
    def write_model(self, path: Path, model: str = MODEL) -> None:
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(model)

    def test_merges_three_scenarios_with_first_as_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            scenarios = [(name, directory_path / f"{name}.lp.gz") for name in ("base", "low", "high")]
            for _name, path in scenarios:
                self.write_model(path)
            output = directory_path / "combined.lp.gz"

            self.assertEqual(merge(scenarios, output), 2)
            with gzip.open(output, "rt", encoding="utf-8") as handle:
                combined = handle.read()

            self.assertIn("InvestmentFlowBlock_invest(asset_0_base)", combined)
            self.assertIn("InvestmentFlowBlock_invest(asset_0_low)", combined)
            self.assertIn("InvestmentFlowBlock_invest(asset_0_high)", combined)
            self.assertIn("c_e_balance(asset_0_base)_:", combined)
            self.assertIn("c_e_balance(asset_0_low)_:", combined)
            self.assertIn("c_e_balance(asset_0_high)_:", combined)
            self.assertIn("c_e_nonanticipativity_(InvestmentFlowBlock_invest(asset_0)_base_low)_:", combined)
            self.assertIn("c_e_nonanticipativity_(InvestmentFlowBlock_invest(asset_0)_base_high)_:", combined)
            self.assertIn("= 0\n\nbounds\n", combined)

    def test_uses_explicit_reference_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            scenarios = [(name, directory_path / f"{name}.lp.gz") for name in ("base", "low", "high")]
            for _name, path in scenarios:
                self.write_model(path)

            output = directory_path / "combined.lp.gz"
            merge(scenarios, output, "high")
            with gzip.open(output, "rt", encoding="utf-8") as handle:
                combined = handle.read()

            self.assertIn("c_e_nonanticipativity_(InvestmentFlowBlock_invest(asset_0)_high_base)_:", combined)
            self.assertIn("c_e_nonanticipativity_(InvestmentFlowBlock_invest(asset_0)_high_low)_:", combined)

    def test_accepts_hyphenated_scenario_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            scenarios = [(name, directory_path / f"{name}.lp.gz") for name in ("base", "elec-heat-1p2")]
            for _name, path in scenarios:
                self.write_model(path)

            output = directory_path / "combined.lp.gz"
            merge(scenarios, output, "elec-heat-1p2")
            with gzip.open(output, "rt", encoding="utf-8") as handle:
                combined = handle.read()

            self.assertIn("InvestmentFlowBlock_invest(asset_0_elec-heat-1p2)", combined)
            self.assertIn("c_e_nonanticipativity_(InvestmentFlowBlock_invest(asset_0)_elec-heat-1p2_base)_:", combined)

    def test_rejects_unknown_reference_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            scenarios = [(name, directory_path / f"{name}.lp.gz") for name in ("base", "low")]
            for _name, path in scenarios:
                self.write_model(path)

            with self.assertRaises(InputError):
                merge(scenarios, directory_path / "combined.lp.gz", "missing")


if __name__ == "__main__":
    unittest.main()
