"""Tests fuer Plan 03-02 Task 1: argparse switch-Subcommand in main.py.

Testet headless CLI-Pfad (SD-02, D-29/D-30b/D-31/SD-07):
  - SUCCESS -> sys.exit(0) + save_state mit active_username
  - BLOCKED  -> sys.exit(1) + kein save_state
  - NO_SNAPSHOT -> sys.exit(1) + kein save_state
  - GUI-freier Import im switch-Branch (kein customtkinter/controller/gui)
  - Ohne Subcommand -> kein SystemExit (GUI-Pfad)

Anti-Pattern-Guard: Dieser Testfile importiert KEIN customtkinter oder tkinter.
"""
from __future__ import annotations

import sys
import types
import importlib
import pathlib
import unittest
from unittest.mock import MagicMock, patch, call


def _state_with(*usernames: str) -> MagicMock:
    """Mock-AppState dessen .accounts die gegebenen usernames enthaelt.

    CR-02: main.main() persistiert active_username nur, wenn der username
    einem vorhandenen Account entspricht — Tests muessen daher echte
    Account-Stubs in state.accounts liefern.
    """
    state = MagicMock()
    state.active_username = None
    state.accounts = [types.SimpleNamespace(username=u) for u in usernames]
    return state


# ---------------------------------------------------------------------------
# Stubs fuer keyring (wird von main._assert_secure_keyring importiert)
# Hinweis: GUI-Imports (customtkinter, controller, gui) werden NICHT auf
# Modulebene geblockt — das neue main.py importiert sie nur im GUI-Branch
# (lazy imports), nicht auf Modulebene. Tests fuer den switch-Branch benoetigen
# diese Stubs daher nicht.
# ---------------------------------------------------------------------------

# Stub keyring NUR wenn noch nicht vorhanden (verhindert WCM-Zugriff in Tests)
if "keyring" not in sys.modules:
    _kr_stub = types.ModuleType("keyring")
    _kr_stub.get_keyring = lambda: MagicMock(__class__=MagicMock(__name__="WinVaultKeyring"))
    sys.modules["keyring"] = _kr_stub

# Import main — keine GUI-Imports auf Modulebene seit der Refaktorierung
import main as _main_module


# ===========================================================================
# Helper
# ===========================================================================

def _reload_main():
    """Re-import main module fresh (avoids cached state)."""
    return importlib.reload(_main_module)


# ===========================================================================
# Task 1 Tests: CLI switch subcommand
# ===========================================================================

class TestCliSwitchSuccess(unittest.TestCase):
    """main.main() mit switch-Subcommand und SUCCESS-Result."""

    def test_success_exits_0(self):
        """SUCCESS -> sys.exit(0)."""
        import main
        import core
        import config
        from core import SwitchResult

        mock_state = _state_with("alice")

        with patch.object(sys, "argv", ["lolswitcher", "switch", "alice"]), \
             patch("core.perform_switch", return_value=SwitchResult.SUCCESS) as mock_switch, \
             patch("config.load_state", return_value=mock_state), \
             patch("config.save_state") as mock_save:
            with self.assertRaises(SystemExit) as ctx:
                main.main()
            self.assertEqual(ctx.exception.code, 0)

    def test_success_calls_save_state_with_username(self):
        """SUCCESS -> save_state wird mit active_username=alice aufgerufen."""
        import main
        import core
        import config
        from core import SwitchResult

        captured_state = _state_with("alice")

        with patch.object(sys, "argv", ["lolswitcher", "switch", "alice"]), \
             patch("core.perform_switch", return_value=SwitchResult.SUCCESS), \
             patch("config.load_state", return_value=captured_state), \
             patch("config.save_state") as mock_save:
            with self.assertRaises(SystemExit):
                main.main()
            mock_save.assert_called_once()
            self.assertEqual(captured_state.active_username, "alice")

    def test_success_calls_perform_switch_with_username(self):
        """SUCCESS -> perform_switch wird mit 'alice' aufgerufen."""
        import main
        import core
        import config
        from core import SwitchResult

        mock_state = _state_with("alice")
        with patch.object(sys, "argv", ["lolswitcher", "switch", "alice"]), \
             patch("core.perform_switch", return_value=SwitchResult.SUCCESS) as mock_ps, \
             patch("config.load_state", return_value=mock_state), \
             patch("config.save_state"):
            with self.assertRaises(SystemExit):
                main.main()
            mock_ps.assert_called_once_with("alice")


