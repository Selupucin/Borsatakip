"""Borsa Bot sürüm bilgisi — tek doğru kaynak.

``installer.iss`` ve update checker bu dosyadan okur. Yeni sürüm için:

1. ``__version__`` arttır (semver: MAJOR.MINOR.PATCH)
2. ``CHANGELOG.md`` veya release notes hazırla
3. Git tag: ``git tag v<VERSION> && git push --tags``
4. GitHub Actions workflow installer'ı otomatik üretir + Release'e yükler.
"""

__version__ = "0.1.8"
__author__ = "Selupucin"
__repo__ = "Selupucin/Borsatakip"
