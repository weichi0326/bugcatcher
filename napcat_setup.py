"""Download and stage the official NapCat Windows OneKey installer.

The existing NapCat installation is never replaced.  The installer itself is
interactive; callers must report its process state separately from installation
success, then call :func:`installation_status` after it exits.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.request import Request, urlopen


RELEASE_API = "https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest"
ASSET_NAME = "NapCat.Shell.Windows.OneKey.zip"
INSTALL_DIR = Path("napcat") / "NapCat.Shell.Windows.OneKey"
RELEASE_MARKER = ".onekey_release.json"
MAX_API_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_EXTRACT_BYTES = 128 * 1024 * 1024
MAX_ZIP_ENTRIES = 256


class NapCatSetupError(RuntimeError):
    """A recoverable download, verification, or installation setup failure."""


def _notify(progress: Callable[[str], None] | None, message: str) -> None:
    if progress:
        progress(message)


def _request(url: str) -> Request:
    return Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "QQBot-WebUI-NapCat-Setup"})


def _install_root(project_root: Path) -> Path:
    project_root = Path(project_root).resolve()
    integration_path = project_root / "config" / "napcat_integration.json"
    try:
        integration = json.loads(integration_path.read_text(encoding="utf-8-sig"))
        configured = str(integration.get("napcat_root") or "").strip() if isinstance(integration, dict) else ""
    except (OSError, ValueError):
        configured = ""
    path = Path(configured) if configured else INSTALL_DIR
    return (path if path.is_absolute() else project_root / path).resolve()


def installation_status(project_root: Path) -> dict:
    """Detect a staged installer and an installed portable QQ/NapCat bundle."""
    install_root = _install_root(project_root)
    shells = []
    if install_root.is_dir():
        for child in install_root.iterdir():
            if child.is_dir() and re.fullmatch(r"NapCat\.\d+\.Shell", child.name, re.IGNORECASE):
                launcher = child / "NapCatWinBootMain.exe"
                qq = child / "QQ.exe"
                if launcher.is_file() and qq.is_file():
                    shells.append((child.name, child, launcher, qq))
    shells.sort(key=lambda item: int(item[0].split(".")[1]), reverse=True)
    selected = shells[0] if shells else None
    installer = install_root / "NapCatInstaller.exe"
    release_version = None
    marker = install_root / RELEASE_MARKER
    if marker.is_file():
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
            release_version = saved.get("version") if isinstance(saved, dict) else None
        except (OSError, ValueError):
            pass
    qq_version = None
    if selected:
        versions = selected[1] / "versions"
        if versions.is_dir():
            names = [item.name for item in versions.iterdir() if item.is_dir() and re.fullmatch(r"\d+\.\d+\.\d+-\d+", item.name)]
            if names:
                qq_version = sorted(names, key=lambda name: tuple(int(part) for part in name.replace("-", ".").split(".")), reverse=True)[0].replace("-", ".")
    return {
        "root": str(install_root),
        "installed": selected is not None,
        "staged": installer.is_file(),
        "shell_dir": str(selected[1]) if selected else None,
        "launcher": str(selected[2]) if selected else None,
        "qq_exe": str(selected[3]) if selected else None,
        "installer": str(installer) if installer.is_file() else None,
        "qq_version": qq_version,
        "release_version": release_version,
    }


def latest_onekey_release(*, timeout: int = 20) -> dict:
    """Read the official stable release and require its asset SHA256 digest."""
    try:
        with urlopen(_request(RELEASE_API), timeout=timeout) as response:
            raw = response.read(MAX_API_BYTES + 1)
        if len(raw) > MAX_API_BYTES:
            raise NapCatSetupError("NapCat 发布信息超出大小限制")
        release = json.loads(raw)
    except NapCatSetupError:
        raise
    except (OSError, ValueError) as exc:
        raise NapCatSetupError(f"无法获取 NapCat 官方发布信息：{exc}") from exc
    if not isinstance(release, dict):
        raise NapCatSetupError("NapCat 发布信息格式无效")
    tag = str(release.get("tag_name") or "")
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[.-][\w.-]+)?", tag):
        raise NapCatSetupError("NapCat 发布版本号无效")
    if release.get("draft") or release.get("prerelease"):
        raise NapCatSetupError("官方 latest 目前不是正式发布版")
    release_assets = release.get("assets")
    if not isinstance(release_assets, list):
        raise NapCatSetupError("NapCat 发布资源列表格式无效")
    assets = [item for item in release_assets if isinstance(item, dict) and item.get("name") == ASSET_NAME]
    if len(assets) != 1:
        raise NapCatSetupError("官方发布页缺少唯一的 Windows OneKey 安装包")
    asset = assets[0]
    url = str(asset.get("browser_download_url") or "")
    expected = f"https://github.com/NapNeko/NapCatQQ/releases/download/{tag}/{ASSET_NAME}"
    if url != expected:
        raise NapCatSetupError("安装包下载地址不是预期的 NapCat 官方发布地址")
    digest = str(asset.get("digest") or "")
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise NapCatSetupError("官方发布信息缺少可校验的 SHA256 摘要")
    try:
        size = int(asset.get("size"))
    except (TypeError, ValueError) as exc:
        raise NapCatSetupError("安装包大小无效") from exc
    if not 0 < size <= MAX_DOWNLOAD_BYTES:
        raise NapCatSetupError("安装包大小超出限制")
    return {"version": tag, "url": url, "sha256": digest.split(":", 1)[1].lower(), "size": size}


def _validate_zip(archive: zipfile.ZipFile) -> None:
    entries = archive.infolist()
    if not entries or len(entries) > MAX_ZIP_ENTRIES:
        raise NapCatSetupError("安装包文件数量异常")
    total = 0
    seen: set[str] = set()
    for info in entries:
        name = info.filename.replace("\\", "/")
        parts = name.rstrip("/").split("/")
        normalized = "/".join(parts).casefold()
        mode = (info.external_attr >> 16) & 0xFFFF
        if (not name or name.startswith("/") or
                any(part in ("", ".", "..") for part in parts) or
                any(":" in part for part in parts) or
                stat.S_ISLNK(mode)):
            raise NapCatSetupError(f"安装包包含不安全路径：{name}")
        if normalized in seen:
            raise NapCatSetupError(f"安装包包含重复路径：{name}")
        seen.add(normalized)
        total += info.file_size
        if total > MAX_EXTRACT_BYTES:
            raise NapCatSetupError("安装包解压后大小超出限制")
    if sum(PurePosixPath(item.filename.replace("\\", "/")).name.lower() == "napcatinstaller.exe"
           for item in entries if not item.is_dir()) != 1:
        raise NapCatSetupError("安装包中未找到唯一的 NapCatInstaller.exe")


def download_onekey(project_root: Path, progress: Callable[[str], None] | None = None) -> dict:
    """Download, verify, and stage OneKey in the configured NapCat path.

    No existing installed or partially staged directory is overwritten.  The
    official installer later downloads QQ and NapCat into this staged folder.
    """
    status = installation_status(project_root)
    if status["installed"]:
        return {"state": "installed", **status}
    if status["staged"]:
        return {"state": "staged", **status}
    destination = Path(status["root"])
    if destination.exists():
        raise NapCatSetupError(f"目标目录已存在但未识别出安装器或完整程序：{destination}；请手动检查，现有文件未改动")
    if os.name != "nt":
        raise NapCatSetupError("NapCat Windows OneKey 仅支持 Windows")

    release = latest_onekey_release()
    _notify(progress, f"官方版本 {release['version']}，开始下载 OneKey 安装器")
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".onekey-staging-", dir=parent) as temporary:
        temp_root = Path(temporary)
        archive_path = temp_root / ASSET_NAME
        digest = hashlib.sha256()
        downloaded = 0
        try:
            with urlopen(_request(release["url"]), timeout=60) as response, archive_path.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES or downloaded > release["size"]:
                        raise NapCatSetupError("下载数据超过官方标示大小或本地限制")
                    out.write(chunk)
                    digest.update(chunk)
        except NapCatSetupError:
            raise
        except OSError as exc:
            raise NapCatSetupError(f"NapCat 下载失败：{exc}") from exc
        if downloaded != release["size"] or digest.hexdigest() != release["sha256"]:
            raise NapCatSetupError("NapCat 下载大小或 SHA256 校验失败；没有修改现有安装")
        _notify(progress, "下载校验通过，正在检查压缩包")
        payload = temp_root / "payload"
        try:
            with zipfile.ZipFile(archive_path) as archive:
                _validate_zip(archive)
                archive.extractall(payload)
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise NapCatSetupError(f"无法解压 NapCat 安装器：{exc}") from exc
        installers = list(payload.rglob("NapCatInstaller.exe"))
        if len(installers) != 1:
            raise NapCatSetupError("解压后未找到唯一的 NapCatInstaller.exe")
        installer_parent = installers[0].parent
        # The OneKey archive normally has a root-level executable; accept one
        # wrapper directory but publish its contents at the configured root.
        if installer_parent != payload:
            if installer_parent.parent != payload:
                raise NapCatSetupError("安装器目录层级异常")
            publish = installer_parent
        else:
            publish = payload
        if destination.exists():
            raise NapCatSetupError("目标目录在下载期间已被创建，未覆盖已有文件")
        try:
            publish.rename(destination)
        except OSError as exc:
            raise NapCatSetupError(f"无法安放 NapCat 安装器：{exc}") from exc
        (destination / RELEASE_MARKER).write_text(json.dumps({"version": release["version"]}), encoding="utf-8")
    _notify(progress, "官方 OneKey 安装组件已就绪，准备启动安装程序")
    return {"state": "staged", "version": release["version"], "sha256": release["sha256"], **installation_status(project_root)}


def launch_installer(project_root: Path) -> subprocess.Popen:
    """Open the staged official GUI installer in its own directory.

    The returned process exit code is only the installer exit code.  Confirm a
    completed install with :func:`installation_status` after the process exits.
    """
    if os.name != "nt":
        raise NapCatSetupError("NapCat Windows OneKey 仅支持 Windows")
    status = installation_status(project_root)
    if status["installed"]:
        raise NapCatSetupError("NapCat 和 QQ 已安装，无需重复运行安装器")
    if not status["staged"]:
        raise NapCatSetupError("尚未下载 OneKey 安装器")
    installer = Path(status["installer"])
    try:
        # Keep the GUI visible so users can complete setup/UAC prompts.
        return subprocess.Popen([str(installer)], cwd=installer.parent)
    except OSError as exc:
        raise NapCatSetupError(f"无法启动 NapCatInstaller.exe：{exc}") from exc
