# -*- coding: utf-8 -*-
"""
精简项目内 NapCat 安装包：仅保留启动、QQ 登录、群消息 OneBot 转发所需文件。

用法（项目根）：
  python scripts/prune_napcat_install.py           # 默认：safe + bot 档位
  python scripts/prune_napcat_install.py --dry-run
  python scripts/prune_napcat_install.py --profile safe

恢复：重新运行 python scripts/migrate_napcat_to_project.py（从 Downloads 源目录复制）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from botskill.napcat_paths import resolve_napcat_root  # noqa: E402

INTEGRATION_PATH = ROOT / "config" / "napcat_integration.json"

# 相对 napcat_root（一键包根目录）
ROOT_JUNK = [
    "NapCat.Shell.zip",
    "NapCatInstaller.exe",
    "7z.exe",
    "7z.dll",
    "QQ.exe",
    "bootmain",
]

# 相对 NapCat.44498.Shell
SHELL_JUNK = [
    "napcat.bat",
    "napcat.quick.bat",
    "napcat.kill.qq.bat",
    "ReadMe.txt",
    "beacon_report.log",
]

# 相对 versions/<ver>/（QQ NT 运行时）
VERSION_JUNK = [
    "LICENSES.chromium.html",
    "LICENSE.electron.txt",
]

# 相对 versions/<ver>/resources/app — 与群文字消息 / 登录无关的 QQ 组件
APP_BOT_JUNK = [
    "wmpfsdk",
    "QQScreenShot",
    "miniapp",
    "remoting_host.exe",
    "libremoting.dll",
    "dbgeng.dll",
    "dbghelp.dll",
    "ppapi_player.plugin",
    "CompatibilityCheck.exe",
    "Timwp.exe",
    "PlayerShader",
    "package.json.bak",
]

# 相对 versions/<ver>/resources/app/napcat
NAPCAT_JUNK = [
    "static",
    "quickLoginExample.bat",
    "KillQQ.bat",
    "launcher-win10.bat",
    "launcher.bat",
    "launcher-user.bat",
    "launcher-win10-user.bat",
    os.path.join("plugins", "napcat-plugin-builtin", "webui"),
]

NODE_PRUNE_DIR_NAMES = {
    "test",
    "tests",
    "__tests__",
    "docs",
    "doc",
    "example",
    "examples",
    "coverage",
    ".github",
}
NODE_PRUNE_FILE_SUFFIXES = (".md", ".map", ".ts", ".flow")
NODE_PRUNE_FILE_NAMES = {
    "CHANGELOG",
    "CHANGELOG.md",
    "LICENSE",
    "LICENSE.md",
    "README",
    "README.md",
}


def load_integration() -> dict:
    with open(INTEGRATION_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def find_shell_dir(napcat_root: Path) -> Path:
    shell = napcat_root / "NapCat.44498.Shell"
    if not shell.is_dir():
        raise FileNotFoundError(f"未找到 NapCat Shell：{shell}")
    return shell


def find_version_dir(shell: Path) -> Path:
    versions_root = shell / "versions"
    versions = sorted(
        [d for d in versions_root.iterdir() if d.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )
    if not versions:
        raise FileNotFoundError(f"versions 为空：{versions_root}")
    return versions[0]


def iter_other_uin_configs(cfg_dir: Path, keep_uin: str) -> Iterable[Path]:
    if not cfg_dir.is_dir():
        return
    pat = re.compile(r"^(onebot11|napcat|napcat_protocol)_(\d+)\.json$", re.I)
    for p in cfg_dir.iterdir():
        if not p.is_file():
            continue
        m = pat.match(p.name)
        if m and m.group(2) != keep_uin:
            yield p


def prune_node_modules(nm_root: Path, dry_run: bool) -> Tuple[int, int]:
    """删除 node_modules 内文档/测试/源码映射等，保留 .js 运行文件。"""
    removed_files = 0
    freed = 0
    if not nm_root.is_dir():
        return 0, 0

    for dirpath, dirnames, filenames in os.walk(nm_root, topdown=False):
        for name in filenames:
            fp = Path(dirpath) / name
            rel = fp.relative_to(nm_root)
            parts = rel.parts
            if any(p in NODE_PRUNE_DIR_NAMES for p in parts):
                sz = fp.stat().st_size
                if not dry_run:
                    fp.unlink(missing_ok=True)
                removed_files += 1
                freed += sz
                continue
            if name in NODE_PRUNE_FILE_NAMES or name.endswith(NODE_PRUNE_FILE_SUFFIXES):
                if name.endswith(".ts") and (fp.with_suffix(".js").exists() or fp.with_suffix(".mjs").exists()):
                    sz = fp.stat().st_size
                    if not dry_run:
                        fp.unlink(missing_ok=True)
                    removed_files += 1
                    freed += sz
        for name in list(dirnames):
            dp = Path(dirpath) / name
            if name in NODE_PRUNE_DIR_NAMES:
                sz = sum(f.stat().st_size for f in dp.rglob("*") if f.is_file())
                if not dry_run:
                    shutil.rmtree(dp, ignore_errors=True)
                removed_files += 1
                freed += sz

    return removed_files, freed


def remove_path(path: Path, dry_run: bool) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        sz = path.stat().st_size
        if not dry_run:
            path.unlink()
        return sz
    sz = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    if not dry_run:
        shutil.rmtree(path, ignore_errors=True)
    return sz


def disable_webui(cfg_dir: Path, dry_run: bool) -> None:
    webui = cfg_dir / "webui.json"
    if not webui.is_file():
        return
    with open(webui, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return
    if data.get("disableWebUI") is True:
        return
    data["disableWebUI"] = True
    if not dry_run:
        with open(webui, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.write("\n")


def collect_targets(
    napcat_root: Path,
    shell: Path,
    version: Path,
    profile: str,
    keep_uin: str,
) -> List[Path]:
    app = version / "resources" / "app"
    napcat_app = app / "napcat"
    cfg_dir = napcat_app / "config"
    targets: List[Path] = []

    if profile in ("safe", "bot", "all"):
        targets.extend(napcat_root / p for p in ROOT_JUNK)
        targets.extend(shell / p for p in SHELL_JUNK)
        targets.extend(version / p for p in VERSION_JUNK)
        targets.extend(iter_other_uin_configs(cfg_dir, keep_uin))

    if profile in ("bot", "all"):
        targets.extend(app / p for p in APP_BOT_JUNK)
        targets.extend(napcat_app / p for p in NAPCAT_JUNK)

    return targets


def dir_size(path: Path) -> float:
    if not path.exists():
        return 0.0
    if path.is_file():
        return path.stat().st_size / (1024 * 1024)
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / (1024 * 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description="精简 NapCat 安装目录")
    parser.add_argument(
        "--profile",
        choices=("safe", "bot", "all"),
        default="bot",
        help="safe=仅安装包冗余；bot=safe+无 WebUI/小程序/截图等（默认，适合群聊分析机器人）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印将删除项，不实际删除")
    args = parser.parse_args()

    integration = load_integration()
    napcat_root = Path(resolve_napcat_root(integration=integration))
    try:
        from botskill.napcat_bridge import bridge_settings_from

        keep_uin = bridge_settings_from(integration=integration).preferred_uin
    except Exception:
        keep_uin = str(integration.get("qq_uin") or "").strip()
    shell = find_shell_dir(napcat_root)
    version = find_version_dir(shell)
    cfg_dir = version / "resources" / "app" / "napcat" / "config"

    before_mb = dir_size(napcat_root)
    targets = collect_targets(napcat_root, shell, version, args.profile, keep_uin)

    freed = 0
    removed = 0
    print(f"NapCat 根目录：{napcat_root}")
    print(f"精简档位：{args.profile}  dry_run={args.dry_run}")
    print(f"保留 QQ 号配置：{keep_uin or '(未配置)'}\n")

    for t in targets:
        if not t.exists():
            continue
        sz = remove_path(t, args.dry_run)
        try:
            rel = t.relative_to(napcat_root)
        except ValueError:
            rel = t
        print(f"  - {rel}  ({sz / 1024 / 1024:.2f} MB)")
        freed += sz
        removed += 1

    if args.profile in ("bot", "all"):
        nm = version / "resources" / "app" / "napcat" / "node_modules"
        nf, ns = prune_node_modules(nm, args.dry_run)
        if nf:
            print(f"  - node_modules 文档/测试等  ({ns / 1024 / 1024:.2f} MB, {nf} 项)")
            freed += ns

        disable_webui(cfg_dir, args.dry_run)
        if not args.dry_run:
            print("  - webui.json → disableWebUI: true")

    after_mb = before_mb if args.dry_run else dir_size(napcat_root)
    saved = before_mb - after_mb if not args.dry_run else freed / (1024 * 1024)

    print(f"\n删除项：{removed}  预计释放：{freed / 1024 / 1024:.1f} MB")
    if not args.dry_run:
        print(f"精简前：{before_mb:.1f} MB → 精简后：{after_mb:.1f} MB（约省 {saved:.1f} MB）")
        print("\n请关闭旧 NapCat 后，在 WebUI 概览页点击“启动全部”验证登录与群消息。")
    else:
        print("\n确认无误后去掉 --dry-run 执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
