import sys
import unittest

from ncodeprocess.preferences import SETTING_DEFAULTS, clear_settings, load_settings, save_settings

# 使用独立的测试键，避免污染真实的 HKCU\Software\NCodeProcess。
TEST_KEY = r"Software\NCodeProcess_UnitTests"


@unittest.skipUnless(sys.platform == "win32", "注册表仅存在于 Windows")
class PreferencesSettingsTests(unittest.TestCase):
    def tearDown(self):
        clear_settings(TEST_KEY)

    def test_defaults_cover_all_setting_keys(self):
        expected = {
            "encoding", "delete_extensions", "allowed_name_pattern", "aptsource_dir",
            "program_extensions", "program_output_extension",
            "require_end_marker", "require_m06", "require_spindle_speed",
        }
        self.assertEqual(set(SETTING_DEFAULTS), expected)

    def test_save_and_load_roundtrip(self):
        save_settings({"encoding": "gb18030", "require_m06": "1", "delete_extensions": ".log"}, TEST_KEY)
        loaded = load_settings(TEST_KEY)
        self.assertEqual(loaded["encoding"], "gb18030")
        self.assertEqual(loaded["require_m06"], "1")
        self.assertEqual(loaded["delete_extensions"], ".log")

    def test_load_missing_key_returns_empty(self):
        self.assertEqual(load_settings(TEST_KEY), {})

    def test_clear_settings_removes_all_setting_values(self):
        save_settings({"encoding": "gb18030", "require_m06": "1"}, TEST_KEY)
        clear_settings(TEST_KEY)
        self.assertEqual(load_settings(TEST_KEY), {})

    def test_save_keeps_other_values_in_same_key(self):
        # save_settings 只写设置值，不应破坏同一键下的其他值（如编制）。
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as key:
            winreg.SetValueEx(key, "bianzhi", 0, winreg.REG_SZ, "测试")
        save_settings({"require_m06": "1"}, TEST_KEY)
        self.assertEqual(load_settings(TEST_KEY), {"require_m06": "1"})
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as key:
            self.assertEqual(str(winreg.QueryValueEx(key, "bianzhi")[0]), "测试")


if __name__ == "__main__":
    unittest.main()
