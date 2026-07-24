"""RED phase: minimal failing test to verify core.py must exist and export SwitchResult + perform_switch."""
import unittest


class TestCoreRedPhase(unittest.TestCase):
    def test_core_importable_and_exports_required_symbols(self):
        """core.py must export SwitchResult and perform_switch without importing tkinter."""
        import core
        self.assertTrue(hasattr(core, "SwitchResult"))
        self.assertTrue(hasattr(core, "perform_switch"))

    def test_switch_result_has_all_six_values(self):
        from core import SwitchResult
        self.assertIn("SUCCESS", [v.name for v in SwitchResult])
        self.assertIn("BLOCKED", [v.name for v in SwitchResult])
        self.assertIn("NO_SNAPSHOT", [v.name for v in SwitchResult])
        self.assertIn("RIOT_NOT_FOUND", [v.name for v in SwitchResult])
        self.assertIn("STOP_FAILED", [v.name for v in SwitchResult])
        self.assertIn("ERROR", [v.name for v in SwitchResult])


if __name__ == "__main__":
    unittest.main()
