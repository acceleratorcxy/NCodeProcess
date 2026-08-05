import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ncodeprocess.preferences as preferences_module
from ncodeprocess.preferences import clear_all, load_all, save_all, storage_backend

# 使用独立的测试键，避免污染真实的 HKCU\Software\NCodeProcess。
TEST_KEY = r"Software\NCodeProcess_UnitTests"


@unittest.skipUnless(sys.platform == "win32", "注册表仅存在于 Windows")
class PreferencesTests(unittest.TestCase):
    def tearDown(self):
        clear_all(TEST_KEY)

    def test_save_and_load_roundtrip(self):
        save_all({"encoding": "gb18030", "require_m06": "1", "bianzhi": "张工"}, TEST_KEY)
        loaded = load_all(TEST_KEY)
        self.assertEqual(loaded["encoding"], "gb18030")
        self.assertEqual(loaded["require_m06"], "1")
        self.assertEqual(loaded["bianzhi"], "张工")

    def test_max_limits_vars_roundtrip(self):
        # WP-C1：文件大小/数量上限持久化 roundtrip。
        save_all({"max_file_size": "1048576", "max_files": "500"}, TEST_KEY)
        loaded = load_all(TEST_KEY)
        self.assertEqual(loaded.get("max_file_size"), "1048576")
        self.assertEqual(loaded.get("max_files"), "500")

    def test_retract_z_threshold_var_roundtrip(self):
        # WP-C9：抬刀高度阈值持久化 roundtrip。
        save_all({"retract_z_threshold": "20"}, TEST_KEY)
        self.assertEqual(load_all(TEST_KEY).get("retract_z_threshold"), "20")

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

    def test_clear_all_also_removes_legacy_key_values(self):
        # 旧版 NCPostProcess 键中的值必须随"清除注册表"一并删除，
        # 否则清除后下次启动旧值会复活。用补丁把当前键/遗留键指向测试键。
        with patch("ncodeprocess.preferences.KEY", TEST_KEY), \
             patch("ncodeprocess.preferences.LEGACY_KEYS", (TEST_KEY + "_Legacy",)):
            save_all({"bianzhi": "旧值"}, TEST_KEY + "_Legacy")
            save_all({"encoding": "gb18030"}, TEST_KEY)
            clear_all(TEST_KEY)
            self.assertEqual(load_all(TEST_KEY), {})
            self.assertEqual(load_all(TEST_KEY + "_Legacy"), {})


FILE_TEST_KEY = r"Software\NCodeProcess_UnitTests_File"