class TestCliSwitchBlocked(unittest.TestCase):
    """main.main() mit switch + BLOCKED -> exit 1, kein save_state."""

    def test_blocked_exits_nonzero(self):
        """BLOCKED -> sys.exit(1)."""
        import main
        import core
        from core import SwitchResult

        with patch.object(sys, "argv", ["lolswitcher", "switch", "alice"]), \
             patch("core.perform_switch", return_value=SwitchResult.BLOCKED), \
             patch("config.save_state") as mock_save:
            with self.assertRaises(SystemExit) as ctx:
                main.main()
            self.assertNotEqual(ctx.exception.code, 0)
            mock_save.assert_not_called()


class TestCliSwitchNoSnapshot(unittest.TestCase):
    """main.main() mit switch + NO_SNAPSHOT -> exit 1, kein save_state."""

    def test_no_snapshot_exits_nonzero(self):
        """NO_SNAPSHOT -> sys.exit(1)."""
        import main
        import core
        from core import SwitchResult

        with patch.object(sys, "argv", ["lolswitcher", "switch", "alice"]), \
             patch("core.perform_switch", return_value=SwitchResult.NO_SNAPSHOT), \
             patch("config.save_state") as mock_save:
            with self.assertRaises(SystemExit) as ctx:
                main.main()
            self.assertNotEqual(ctx.exception.code, 0)
            mock_save.assert_not_called()


class TestCliSwitchOtherErrors(unittest.TestCase):
    """Andere Fehlerwerte -> exit 1, kein save_state."""

    def test_stop_failed_exits_nonzero(self):
        """STOP_FAILED -> sys.exit(1)."""
        import main
        import core
        from core import SwitchResult

        with patch.object(sys, "argv", ["lolswitcher", "switch", "alice"]), \
             patch("core.perform_switch", return_value=SwitchResult.STOP_FAILED), \
             patch("config.save_state") as mock_save:
            with self.assertRaises(SystemExit) as ctx:
                main.main()
            self.assertNotEqual(ctx.exception.code, 0)
            mock_save.assert_not_called()

    def test_error_exits_nonzero(self):
        """ERROR -> sys.exit(1)."""
        import main
        import core
        from core import SwitchResult

        with patch.object(sys, "argv", ["lolswitcher", "switch", "alice"]), \
             patch("core.perform_switch", return_value=SwitchResult.ERROR), \
             patch("config.save_state") as mock_save:
            with self.assertRaises(SystemExit) as ctx:
                main.main()
            self.assertNotEqual(ctx.exception.code, 0)
            mock_save.assert_not_called()


class TestCliSwitchActiveMarkerPersistence(unittest.TestCase):
    """CR-02: active_username nur bei vorhandenem Account persistieren; Save-Fehler
    darf den bereits erfolgten Switch NICHT als Fehlschlag melden."""

    def test_success_unknown_account_does_not_persist(self):
        """SUCCESS aber username nicht in accounts -> exit 0, KEIN save_state."""
        import main
        from core import SwitchResult

        state = _state_with("bob")  # 'alice' ist NICHT enthalten

        with patch.object(sys, "argv", ["lolswitcher", "switch", "alice"]), \
             patch("core.perform_switch", return_value=SwitchResult.SUCCESS), \
             patch("config.load_state", return_value=state), \
             patch("config.save_state") as mock_save:
            with self.assertRaises(SystemExit) as ctx:
                main.main()
            self.assertEqual(ctx.exception.code, 0)
            mock_save.assert_not_called()

    def test_success_save_failure_still_exits_0(self):
        """SUCCESS + save_state wirft OSError -> trotzdem exit 0 (Marker best-effort)."""
        import main
        from core import SwitchResult

        state = _state_with("alice")

        with patch.object(sys, "argv", ["lolswitcher", "switch", "alice"]), \
             patch("core.perform_switch", return_value=SwitchResult.SUCCESS), \
             patch("config.load_state", return_value=state), \
             patch("config.save_state", side_effect=OSError("disk full")) as mock_save:
            with self.assertRaises(SystemExit) as ctx:
                main.main()
            self.assertEqual(ctx.exception.code, 0)
            mock_save.assert_called_once()


