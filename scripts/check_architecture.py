"""检查架构手册逐文件清单，覆盖 Git 文件与当前未忽略的新文件，排除已删除文件。"""
from __future__ import annotations

import pathlib
import re
import subprocess
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs" / "architecture.md"


def project_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return {name for name in result.stdout.decode("utf-8").split("\0")
            if name and (ROOT / name).is_file()}


def main() -> int:
    expected = project_files()
    content = MANUAL.read_text(encoding="utf-8")
    # 页面 URL 与忽略目录也使用表格，覆盖校验仅读取正式文件清单一节。
    inventory = content.split("## 5. 正式项目文件清单", 1)[1].split("## 6.", 1)[0]
    entries = re.findall(r"^\| `([^`]+)` \|", inventory, re.MULTILINE)
    actual = set(entries)
    missing = sorted(expected - actual)
    stale = sorted(actual - expected)
    duplicates = sorted(name for name, count in Counter(entries).items() if count > 1)
    for label, values in (("未覆盖", missing), ("过期或非项目文件", stale), ("重复", duplicates)):
        if values:
            print(f"{label}:\n" + "\n".join(f"  {name}" for name in values))
    if missing or stale or duplicates:
        return 1
    print(f"架构手册覆盖 {len(expected)}/{len(expected)} 个项目文件（100%），无重复或过期条目。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