class FileBackendPreferencesTests(unittest.TestCase):
    """注册表不可写时回退到设置文件的后备存储测试（与平台无关）。"""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="ncodeprocess-prefs-")
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.appdata_dir = root / "appdata"
        self.home_dir = root / "home"
        # 指向隔离的临时目录，避免读写真实的 %APPDATA% 与用户主目录。
        self._env_patch = patch.dict(os.environ, {"APPDATA": str(self.appdata_dir)})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self._home_patch = patch.object(preferences_module.Path, "home", return_value=self.home_dir)
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)

    def tearDown(self):
        clear_all(FILE_TEST_KEY)

    @property
    def appdata_file(self):
        return self.appdata_dir / "NCodeProcess" / "NCodeProcess_UnitTests_File.json"

    @property
    def home_file(self):
        return self.home_dir / "NCodeProcess_UnitTests_File.json"

    @staticmethod
    def _unwritable_registry():
        return patch.object(preferences_module, "_registry_write_works", return_value=False)

    def test_save_falls_back_to_appdata_file_when_registry_unwritable(self):
        with self._unwritable_registry():
            backend, location = save_all({"encoding": "gb18030", "bianzhi": "张工"}, FILE_TEST_KEY)
        self.assertEqual(backend, "appdata")
        self.assertEqual(location, str(self.appdata_file))
        self.assertEqual(load_all(FILE_TEST_KEY), {"encoding": "gb18030", "bianzhi": "张工"})

    def test_save_all_keeps_other_values_in_file(self):
        with self._unwritable_registry():
            save_all({"encoding": "gb18030"}, FILE_TEST_KEY)
            save_all({"require_m06": "1"}, FILE_TEST_KEY)
        self.assertEqual(load_all(FILE_TEST_KEY)["encoding"], "gb18030")
        self.assertEqual(load_all(FILE_TEST_KEY)["require_m06"], "1")

    def test_save_explicit_appdata_backend_clears_other_backends(self):
        # 显式选择 appdata：值只写 appdata 文件，注册表与 home 文件被清空。
        save_all({"encoding": "gb18030", "require_m06": "1"}, FILE_TEST_KEY, backend="appdata")
        loaded = load_all(FILE_TEST_KEY)
        self.assertEqual(loaded.get("encoding"), "gb18030")
        self.assertEqual(loaded.get("storage_backend"), "appdata")
        self.assertFalse(self.home_file.exists())
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, FILE_TEST_KEY) as key:
                winreg.QueryValueEx(key, "encoding")
            self.fail("注册表不应残留 encoding")
        except FileNotFoundError:
            pass

    def test_switch_backend_to_registry_clears_files(self):
        # 从 appdata 切回注册表：appdata 文件被清空，值写入注册表。
        save_all({"encoding": "gb18030"}, FILE_TEST_KEY, backend="appdata")
        self.assertTrue(self.appdata_file.exists())
        save_all({"encoding": "utf-8"}, FILE_TEST_KEY, backend="registry")
        self.assertFalse(self.appdata_file.exists())
        loaded = load_all(FILE_TEST_KEY)
        self.assertEqual(loaded.get("encoding"), "utf-8")
        self.assertEqual(loaded.get("storage_backend"), "registry")

    def test_load_uses_selected_backend(self):
        # WP-R2：按存在性检测——注册表有残留配置时优先采用注册表。
        save_all({"encoding": "gb18030"}, FILE_TEST_KEY, backend="appdata")
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, FILE_TEST_KEY) as key:
            winreg.SetValueEx(key, "encoding", 0, winreg.REG_SZ, "cp1252")
        loaded = load_all(FILE_TEST_KEY)
        self.assertEqual(loaded.get("encoding"), "cp1252")

    def test_load_detects_backend_by_existing_config(self):
        # WP-R2：配置文件无 storage_backend 键时，按存在性检测定位（appdata 有值即用）。
        self.appdata_file.parent.mkdir(parents=True, exist_ok=True)
        self.appdata_file.write_text(json.dumps({"encoding": "gb18030"}), encoding="utf-8")
        loaded = load_all(FILE_TEST_KEY)
        self.assertEqual(loaded.get("encoding"), "gb18030")
        self.assertEqual(storage_backend(FILE_TEST_KEY)[0], "appdata")

    def test_clear_all_removes_file_settings(self):
        with self._unwritable_registry():
            save_all({"encoding": "gb18030"}, FILE_TEST_KEY)
        clear_all(FILE_TEST_KEY)
        self.assertEqual(load_all(FILE_TEST_KEY), {})

    def test_save_falls_back_to_home_when_appdata_unusable(self):
        # 让 %APPDATA% 指向一个普通文件，使 APPDATA 目录创建失败 → 回退用户主目录。
        blocker = Path(self._temp.name) / "appdata_blocker"
        blocker.write_text("occupied", encoding="utf-8")
        with patch.dict(os.environ, {"APPDATA": str(blocker)}):
            with self._unwritable_registry():
                backend, location = save_all({"encoding": "gb18030"}, FILE_TEST_KEY)
        self.assertEqual(backend, "home")
        self.assertEqual(location, str(self.home_file))
        self.assertEqual(load_all(FILE_TEST_KEY)["encoding"], "gb18030")

    def test_storage_backend_reports_file_when_registry_unwritable(self):
        with self._unwritable_registry():
            backend, location = storage_backend(FILE_TEST_KEY)
        self.assertEqual(backend, "appdata")
        self.assertEqual(location, str(self.appdata_file))

    def test_storage_backend_reports_registry_when_writable(self):
        with patch.object(preferences_module, "_registry_write_works", return_value=True):
            backend, location = storage_backend(FILE_TEST_KEY)
        self.assertEqual(backend, "registry")
        self.assertIsNone(location)


if __name__ == "__main__":
    unittest.main()
