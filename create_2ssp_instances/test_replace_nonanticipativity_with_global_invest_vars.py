from __future__ import annotations

import gzip
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from replace_nonanticipativity_with_global_invest_vars import globalize_line, merge  # noqa: E402


MODEL = """\\* integrated *\\

min
objective:
+10 InvestmentFlowBlock_invest(asset_base)
+10 InvestmentFlowBlock_invest(asset_elec1p2)
+10 InvestmentFlowBlock_invest(asset_heat1p2)
+1 flow(asset_base)
s.t.
c_e_balance(asset_base)_:
+1 InvestmentFlowBlock_invest(asset_base)
+1 flow(asset_base)
= 0
c_e_balance(asset_elec1p2)_:
+1 InvestmentFlowBlock_invest(asset_elec1p2)
= 0
c_e_balance(asset_heat1p2)_:
+1 InvestmentFlowBlock_invest(asset_heat1p2)
= 0
c_e_nonanticipativity_(InvestmentFlowBlock_invest(asset)_base_elec1p2)_:
+1 InvestmentFlowBlock_invest(asset_base)
-1 InvestmentFlowBlock_invest(asset_elec1p2)
= 0
c_e_nonanticipativity_(InvestmentFlowBlock_invest(asset)_base_heat1p2)_:
+1 InvestmentFlowBlock_invest(asset_base)
-1 InvestmentFlowBlock_invest(asset_heat1p2)
= 0
bounds
   0 <= InvestmentFlowBlock_invest(asset_base) <= 7
   0 <= InvestmentFlowBlock_invest(asset_elec1p2) <= 7
   0 <= InvestmentFlowBlock_invest(asset_heat1p2) <= 7
   0 <= flow(asset_base) <= +inf
End
"""


class GlobalInvestmentTransformationTest(unittest.TestCase):
    def test_prefers_a_compound_scenario_suffix(self):
        rewritten, replaced = globalize_line(
            "InvestmentFlowBlock_invest(asset_elec1p2_heat_decentral1p2)\n",
            ["base", "elec1p2", "heat_decentral1p2", "elec1p2_heat_decentral1p2"],
            "global",
        )
        self.assertEqual(replaced, {"elec1p2_heat_decentral1p2"})
        self.assertEqual(rewritten, "InvestmentFlowBlock_invest(asset_global)\n")

    def test_replaces_investments_and_removes_couplings(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.lp.gz"
            output = Path(directory) / "output.lp.gz"
            with gzip.open(source, "wt", encoding="utf-8") as handle:
                handle.write(MODEL)

            self.assertEqual(merge(source, output, ["base", "elec1p2", "heat1p2"]), (2, 1))
            with gzip.open(output, "rt", encoding="utf-8") as handle:
                transformed = handle.read()

            self.assertNotIn("c_e_nonanticipativity_", transformed)
            self.assertNotIn("InvestmentFlowBlock_invest(asset_base)", transformed)
            self.assertNotIn("InvestmentFlowBlock_invest(asset_elec1p2)", transformed)
            self.assertIn("InvestmentFlowBlock_invest(asset_global)", transformed)
            self.assertEqual(transformed.count("+30 InvestmentFlowBlock_invest(asset_global)"), 1)
            self.assertEqual(transformed.count("0 <= InvestmentFlowBlock_invest(asset_global) <= 7"), 1)
            self.assertIn("flow(asset_base)", transformed)

    def test_warns_and_intersects_different_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.lp.gz"
            output = Path(directory) / "output.lp.gz"
            modified = MODEL.replace("+10 InvestmentFlowBlock_invest(asset_elec1p2)", "+12 InvestmentFlowBlock_invest(asset_elec1p2)")
            modified = modified.replace("0 <= InvestmentFlowBlock_invest(asset_elec1p2) <= 7", "2 <= InvestmentFlowBlock_invest(asset_elec1p2) <= 6")
            with gzip.open(source, "wt", encoding="utf-8") as handle:
                handle.write(modified)
            warnings = io.StringIO()
            with redirect_stderr(warnings):
                merge(source, output, ["base", "elec1p2", "heat1p2"])
            with gzip.open(output, "rt", encoding="utf-8") as handle:
                transformed = handle.read()

            self.assertIn("objective costs differ", warnings.getvalue())
            self.assertIn("bounds differ", warnings.getvalue())
            self.assertIn("2 <= InvestmentFlowBlock_invest(asset_global) <= 6", transformed)


if __name__ == "__main__":
    unittest.main()
