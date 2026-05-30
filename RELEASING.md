# Sürüm Yayınlama (Release) Süreci

GitHub Releases üzerinden tam otomatik build & dağıtım. Kullanıcılar
**Ayarlar → Güncellemeler** ekranından "Şimdi Kontrol Et" deyince GitHub
API'den son sürümü görür, "İndir & Kur" ile yeni installer otomatik
indirilip çalıştırılır.

---

## İlk kurulum (sadece bir kez)

### 1. GitHub repo'su
- Repo: `Selupucin/Borsatakip` (zaten public)
- Settings → Actions → General → Workflow permissions:
  - "Read and write permissions" seç
  - "Allow GitHub Actions to create and approve pull requests" — opsiyonel

### 2. İlk dosyaları push'la
```powershell
cd "c:\Users\MSI-Hawking\Desktop\Yazılım\Borsa Bot"
git init
git remote add origin https://github.com/Selupucin/Borsatakip.git
git add .
git commit -m "İlk sürüm: 0.1.2 (auto-update destekli)"
git branch -M main
git push -u origin main
```

> **Not:** `.gitignore` zaten `dist/`, `build/`, `installer_output/`, `.env`,
> `*.spec`, `__pycache__/` gibi büyük/hassas içerikleri commit'ten engeller.
> Sadece kaynak kod + CI workflow yüklenir.

---

## Her yeni sürüm yayını (manuel veya otomatik)

### Seçenek A — GitHub Actions ile otomatik (ÖNERİLEN)

```powershell
# 1. Versiyonu arttır
notepad app\__version__.py    # "0.1.3" yap, kaydet

# 2. Commit + tag + push
git add app/__version__.py
git commit -m "v0.1.3"
git tag v0.1.3
git push origin main --tags
```

**Sonra:** GitHub Actions workflow tetiklenir (~10-15 dk):
1. Windows runner kurulur
2. Python 3.11 + bağımlılıklar yüklenir
3. `scripts/make_icon.py` → ikon
4. `scripts/build_exe.py` → PyInstaller onedir
5. Inno Setup → `BorsaBot-Setup-0.1.3.exe`
6. Otomatik **GitHub Release** oluşturulur
7. Installer dosyası release asset olarak yüklenir
8. Release notes Git commit'lerden otomatik üretilir

**Sonuç:** `https://github.com/Selupucin/Borsatakip/releases/latest` adresinde
yeni sürüm hazır. Kullanıcılar uygulamada Ayarlar → Güncellemeler → Şimdi
Kontrol Et → İndir & Kur yapar.

### Seçenek B — Yerel build + manuel yükle

```powershell
# Versiyonu güncelle (app/__version__.py + installer.iss)
scripts\build_installer.bat
# Bittiğinde installer_output\BorsaBot-Setup-X.Y.Z.exe oluşur

# GitHub web arayüzünden:
# 1. Releases → "Draft a new release"
# 2. Tag: v0.1.3
# 3. Title: "Borsa Bot 0.1.3"
# 4. Description: değişiklik notları
# 5. installer .exe'yi sürükle-bırak → asset olarak yüklenir
# 6. "Publish release"
```

---

## Kullanıcı tarafında

**Otomatik akış:**
- Uygulama açıldıktan 30 sn sonra arka planda check
- Her 6 saatte bir tekrar check
- Yeni sürüm varsa: **sidebar Ayarlar item'ında kırmızı "● 0.1.3" rozet**
- Toast: "Yeni sürüm: 0.1.3"

**Manuel akış:**
- Ayarlar → Güncellemeler → "🔄 Şimdi Kontrol Et"
- Yeni varsa: "⬇ İndir & Kur (0.1.3)" butonu aktive olur
- Tıkla → onay → ~200 MB installer indirilir (`%LOCALAPPDATA%\BorsaBot\updates\`)
- İndirme tamam → uygulama kapanır → installer açılır → otomatik kurulum
- Kullanıcı verisi (`%LOCALAPPDATA%\BorsaBot\borsa_bot.db`) **korunur**

---

## Sorun Giderme

**Workflow başarısız oldu?**
- Actions tab → en son run'a tıkla → kırmızı adımın loglarına bak
- Genelde: çoğunlukla bağımlılık eksik veya `app/__version__.py`'da typo

**Release oluştu ama installer yok?**
- `softprops/action-gh-release` adımı GITHUB_TOKEN gerektiriyor
- Settings → Actions → General → Workflow permissions = "Read and write"

**Kullanıcı "güncelleme yok" diyor ama yeni release var?**
- API cache 60 sn olabilir, biraz bekle
- `https://api.github.com/repos/Selupucin/Borsatakip/releases/latest` ile manuel test et
- Anonim rate limit saatte 60 — VPN/proxy varsa düşebilir

**Indirme çok yavaş?**
- GitHub CDN bölgesel — yüksek hızda olur normalde
- Yarıda kesilirse `%LOCALAPPDATA%\BorsaBot\updates\` altında yarım dosya kalır,
  uygulama yeniden başlayınca tekrar denerse aynı yere yazar (overwrite)
