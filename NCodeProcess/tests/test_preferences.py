import sys
import unittest

from ncodeprocess.preferences import REGISTRY_DEFAULTS, clear_all, load_all, save_all

# 使用独立的测试键，避免污染真实的 HKCU\Software\NCodeProcess。
TEST_KEY = r"Software\NCodeProcess_UnitTests"


@unittest.skipUnless(sys.platform == "win32", "注册表仅存在于 Windows")
class PreferencesTests(unittest.TestCase):
    def tearDown(self):
        clear_all(TEST_KEY)

    def test_defaults_cover_all_registry_items(self):
        # 统一注册表模型：编制/审核 + 程序设置全部项
        self.assertEqual(set(REGISTRY_DEFAULTS), {
            "bianzhi", "shenhe",
            "encoding", "delete_extensions", "allowed_name_pattern", "aptsource_dir",
            "program_extensions", "program_output_extension",
            "require_end_marker", "require_m06", "require_spindle_speed",
        })

    def test_save_and_load_roundtrip(self):
        save_all({"encoding": "gb18030", "require_m06": "1", "bianzhi": "张工"}, TEST_KEY)
        loaded = load_all(TEST_KEY)
        self.assertEqual(loaded["encoding"], "gb18030")
        self.assertEqual(loaded["require_m06"], "1")
        self.assertEqual(loaded["bianzhi"], "张工")

    def test_load_missing_key_returns_empty(self):
        self.assertEqual(load_all(TEST_KEY), {})

    def test_clear_all_removes_everything(self):
        save_all({"encoding": "gb18030", "bianzhi": "张工", "require_m06": "1"}, TEST_KEY)
        clear_all(TEST_KEY)
        self.assertEqual(load_all(TEST_KEY), {})

    def test_save_all_keeps_other_values_in_same_key(self):
        # save_all 只写传入的值名，不应破坏同一键下的其他值。
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as key:
            winreg.SetValueEx(key, "bianzhi", 0, winreg.REG_SZ, "测试")
        save_all({"require_m06": "1"}, TEST_KEY)
        loaded = load_all(TEST_KEY)
        self.assertEqual(loaded["require_m06"], "1")
        self.assertEqual(loaded["bianzhi"], "测试")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as key:
            self.assertEqual(str(winreg.QueryValueEx(key, "bianzhi")[0]), "测试")


if __name__ == "__main__":
    unittest.main()
