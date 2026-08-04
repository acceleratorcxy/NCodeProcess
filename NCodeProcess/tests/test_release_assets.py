import unittest
from pathlib import Path

import ncodeprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReleaseAssetTests(unittest.TestCase):
    def test_windows_version_resource_matches_package_version(self):
        version_resource = PROJECT_ROOT / "version_info.txt"
        self.assertTrue(version_resource.is_file())
        content = version_resource.read_text(encoding="utf-8")
        self.assertIn("filevers=(1, 0, 0, 0)", content)
        self.assertIn("prodvers=(1, 0, 0, 0)", content)
        self.assertIn("StringStruct('ProductName', 'NCodeProcess')", content)
        self.assertIn("StringStruct('InternalName', 'NCodeProcess')", content)
        self.assertIn("StringStruct('OriginalFilename', 'NCodeProcess.exe')", content)
        self.assertIn("StringStruct('FileDescription', 'NCodeProcess NC Program Processing Tool')", content)

    def test_build_configuration_packages_version_metadata_without_cleaning_all_dist(self):
        spec = (PROJECT_ROOT / "NCodeProcess.spec").read_text(encoding="utf-8")
        build_script = (PROJECT_ROOT / "build_portable.ps1").read_text(encoding="utf-8")
        self.assertIn('version=os.path.join(project_root, "version_info.txt")', spec)
        self.assertTrue((PROJECT_ROOT / "VERSION.txt").is_file())
        self.assertEqual((PROJECT_ROOT / "VERSION.txt").read_text(encoding="utf-8").strip(), f"NCodeProcess {ncodeprocess.__version__}")
        self.assertIn("'VERSION.txt'", build_script)
        self.assertIn("Join-Path $dist 'NCodeProcess.exe'", build_script)
        self.assertIn("Join-Path $dist 'NCodeProcess-Package'", build_script)
        self.assertIn("Join-Path $dist 'NCodeProcess-Windows7-Portable.zip'", build_script)

