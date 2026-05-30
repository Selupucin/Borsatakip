---
date: 2026-05-27
agent: project-orchestrator
phase: faz-1
type: config
related_files:
  - .claude/agents/project-orchestrator.md
  - .claude/agents/data-collector.md
  - .claude/agents/database-architect.md
  - .claude/agents/analysis-engine.md
  - .claude/agents/portfolio-manager.md
  - .claude/agents/ui-developer.md
  - .claude/agents/trading-executor.md
  - .claude/agents/test-engineer.md
  - .claude/agents/devops-engineer.md
  - doc/project-history/README.md
related_doc_sections:
  - "Bütün dokümantasyon — agent yetki sınırları için referans"
---

## Özet
Borsa Bot projesi için 1 orchestrator + 8 uzman sub-agent oluşturuldu ve `doc/project-history/` kayıt sistemi kuruldu.

## Detaylar
- `.claude/agents/` dizini oluşturuldu, 9 agent tanım dosyası yazıldı:
  1. **project-orchestrator** (opus) — Baş koordinatör, görev ayrıştırma, kalite kapısı
  2. **data-collector** (sonnet) — `app/data/` veri kaynakları + async toplayıcı + karşılaştırma motoru
  3. **database-architect** (sonnet) — `app/db/` PostgreSQL şema, SQLAlchemy 2.0, Alembic
  4. **analysis-engine** (opus) — `app/analysis/` teknik, NLP, öneri motoru, backtest, risk
  5. **portfolio-manager** (sonnet) — `app/portfolio/` cüzdan matrisi, P&L, kur etkisi
  6. **ui-developer** (sonnet) — `app/ui/` PySide6, finplot, tema, reusable component'lar
  7. **trading-executor** (opus) — `app/trading/` + `app/broker/` modlar, risk skoru, auto gate
  8. **test-engineer** (sonnet) — `tests/` pytest birim + entegrasyon
  9. **devops-engineer** (sonnet) — Poetry, .env, PyInstaller, scripts
- `doc/project-history/README.md` yazıldı; dosya adlandırma kuralı (`YYYY-MM-DD_<agent>_<slug>.md`) ve içerik şablonu tanımlandı
- **Zorunlu kayıt kuralı:** Her sub-agent tamamladığı her iş için `doc/project-history/` altına şablona uygun kayıt oluşturmak zorunda. Orchestrator bunu denetler

## Gerekçe
Doküman §10'daki klasör yapısı 8 ayrı uzmanlık alanına bölünmüş (data, db, analysis, portfolio, trading, broker, ui, alerts/scripts). Her alana özel sub-agent atayarak:
- Tek bir konuşmada birden fazla uzmanlığın çakışmasını önler
- Sub-agent'lar paralel çalıştırılabilir (orchestrator tek mesajda birden fazla `Agent` çağrısı yapabilir)
- Her agent'ın kendi bağlam penceresinde sadece kendi alanını taşıması model performansını artırır
- Faz yönetimi (özellikle Faz 4'ün AKTİF DEĞİL kuralı) merkezi olarak orchestrator tarafından zorlanır

Project-history kaydı; ileride hangi değişikliğin neden yapıldığını, hangi agent tarafından gerçekleştirildiğini ve dokümanın hangi maddesine karşılık geldiğini izlemek için.

## Test / Doğrulama
- `Glob ".claude/agents/*.md"` ile 9 dosyanın hepsi doğrulandı
- Henüz uygulama kodu yok — test fazı yok
- İlk gerçek faz görevinde orchestrator'ın kayıt denetimi mekanizması test edilecek

## Notlar
- **Faz 4 (gerçek aracı kurum entegrasyonu) AKTİF DEĞİL.** `trading-executor` agent'ı bu kuralı zorlar; `BaseBroker` arayüzü yazılabilir ama `place_order` gerçek emir göndermez (`NotImplementedError`).
- **PyQt6 değil, PySide6** — lisans (LGPL) nedeniyle. UI agent'ı bu kuralı içerir.
- **TCMB ve MKK hisse fiyat kaynağı DEĞİL** — sadece TCMB kur için. Data-collector agent'ı bu kuralı içerir.
- Sub-agent'lar henüz **invoke edilmedi**. Bir sonraki adım kullanıcıya kalır:
  - "Faz 1'i başlat" → orchestrator devreye girer, sırayla devops + database + data-collector + ui + test agent'larını koordine eder.
  - Ya da spesifik bir görev (örn. "yfinance source yaz") → orchestrator doğrudan data-collector'a delege eder.
