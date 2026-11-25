"""验证总览入口页的版本戳机制与 _hub/ 目录布局。

覆盖：
- 新目录第一次生成（无戳 → 全写）
- 同版本再导出（不重复写、不破坏 mtime）
- 版本升高（覆盖旧入口页与静态资源）
- 用户改过的入口页在同版本下被保留、在升版本时被覆盖（这是有意的取舍）
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import export as ex  # noqa: E402


class HubVersionStampTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "root"
        self.viewer = base / "viewer"
        for src_rel, _dst in ex.HUB_VIEWER_FILES:
            f = self.viewer / src_rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"/* {src_rel} v{ex.HUB_ASSET_VERSION} */", encoding="utf-8")
        self.project = base / "RepoA"

    def tearDown(self):
        self.tmp.cleanup()

    def _stamp_path(self):
        return self.root / ex.HUB_DIR / "version.json"

    def test_first_run_creates_layout(self):
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        self.assertTrue((self.root / "index.html").exists())
        for _src, dst in ex.HUB_VIEWER_FILES:
            if dst == "index.html":
                continue
            self.assertTrue((self.root / dst).exists(), dst)
        # 静态资源都在 _hub/ 下，不在项目目录旁边散着
        self.assertEqual(
            sorted(p.name for p in (self.root / ex.HUB_DIR).iterdir() if p.suffix != ".json"),
            ["app.css", "hub.css", "hub.js"],
        )

    def test_stamp_written_with_current_version(self):
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        data = ex.read_hub_version(self.root)
        self.assertEqual(data.get("version"), ex.HUB_ASSET_VERSION)

    def test_same_version_does_not_rewrite_files(self):
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        idx = self.root / "index.html"
        idx.write_text("USER EDIT", encoding="utf-8")
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        # 同版本：用户改动必须保留
        self.assertEqual(idx.read_text(encoding="utf-8"), "USER EDIT")

    def test_newer_version_overwrites_entry_and_assets(self):
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        idx = self.root / "index.html"
        idx.write_text("USER EDIT", encoding="utf-8")
        # 模拟"viewer 升级了"
        for src_rel, _dst in ex.HUB_VIEWER_FILES:
            (self.viewer / src_rel).write_text(f"/* {src_rel} v999 */", encoding="utf-8")
        ex.write_hub_root(self.root, self.viewer, "RepoA", asset_version=ex.HUB_ASSET_VERSION + 1)
        self.assertIn("v999", idx.read_text(encoding="utf-8"))
        self.assertEqual(ex.read_hub_version(self.root).get("version"), ex.HUB_ASSET_VERSION + 1)

    def test_legacy_dir_without_stamp_is_upgraded(self):
        # 老版本（无版本戳）的目录：视为过旧，直接覆盖
        (self.root / "hub").mkdir(parents=True)
        (self.root / "index.html").write_text("OLD", encoding="utf-8")
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        self.assertNotIn("OLD", (self.root / "index.html").read_text(encoding="utf-8"))
        self.assertTrue(self._stamp_path().exists())

    def test_order_merge_still_works_across_versions(self):
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        ex.write_hub_root(self.root, self.viewer, "RepoB")
        self.assertEqual(ex.read_hub_order(self.root / "repos.js"), ["RepoA", "RepoB"])

    def test_corrupt_stamp_treated_as_outdated(self):
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        self._stamp_path().write_text("{not json", encoding="utf-8")
        idx = self.root / "index.html"
        idx.write_text("USER EDIT", encoding="utf-8")
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        self.assertNotIn("USER EDIT", idx.read_text(encoding="utf-8"))

    def test_stamp_write_is_atomic_no_tmp_left(self):
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        leftovers = [p.name for p in (self.root / ex.HUB_DIR).iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
