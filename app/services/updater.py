"""GitHub Releases üzerinden otomatik güncelleme kontrolü ve indirme.

API: ``https://api.github.com/repos/<owner>/<repo>/releases/latest``

Beklenen JSON (kısaltılmış):
    {
      "tag_name": "v0.1.2",
      "name": "Borsa Bot 0.1.2",
      "body": "...markdown release notes...",
      "published_at": "2026-06-01T12:00:00Z",
      "assets": [
        {
          "name": "BorsaBot-Setup-0.1.2.exe",
          "browser_download_url": "https://github.com/.../BorsaBot-Setup-0.1.2.exe",
          "size": 207000000
        }
      ]
    }

Kullanım::

    checker = UpdateChecker()
    info = await checker.check_for_updates()
    if info and info.is_newer:
        path = await checker.download(info)
        checker.install(path)   # mevcut app kendini kapatır, installer açılır
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from app.__version__ import __repo__, __version__
from app.config import USER_DATA_DIR

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_UPDATE_URL = f"{GITHUB_API_BASE}/repos/{__repo__}/releases/latest"
UPDATES_DIR = USER_DATA_DIR / "updates"
UPDATES_DIR.mkdir(parents=True, exist_ok=True)

# Semver: MAJOR.MINOR.PATCH (öncesinde opsiyonel "v")
SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


# ---------------------------------------------------------------------------
# Veri modelleri
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpdateInfo:
    """Bir GitHub release'i temsil eder."""

    current_version: str
    latest_version: str
    release_name: str
    release_notes: str
    published_at: Optional[datetime]
    download_url: Optional[str]
    asset_name: Optional[str]
    asset_size_bytes: int = 0

    @property
    def is_newer(self) -> bool:
        """Yeni sürüm mevcut sürümden büyük mü?"""
        return _semver_tuple(self.latest_version) > _semver_tuple(self.current_version)

    @property
    def is_critical(self) -> bool:
        """release_notes içinde [CRITICAL] etiketi var mı?"""
        return "[CRITICAL]" in (self.release_notes or "").upper()


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _semver_tuple(version: str) -> tuple[int, int, int]:
    """'v0.1.2' / '0.1.2' → (0, 1, 2). Geçersizse (0, 0, 0)."""
    m = SEMVER_RE.match((version or "").strip())
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


class NoReleasesError(RuntimeError):
    """Repo'da henüz hiç release yayınlanmamış (HTTP 404)."""


