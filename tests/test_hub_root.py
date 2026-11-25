"""导出侧单测：write_hub_root / read_hub_order。

直接用 Python 标准库 unittest 跑，不联网、不依赖 PyQt。
运行：PYTHONIOENCODING=utf-8 python -m unittest tests.test_hub_root -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import export as ex  # noqa: E402


class ReadHubOrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_returns_empty(self):
        self.assertEqual(ex.read_hub_order(self.dir / "nope.js"), [])

    def test_reads_written_file(self):
        p = self.dir / "repos.js"
        p.write_text(
            'window.GH_HUB = window.GH_HUB || {};\n'
            'window.GH_HUB.order = [\n  "A",\n  "B"\n];\n',
            encoding="utf-8",
        )
        self.assertEqual(ex.read_hub_order(p), ["A", "B"])

    def test_corrupt_file_returns_empty(self):
        p = self.dir / "repos.js"
        p.write_text("window.GH_HUB.order=[ 坏掉的", encoding="utf-8")
        self.assertEqual(ex.read_hub_order(p), [])

    def test_non_list_returns_empty(self):
        p = self.dir / "repos.js"
        p.write_text('window.GH_HUB.order="A";', encoding="utf-8")
        self.assertEqual(ex.read_hub_order(p), [])

    def test_filters_non_string_items(self):
        p = self.dir / "repos.js"
        p.write_text('window.GH_HUB.order=["A",1,null,"B"];', encoding="utf-8")
        self.assertEqual(ex.read_hub_order(p), ["A", "B"])


class WriteHubRootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "hubroot"
        self.viewer = Path(self.tmp.name) / "viewer"
        (self.viewer / "hub").mkdir(parents=True)
        (self.viewer / "hub" / "index.html").write_text("<!doctype html><title>hub</title>", encoding="utf-8")
        (self.viewer / "hub" / "hub.css").write_text("/*css*/", encoding="utf-8")
        (self.viewer / "hub" / "hub.js").write_text("/*js*/", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_entry_files_and_manifest(self):
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        self.assertTrue((self.root / "index.html").exists())
        self.assertTrue((self.root / "hub" / "hub.css").exists())
        self.assertTrue((self.root / "hub" / "hub.js").exists())
        self.assertEqual(ex.read_hub_order(self.root / "repos.js"), ["RepoA"])

    def test_appends_second_repo_without_losing_first(self):
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        ex.write_hub_root(self.root, self.viewer, "RepoB")
        self.assertEqual(ex.read_hub_order(self.root / "repos.js"), ["RepoA", "RepoB"])

    def test_same_repo_twice_not_duplicated(self):
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        self.assertEqual(ex.read_hub_order(self.root / "repos.js"), ["RepoA"])

    def test_does_not_overwrite_user_edited_index(self):
        self.root.mkdir(parents=True)
        (self.root / "index.html").write_text("USER EDIT", encoding="utf-8")
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        self.assertEqual((self.root / "index.html").read_text(encoding="utf-8"), "USER EDIT")

    def test_does_not_overwrite_user_edited_css(self):
        (self.root / "hub").mkdir(parents=True)
        (self.root / "hub" / "hub.css").write_text("USER CSS", encoding="utf-8")
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        self.assertEqual((self.root / "hub" / "hub.css").read_text(encoding="utf-8"), "USER CSS")

    def test_no_temp_file_left_behind(self):
        ex.write_hub_root(self.root, self.viewer, "RepoA")
        leftovers = [p.name for p in self.root.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_nested_folder_kept_as_posix_path(self):
        ex.write_hub_root(self.root, self.viewer, "group/RepoA")
        self.assertEqual(ex.read_hub_order(self.root / "repos.js"), ["group/RepoA"])

    def test_backslashes_normalized(self):
        ex.write_hub_root(self.root, self.viewer, "group\\RepoA")
        self.assertEqual(ex.read_hub_order(self.root / "repos.js"), ["group/RepoA"])

    def test_empty_folder_name_not_registered(self):
        ex.write_hub_root(self.root, self.viewer, "")
        self.assertEqual(ex.read_hub_order(self.root / "repos.js"), [])

    def test_manifest_is_valid_js_literal(self):
        ex.write_hub_root(self.root, self.viewer, 'weird"name</script>')
        text = (self.root / "repos.js").read_text(encoding="utf-8")
        # 不得出现裸 </script>，否则嵌进 HTML 会提前闭合
        self.assertNotIn("</script>", text)
        self.assertEqual(ex.read_hub_order(self.root / "repos.js"), ['weird"name</script>'])

    def test_missing_viewer_file_raises(self):
        (self.viewer / "hub" / "hub.js").unlink()
        with self.assertRaises(FileNotFoundError):
            ex.write_hub_root(self.root, self.viewer, "RepoA")

    def test_preserves_order_across_many(self):
        names = [f"Repo{i}" for i in range(10)]
        for n in names:
            ex.write_hub_root(self.root, self.viewer, n)
        self.assertEqual(ex.read_hub_order(self.root / "repos.js"), names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