class TestCliSwitchNoGuiImport(unittest.TestCase):
    """Im switch-Branch wird customtkinter NICHT importiert (SD-07 / Anti-Pattern-Guard)."""

    def test_switch_branch_does_not_import_customtkinter(self):
        """Im switch-Branch werden customtkinter/controller/gui NICHT importiert (SD-07).

        Prueft die Import-Struktur via AST: customtkinter darf nur innerhalb des
        GUI-Zweigs in main() auftauchen, NICHT auf Modulebene oder im switch-Branch.
        Prueft ausserdem, dass main.main() im switch-Pfad sauber sys.exit(1) wirft
        ohne tkinter-Submodule nachzuladen.
        """
        import ast
        import main

        # AST-Analyse: customtkinter darf NUR innerhalb einer Funktion importiert werden
        src = open("main.py", encoding="utf-8").read()
        tree = ast.parse(src)

        # Toplevel-Imports: duerfen nur stdlib enthalten
        top_level_imports = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and getattr(node, "col_offset", -1) == 0
        ]
        top_level_names = set()
        for node in top_level_imports:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                top_level_names.add(node.module or "")

        gui_on_toplevel = {n for n in top_level_names
                          if "customtkinter" in n or "controller" in n or "gui" in n}
        self.assertEqual(gui_on_toplevel, set(),
                         f"GUI-Imports auf Modulebene in main.py: {gui_on_toplevel}")

        # Funktionsaufruf-Test: switch-Branch soll sys.exit(1) werfen ohne neue tkinter-Imports
        from core import SwitchResult
        before_keys = set(sys.modules.keys())

        with patch.object(sys, "argv", ["lolswitcher", "switch", "test_user"]), \
             patch("core.perform_switch", return_value=SwitchResult.BLOCKED), \
             patch("config.save_state"):
            with self.assertRaises(SystemExit) as ctx:
                main.main()

        self.assertNotEqual(ctx.exception.code, 0)

        # Keine neuen tkinter-Submodule geladen (customtkinter selbst kann vorhanden sein
        # wenn vorherige Tests es luden — pruefe nur neu hinzugefuegte Module)
        new_keys = set(sys.modules.keys()) - before_keys
        new_tkinter = {k for k in new_keys if "tkinter" in k.lower()}
        self.assertEqual(new_tkinter, set(),
                         f"Unerwartete tkinter-Imports im switch-Pfad: {new_tkinter}")


class TestCliGuiPathNotExited(unittest.TestCase):
    """Ohne switch-Subcommand (GUI-Pfad) darf main() keinen sys.exit(0/1) via argparse ausloesen."""

    def test_no_subcommand_does_not_exit_from_switch_branch(self):
        """Aufruf ohne Argumente -> switch-Branch wird nicht betreten -> kein CLI-Exit.

        Der GUI-Pfad endet mit _assert_secure_keyring() oder dem Tk-Loop —
        in Tests simulieren wir das durch Exception-Injection im GUI-Pfad.
        Kein SystemExit mit Code 0 oder 1 aus dem switch-Branch.
        """
        import main

        # GUI-Pfad: _assert_secure_keyring wird aufgerufen und wuerde normalerweise
        # das Keyring validieren. In Tests muss es einen RuntimeError werfen (kein echtes WCM),
        # NICHT einen SystemExit aus dem switch-Branch (Code 0/1).
        with patch.object(sys, "argv", ["lolswitcher"]):
            # Wir mocken _assert_secure_keyring um keinen echten WCM-Check zu machen.
            # Der switch-Branch darf NICHT aufgerufen werden (args.command == None).
            # Die GUI-Imports (customtkinter, gui, controller) werden erst nach
            # _assert_secure_keyring aufgerufen — sie werden niemals erreicht wenn
            # _assert_secure_keyring frueher wirft.
            with patch.object(main, "_assert_secure_keyring") as mock_assert:
                # Inject a stop so the mainloop doesn't run
                mock_assert.side_effect = RuntimeError("stop-gui")
                with self.assertRaises(RuntimeError) as ctx:
                    main.main()
                self.assertEqual(str(ctx.exception), "stop-gui")
                # _assert_secure_keyring war erreichbar -> switch-Branch wurde nicht betreten
                mock_assert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
