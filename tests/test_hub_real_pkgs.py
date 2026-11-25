"""端到端：用已有真实镜像包验证「登记 → 总览页能读到」这条链路。

不联网：把现有包复制到临时根目录，再直接调 write_hub_root 登记，
然后断言清单内容与文件布局。
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import export as ex  # noqa: E402

# 真实镜像包的位置会随使用者的目录习惯变化（本机就从各项目目录收拢到了
# D:\_TOOLS\Mirrors）。这里按候选列表探测，一个都找不到才跳过。
_CANDIDATES = [
    "D:/_TOOLS/Mirrors",
    "D:/_TOOLS/Csv_reader",
    "D:/_TOOLS/Mirror_export",
]


def _discover_real_pkgs(limit=2):
    """在候选根目录下找含 data/meta.js 的镜像包，取前 limit 个。"""
    found = []
    for root in _CANDIDATES:
        base = Path(root)
        if not base.is_dir():
            continue
        for p in sorted(base.iterdir()):
            if len(found) >= limit:
                return found
            if (p / "data" / "meta.js").exists():
                found.append(p)
    return found


REAL_PKGS = _discover_real_pkgs()


class RealPackageRegistrationTest(unittest.TestCase):
    @unittest.skipUnless(all(p.exists() for p in REAL_PKGS), "本机没有真实镜像包")
    def test_register_two_real_packages(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "hubroot"
            root.mkdir()
            names = []
            for pkg in REAL_PKGS:
                dst = root / pkg.name
                shutil.copytree(pkg, dst)
                ex.write_hub_root(root, ROOT / "viewer", pkg.name)
                names.append(pkg.name)

            # 1) 清单按登记顺序记录两个项目
            self.assertEqual(ex.read_hub_order(root / "repos.js"), names)

            # 2) 入口页与静态资源齐备
            for rel in ("index.html", "hub/hub.css", "hub/hub.js"):
                self.assertTrue((root / rel).exists(), rel)

            # 3) 清单是合法 JS 赋值（能按 data/*.js 的同一套约定解析）
            text = (root / "repos.js").read_text(encoding="utf-8")
            self.assertIn("window.GH_HUB", text)
            body = text[text.index("window.GH_HUB.order"):]
            body = body[body.index("=") + 1:].strip().rstrip(";")
            self.assertEqual(json.loads(body), names)

            # 4) 总览页要读的 data 文件确实在各自目录下
            for name in names:
                for d in ("meta", "prs", "issues"):
                    self.assertTrue((root / name / "data" / f"{d}.js").exists(), f"{name}/{d}")

    @unittest.skipUnless(all(p.exists() for p in REAL_PKGS), "本机没有真实镜像包")
    def test_re_register_keeps_existing_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "hubroot"
            root.mkdir()
            for pkg in REAL_PKGS:
                shutil.copytree(pkg, root / pkg.name)
            ex.write_hub_root(root, ROOT / "viewer", REAL_PKGS[0].name)
            ex.write_hub_root(root, ROOT / "viewer", REAL_PKGS[1].name)
            # 再登记一次第一个：不得改变顺序、不得重复
            ex.write_hub_root(root, ROOT / "viewer", REAL_PKGS[0].name)
            self.assertEqual(
                ex.read_hub_order(root / "repos.js"),
                [p.name for p in REAL_PKGS],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
