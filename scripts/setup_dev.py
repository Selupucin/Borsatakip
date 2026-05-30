"""Geliştirici ortamı kurulum yardımcısı.

Bu script:
- Python sürümünü kontrol eder (>=3.11 zorunlu).
- `.env` dosyasının var olduğunu kontrol eder; yoksa `.env.example`'dan kopyalar.
- `playwright install chromium` komutunu çağırır.
- HuggingFace cache konumu hakkında bilgi verir.

Kullanım:
    poetry run python scripts/setup_dev.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

MIN_PYTHON = (3, 11)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def check_python_version() -> None:
    """Python 3.11+ olup olmadığını doğrular."""
    current = sys.version_info[:2]
    if current < MIN_PYTHON:
        print(
            f"[HATA] Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ gerekiyor. "
            f"Mevcut sürüm: {current[0]}.{current[1]}"
        )
        sys.exit(1)
    print(f"[OK]  Python sürümü: {current[0]}.{current[1]}")


def ensure_env_file() -> None:
    """`.env` yoksa `.env.example`'dan kopyalar."""
    env_path = PROJECT_ROOT / ".env"
    example_path = PROJECT_ROOT / ".env.example"

    if env_path.exists():
        print("[OK]  .env dosyası mevcut.")
        return

    if not example_path.exists():
        print("[HATA] .env.example bulunamadı, kopyalanamıyor.")
        sys.exit(1)

    shutil.copy(example_path, env_path)
    print("[OK]  .env dosyası .env.example'dan oluşturuldu.")
    print("      >> Lütfen DB bilgilerini ve API anahtarlarını .env içinde doldurun.")


def install_playwright_chromium() -> None:
    """Playwright Chromium tarayıcısını indirir."""
    print("[..]  Playwright Chromium indiriliyor (scraping için gerekli)...")
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
        print("[OK]  Playwright Chromium kuruldu.")
    except subprocess.CalledProcessError as exc:
        print(f"[UYARI] Playwright kurulumu başarısız oldu: {exc}")
        print("        Manuel deneyin: poetry run playwright install chromium")
    except FileNotFoundError:
        print("[UYARI] Playwright bulunamadı. Önce `poetry install` çalıştırın.")


def print_huggingface_note() -> None:
    """HuggingFace model cache konumu hakkında bilgi verir."""
    print()
    print("=" * 70)
    print("HuggingFace NLP modelleri (BERT-Turkish + FinBERT)")
    print("=" * 70)
    print("İlk çalıştırmada ~500 MB model otomatik indirilir.")
    print("Önbellek konumu:")
    print("  - Linux/macOS: ~/.cache/huggingface/")
    print("  - Windows:     %USERPROFILE%\\.cache\\huggingface\\")
    print("İnternet bağlantınız yavaşsa ilk açılış uzun sürebilir.")
    print()


def main() -> None:
    print("Borsa Bot geliştirici ortamı kurulumu başlıyor...")
    print("-" * 70)
    check_python_version()
    ensure_env_file()
    install_playwright_chromium()
    print_huggingface_note()
    print("Kurulum tamamlandı.")
    print("Sırada: `poetry run python scripts/setup_db.py` ile DB şemasını kurun.")


if __name__ == "__main__":
    main()
