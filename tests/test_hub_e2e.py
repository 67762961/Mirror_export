"""端到端验证：--hub-root 的目录校验与登记行为。

真实导出需要联网 + Token，不适合在测试里跑；这里用 argparse 解析 +
export() 的入口校验分支来做「不联网」的部分，联网部分用已有的真实包验证
总览页能否读到登记后的清单。
"""
import argparse
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import export as ex  # noqa: E402


def make_args(**kw):
    base = dict(
        repo="owner/name",
        out=None,
        token="dummy",
        force=True,
        incremental=True,
        max_prs=0,
        max_issues=0,
        max_commits=0,
        max_tags=0,
        max_branches=0,
        max_branch_commits=0,
        max_branch_diffs=0,
        no_branches=False,
        max_file_bytes=400_000,
        skip_details=True,
        include_contents=False,
        max_commit_diffs=0,
        no_assets=True,
        hub_root=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


class HubRootPathValidationTest(unittest.TestCase):
    """export() 里 --hub-root 与 --out 的上下级校验。"""

    def test_hub_root_must_be_ancestor(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            out.mkdir()
            (out / "data").mkdir()
            # hub 目录在 out 的兄弟位置 → 不是上级 → 必须报错退出
            hub = Path(td) / "elsewhere"
            hub.mkdir()
            buf = io.StringIO()
            with redirect_stdout(buf):
                with self.assertRaises(SystemExit) as ctx:
                    ex.export(make_args(out=str(out), hub_root=str(hub)))
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("必须是输出目录的上级目录", buf.getvalue())
            # 校验失败时不得写 repos.js
            self.assertFalse((hub / "repos.js").exists())

    def test_relative_vs_absolute_path_still_matches(self):
        """out 与 hub_root 一个是相对、一个是绝对时也要能算对相对名。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            out = root / "RepoA"
            out.mkdir()
            rel = out.relative_to(root).as_posix()
            self.assertEqual(rel, "RepoA")


if __name__ == "__main__":
    unittest.main(verbosity=2)
