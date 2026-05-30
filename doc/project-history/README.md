# Project History

Bu klasör, Borsa Bot projesindeki tüm güncelleme, yenilik ve değişikliklerin kronolojik kaydını tutar.

## Dosya Adlandırma Kuralı

```
YYYY-MM-DD_<agent-name>_<short-slug>.md
```

Örnek:
- `2026-05-27_database-architect_initial-schema.md`
- `2026-05-28_data-collector_yfinance-integration.md`

## İçerik Şablonu

Her giriş aşağıdaki şablonu kullanmalıdır:

```markdown
---
date: YYYY-MM-DD
agent: <agent-name>
phase: <faz-1 | faz-2 | faz-3 | faz-4>
type: <feature | fix | refactor | docs | config | test>
related_files:
  - app/path/to/file.py
related_doc_sections:
  - "5.1 Girdi Sinyalleri"
---

## Özet
Bir-iki cümlede ne yapıldı.

## Detaylar
- Yapılan değişikliklerin maddeler halinde listesi
- Etkilenen modüller / tablolar / endpoint'ler
- Bağımlılık değişiklikleri (yeni paket, sürüm yükseltme vb.)

## Gerekçe
Neden bu değişiklik yapıldı? Hangi gereksinim veya dokümantasyon maddesini karşılıyor?

## Test / Doğrulama
Nasıl test edildi? Hangi pytest dosyaları eklendi/güncellendi?

## Notlar
Bilinen sınırlamalar, takip eden işler, dikkat edilmesi gerekenler.
```

## Zorunlu Kayıt Kuralları

1. **Her** önemli kod değişikliği, yeni dosya, refactor veya kaldırma için kayıt oluşturulur.
2. Konfigürasyon değişiklikleri (`.env`, `pyproject.toml`, Alembic migration) da kayıt altına alınır.
3. Veritabanı şema değişiklikleri için ilgili migration adı kayıtta yer almalıdır.
4. UI değişiklikleri için ekran adı belirtilmelidir.
5. Sub-agent'lar kayıt yazmadan iş tamamlanmış sayılmaz — bu kural orchestrator tarafından denetlenir.
