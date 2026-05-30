"""PyInstaller paketleme — Borsa Bot için onedir (klasör) build üretir.

Onedir tercih ediyoruz:
- Inno Setup ile bütün klasörü bir installer'a koyabiliriz
- Açılış hızı onefile'a göre çok daha hızlı (temp'e açma yok)
- Antivirus false-positive daha az

Kullanım:
    python scripts\\build_exe.py            # release build
    python scripts\\build_exe.py --debug    # konsol penceresi ile
    python scripts\\build_exe.py --clean    # önce dist/build temizle
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "app" / "main.py"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "BorsaBot.spec"
APP_NAME = "BorsaBot"

# Hidden imports — PyInstaller bunları otomatik bulamayabilir
HIDDEN_IMPORTS = [
    # Qt katmanları
    "PySide6.QtNetwork",
    "PySide6.QtSvg",
    "PySide6.QtPrintSupport",
    "PySide6.QtWebEngineWidgets",
    # Grafik
    "pyqtgraph.colors",
    "pyqtgraph.exporters",
    # SQLAlchemy dialect'leri
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.postgresql",
    "sqlalchemy.dialects.postgresql.psycopg2",
    # asyncio
    "asyncio",
    "aiosqlite",
    # Veri kaynakları — lazy-import edilen 3rd party modüller
    "feedparser",          # RSS haber kaynağı (BUNSUZ haber çekmez)
    "yfinance",            # yfinance source
    "pandas_datareader",   # stooq source
    "isyatirimhisse",      # BIST kaynak
    "bs4",                 # beautifulsoup4 (kap_source, investing_source)
    "lxml",                # BS4 parser
    "html.parser",         # BS4 fallback parser
    # Lazy import edilen iç modüller
    "app.db.session",
    "app.db.models",
    "app.services.scheduler",
    "app.data.sources.rss_source",
    "app.data.sources.yfinance_source",
    "app.data.sources.stooq_source",
    "app.data.sources.isyatirim_source",
    "app.data.sources.kap_source",
    "app.data.sources.investing_source",
    "app.data.sources.alphavantage_source",
    "app.data.sources.tradingview_source",
    "scripts.seed_instruments",
]

# Excludes — gereksiz büyük paketler
EXCLUDE_MODULES = [
    "tkinter",
    "matplotlib",
    "test",
    "tests",
    "unittest",
    "pytest",
    "torch",         # opsiyonel sentiment için; istek üzerine kaldırılabilir
    "transformers",  # aynı
    "scipy",         # vectorbt için lazım olmadıkça
    "IPython",
    "notebook",
]

# Eklenecek data dosyaları (script'ler — seed için)
DATAS: list[tuple[str, str]] = [
    # (src_relative_to_root, dest_in_bundle)
    ("scripts/seed_instruments.py", "scripts"),
    ("scripts/setup_db.py", "scripts"),
    (".env.example", "."),
]


def run(cmd: list[str]) -> int:
    print(f"\n$ {' '.join(cmd)}\n", flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def clean() -> None:
    for path in (DIST, BUILD, SPEC):
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
                print(f"silindi: {path}")
            else:
                path.unlink()
                print(f"silindi: {path}")


def build(debug: bool, onefile: bool = False) -> int:
    if not ENTRY.exists():
        print(f"HATA: giriş dosyası yok: {ENTRY}", file=sys.stderr)
        return 1

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        APP_NAME,
        "--noconfirm",
        "--clean",
        str(ENTRY),
    ]

    # onedir varsayılan (klasör tabanlı, Inno Setup için ideal)
    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    # GUI mode — debug değilse konsol gizli
    if not debug:
        cmd.append("--windowed")

    for hi in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", hi]
    for em in EXCLUDE_MODULES:
        cmd += ["--exclude-module", em]

    # Data dosyaları — Windows'ta ; separator
    sep = ";" if platform.system() == "Windows" else ":"
    for src, dest in DATAS:
        src_path = ROOT / src
        if src_path.exists():
            cmd += ["--add-data", f"{src_path}{sep}{dest}"]

    # Icon (varsa)
    icon = ROOT / "app" / "resources" / "icon.ico"
    if icon.exists() and platform.system() == "Windows":
        cmd += ["--icon", str(icon)]

    return run(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Borsa Bot PyInstaller build")
    parser.add_argument("--clean", action="store_true", help="dist/build/spec temizle")
    parser.add_argument("--debug", action="store_true", help="konsol penceresi ile")
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="tek dosya .exe (boyut büyük, açılış yavaş) — varsayılan onedir",
    )
    args = parser.parse_args()

    if args.clean:
        clean()

    print(f"Platform: {platform.system()} {platform.release()}")
    print(f"Python:   {sys.version.split()[0]}")
    print(f"Giriş:    {ENTRY}")

    rc = build(debug=args.debug, onefile=args.onefile)
    if rc == 0:
        if args.onefile:
            artifact = DIST / f"{APP_NAME}.exe"
        else:
            artifact = DIST / APP_NAME / f"{APP_NAME}.exe"
        print(f"\nBaşarılı. Çıktı: {artifact}")
        print("Sonraki adım: scripts\\build_installer.bat")
    else:
        print(f"\nHATA: PyInstaller başarısız (exit={rc})", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