def _http_get_json(url: str, timeout: float = 10.0) -> dict:
    """urllib ile GitHub API'sini çağır.

    Raises:
        NoReleasesError: 404 — bu repo'da hiç release yok
        RuntimeError: Diğer ağ/HTTP hataları
    """
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "BorsaBot-Updater",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NoReleasesError(
                "GitHub repo'sunda henüz hiç sürüm yayınlanmamış. "
                "İlk sürümü yayınlamak için: git tag v<sürüm> && git push --tags"
            ) from exc
        raise RuntimeError(f"GitHub API HTTP {exc.code}: {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"GitHub API'ye ulaşılamadı: {exc}") from exc


# ---------------------------------------------------------------------------
# Ana sınıf
# ---------------------------------------------------------------------------


class UpdateChecker:
    """GitHub Releases ile sürüm kontrol + indir + kur."""

    def __init__(self, url: Optional[str] = None, current_version: Optional[str] = None) -> None:
        self.url = url or os.environ.get("UPDATE_CHECK_URL") or DEFAULT_UPDATE_URL
        self.current_version = current_version or __version__

    # ------------------------------------------------------------------ check

    async def check_for_updates(self) -> Optional[UpdateInfo]:
        """En son release'i çek + sürüm karşılaştır. Hata olursa None."""
        import asyncio

        try:
            data = await asyncio.to_thread(_http_get_json, self.url)
        except NoReleasesError as exc:
            logger.info("Update check: {}", exc)
            # "Henüz sürüm yok" özel durumu — UI tarafında bilgi göster
            return UpdateInfo(
                current_version=self.current_version,
                latest_version=self.current_version,
                release_name="Henüz sürüm yayınlanmamış",
                release_notes=(
                    "Bu GitHub repo'sunda henüz hiç release yok.\n\n"
                    "İlk sürümü yayınlamak için terminal'de:\n"
                    "```\n"
                    f"git tag v{self.current_version}\n"
                    "git push --tags\n"
                    "```\n"
                    "GitHub Actions otomatik build edip Releases sayfasına yükler."
                ),
                published_at=None,
                download_url=None,
                asset_name=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Update check başarısız: {}", exc)
            return None

        tag = str(data.get("tag_name") or "").strip()
        if not tag:
            return None

        # En uygun installer asset'i seç (BorsaBot-Setup-*.exe)
        assets = data.get("assets") or []
        download_url: Optional[str] = None
        asset_name: Optional[str] = None
        asset_size: int = 0
        for asset in assets:
            name = str(asset.get("name") or "")
            if name.lower().endswith(".exe") and "setup" in name.lower():
                download_url = str(asset.get("browser_download_url") or "")
                asset_name = name
                asset_size = int(asset.get("size") or 0)
                break

        published_at: Optional[datetime] = None
        pub_str = data.get("published_at")
        if pub_str:
            try:
                published_at = datetime.fromisoformat(str(pub_str).replace("Z", "+00:00"))
            except ValueError:
                published_at = None

        return UpdateInfo(
            current_version=self.current_version,
            latest_version=tag.lstrip("v"),
            release_name=str(data.get("name") or tag),
            release_notes=str(data.get("body") or ""),
            published_at=published_at,
            download_url=download_url,
            asset_name=asset_name,
            asset_size_bytes=asset_size,
        )

    # ------------------------------------------------------------------ download

    async def download(self, info: UpdateInfo, progress=None) -> Path:
        """Installer dosyasını ``UPDATES_DIR``'a indir.

        ``progress`` (opsiyonel): ``Callable[[int, int], None]`` → (downloaded, total)
        """
        if not info.download_url or not info.asset_name:
            raise RuntimeError("İndirme URL'i yok — release asset bulunamadı.")

        import asyncio
        import urllib.error
        import urllib.request

        target = UPDATES_DIR / info.asset_name
        if target.exists() and target.stat().st_size == info.asset_size_bytes:
            logger.info("İndirme atlandı (zaten mevcut): {}", target)
            return target

        def _do_download() -> Path:
            req = urllib.request.Request(
                info.download_url,
                headers={"User-Agent": "BorsaBot-Updater"},
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    total = int(resp.headers.get("Content-Length") or info.asset_size_bytes or 0)
                    downloaded = 0
                    chunk = 64 * 1024
                    with open(target, "wb") as f:
                        while True:
                            data = resp.read(chunk)
                            if not data:
                                break
                            f.write(data)
                            downloaded += len(data)
                            if progress:
                                try:
                                    progress(downloaded, total)
                                except Exception:  # noqa: BLE001
                                    pass
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                if target.exists():
                    target.unlink(missing_ok=True)
                raise RuntimeError(f"İndirme hatası: {exc}") from exc
            return target

        return await asyncio.to_thread(_do_download)

    # ------------------------------------------------------------------ install

    @staticmethod
    def install_via_helper(installer_path: Path) -> None:
        """Helper PowerShell script ile bağımsız kurulum + restart akışı.

        Akış:
          1. Bu fonksiyon PowerShell helper'ı başlatır (bağımsız process).
          2. Helper bizim PID'imizi bekler — uygulama kapanır.
          3. Helper installer'ı /VERYSILENT çalıştırır (UAC popup).
          4. Kurulum bitince yeni BorsaBot.exe'yi başlatır.
          5. Bu fonksiyon QApplication.quit() ile uygulamayı kapatır.

        Inno Setup'ın kendi /RESTARTAPPLICATIONS flag'ine bel bağlamak
        yerine bu pattern daha güvenilir — helper Python tarafından
        kontrol edilir, başarısız olursa user'a görünür hata bırakır.
        """
        import os
        import tempfile

        if not installer_path.exists():
            raise FileNotFoundError(f"Installer yok: {installer_path}")

        # Kurulu BorsaBot.exe'nin yolu — PyInstaller bundle ise sys.executable
        if getattr(sys, "frozen", False):
            app_exe = Path(sys.executable)
        else:
            # Dev modda Python script çalıştırılıyor — sys.executable Python
            # yorumlayıcısı. Bu durumda helper'ı dummy çalıştır (no-op restart).
            app_exe = Path(sys.executable)

        # Helper script — runtime'da %TEMP%'e yazılır
        helper_script = r'''
param(
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$InstallerPath,
    [Parameter(Mandatory=$true)][string]$AppExe
)

$log = "$env:TEMP\borsabot_update_helper.log"
"[{0}] Helper başladı. ParentPid={1}" -f (Get-Date -Format "HH:mm:ss"), $ParentPid | Out-File $log -Append

# 1) Parent process (BorsaBot) kapanmasını bekle
try {
    Wait-Process -Id $ParentPid -Timeout 30 -ErrorAction Stop
    "[{0}] BorsaBot kapandı." -f (Get-Date -Format "HH:mm:ss") | Out-File $log -Append
} catch {
    "[{0}] Parent process timeout veya bulunamadı, devam ediliyor." -f (Get-Date -Format "HH:mm:ss") | Out-File $log -Append
}

# Kalan child process'leri zorla kapat (Qt WebEngine vb.)
taskkill /F /IM BorsaBot.exe /T 2>$null | Out-Null
taskkill /F /IM QtWebEngineProcess.exe 2>$null | Out-Null
Start-Sleep -Milliseconds 1500

# 2) Installer'ı UAC ile başlat ve bekle
"[{0}] Installer başlatılıyor: $InstallerPath" -f (Get-Date -Format "HH:mm:ss") | Out-File $log -Append
try {
    $proc = Start-Process -FilePath $InstallerPath `
        -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/LOG=$env:TEMP\borsabot_install.log" `
        -Verb RunAs -PassThru -Wait
    "[{0}] Installer exit code: $($proc.ExitCode)" -f (Get-Date -Format "HH:mm:ss") | Out-File $log -Append
} catch {
    "[{0}] Installer hata: $_" -f (Get-Date -Format "HH:mm:ss") | Out-File $log -Append
    exit 1
}

# 3) Yeni BorsaBot.exe başlat
Start-Sleep -Milliseconds 1500
if (Test-Path $AppExe) {
    "[{0}] Yeni sürüm açılıyor: $AppExe" -f (Get-Date -Format "HH:mm:ss") | Out-File $log -Append
    Start-Process -FilePath $AppExe
} else {
    "[{0}] AppExe bulunamadı: $AppExe" -f (Get-Date -Format "HH:mm:ss") | Out-File $log -Append
}
"[{0}] Helper bitti." -f (Get-Date -Format "HH:mm:ss") | Out-File $log -Append
'''

        helper_path = Path(tempfile.gettempdir()) / "borsabot_update_helper.ps1"
        helper_path.write_text(helper_script, encoding="utf-8")

        my_pid = os.getpid()
        logger.info(
            "install_via_helper: pid={}, installer={}, app_exe={}, helper={}",
            my_pid, installer_path, app_exe, helper_path,
        )

        # Helper'ı bağımsız (detached) process olarak başlat —
        # bu uygulama kapansa bile yaşamaya devam etsin.
        try:
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy", "Bypass",
                    "-WindowStyle", "Hidden",
                    "-File", str(helper_path),
                    "-ParentPid", str(my_pid),
                    "-InstallerPath", str(installer_path),
                    "-AppExe", str(app_exe),
                ],
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0),
                close_fds=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Helper başlatılamadı: {exc}") from exc

        # Kısa gecikme — helper subprocess oluşsun, sonra app.quit().
        # Bu süre helper'ın "Wait-Process" çağrısına ulaşmasına yeter.
        import time
        time.sleep(1.0)

        logger.info("Helper başlatıldı, uygulama kapanıyor.")
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.quit()
        except Exception:  # noqa: BLE001
            sys.exit(0)

    @staticmethod
    def install(installer_path: Path, silent: bool = True) -> None:
        """İndirilen installer'ı çalıştır + mevcut uygulamayı kapat.

        Varsayılan tamamen sessiz mod: kullanıcı UI görmez, kurulum
        arka planda yapılır, bittiğinde yeni sürüm otomatik başlar.

        Inno Setup CLI flag'leri:
          /VERYSILENT         → hiçbir pencere/progress göstermez
          /SUPPRESSMSGBOXES   → kalan tüm onay diyaloglarını bastır
          /CLOSEAPPLICATIONS  → kullanılmakta olan exe'leri kapat
          /RESTARTAPPLICATIONS → kurulum sonrası exe'yi tekrar başlat
          /NORESTART          → Windows reboot dialog'u sormasın
        """
        if not installer_path.exists():
            raise FileNotFoundError(f"Installer yok: {installer_path}")

        args: list[str] = [str(installer_path)]
        if silent:
            args += [
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/CLOSEAPPLICATIONS",
                "/RESTARTAPPLICATIONS",
                "/NORESTART",
            ]

        # Yeni process başlat — mevcut süreçten bağımsız
        try:
            subprocess.Popen(
                args,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0),
                close_fds=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Installer başlatılamadı: {exc}") from exc

        # Mevcut app'i kapat (installer'ın işine yarar — exe locked olmaz)
        logger.info("Yeni sürüm installer'ı başlatıldı, uygulama kapanıyor: {}", installer_path)
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.quit()
        except Exception:  # noqa: BLE001
            sys.exit(0)


__all__ = ["UpdateChecker", "UpdateInfo", "UPDATES_DIR", "DEFAULT_UPDATE_URL"]
