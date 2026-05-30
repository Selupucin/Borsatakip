"""SentimentAnalyzer (app/analysis/sentiment.py) birim testleri.

Tasarım kuralları:
- ``transformers`` / ``torch`` modülleri YOK varsayılır; ``backend=None``
  modu ile analizer oluşturulur ve ``_tr_pipeline`` / ``_en_pipeline`` slotları
  doğrudan callable mock'lar ile değiştirilir. Bu sayede gerçek model
  indirme/yükleme gerekmez.
- DB testleri için conftest.py'deki ``db_session`` (in-memory SQLite, sync)
  fixture'ı kullanılır; ``NewsFeed`` modelinin ``Numeric(5,4)`` ve
  ``processed_at`` alanları SQLite üzerinde de güncellenebilir.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.analysis.sentiment import (
    LOW_CONFIDENCE_THRESHOLD,
    SentimentAnalyzer,
    SentimentLabel,
    SentimentResult,
)
from app.db.models import Instrument, NewsFeed


# ---------------------------------------------------------------------------
# Yardımcı fabrika
# ---------------------------------------------------------------------------


def _make_pipe(label: str, score: float):
    """HuggingFace pipeline'ı taklit eden callable mock.

    Pipeline çağrılınca ``[{"label": label, "score": score}]`` döner — gerçek
    pipeline davranışıyla birebir uyumlu.
    """

    return MagicMock(return_value=[{"label": label, "score": score}])


# ---------------------------------------------------------------------------
# Backend / RuntimeError testleri
# ---------------------------------------------------------------------------


class TestSentimentBackendNone:
    def test_backend_none_initialization_does_not_raise(self):
        """``backend=None`` ile ctor — sadece konfigürasyon, ağ trafiği yok."""
        analyzer = SentimentAnalyzer(backend=None)
        assert analyzer.backend is None
        assert analyzer._tr_pipeline is None
        assert analyzer._en_pipeline is None

    def test_analyze_without_backend_or_mock_raises_runtime_error(self):
        """Mock yokken ``analyze()`` çağrısı ``RuntimeError`` fırlatır."""
        analyzer = SentimentAnalyzer(backend=None)
        with pytest.raises(RuntimeError, match="backend=None"):
            analyzer.analyze("Türkçe metin")

    def test_invalid_backend_raises_value_error(self):
        with pytest.raises(ValueError, match="Geçersiz backend"):
            SentimentAnalyzer(backend="onnx")


# ---------------------------------------------------------------------------
# Dil tespiti
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    @pytest.fixture
    def analyzer(self):
        return SentimentAnalyzer(backend=None)

    def test_tr_chars_detected_as_tr(self, analyzer):
        assert analyzer.detect_language("Türkçe haber metni") == "tr"

    def test_english_text_detected_as_en(self, analyzer):
        assert analyzer.detect_language("English news text") == "en"

    def test_empty_text_defaults_to_en(self, analyzer):
        assert analyzer.detect_language("") == "en"

    def test_uppercase_tr_chars_also_detected(self, analyzer):
        assert analyzer.detect_language("ÇOK İYİ ŞİRKET") == "tr"


# ---------------------------------------------------------------------------
# analyze() — mock pipeline ile
# ---------------------------------------------------------------------------


class TestAnalyzeWithMock:
    def test_tr_positive_returns_positive_label_and_positive_score(self):
        """TR pozitif metin → label=POSITIVE, signed score > 0."""
        analyzer = SentimentAnalyzer(backend=None)
        analyzer._tr_pipeline = _make_pipe("positive", 0.92)

        result = analyzer.analyze("Şirket bu çeyrekte rekor kâr açıkladı")

        assert isinstance(result, SentimentResult)
        assert result.label is SentimentLabel.POSITIVE
        assert result.score > 0
        assert result.score == pytest.approx(0.92, abs=1e-6)
        assert result.language == "tr"

    def test_en_negative_returns_negative_label_and_negative_score(self):
        """EN negatif metin → label=NEGATIVE, signed score < 0."""
        analyzer = SentimentAnalyzer(backend=None)
        analyzer._en_pipeline = _make_pipe("negative", 0.85)

        result = analyzer.analyze("Company missed earnings expectations significantly")

        assert result.label is SentimentLabel.NEGATIVE
        assert result.score < 0
        assert result.score == pytest.approx(-0.85, abs=1e-6)
        assert result.language == "en"

    def test_neutral_returns_zero_signed_score(self):
        """Neutral etiket → signed score ≈ 0 (ham ne olursa olsun)."""
        analyzer = SentimentAnalyzer(backend=None)
        analyzer._en_pipeline = _make_pipe("neutral", 0.75)

        result = analyzer.analyze("The company released a report today")

        assert result.label is SentimentLabel.NEUTRAL
        assert result.score == pytest.approx(0.0, abs=1e-9)

    def test_low_confidence_adds_turkish_warning_note(self):
        """``raw_score < LOW_CONFIDENCE_THRESHOLD`` → notes Türkçe uyarı içerir."""
        low = LOW_CONFIDENCE_THRESHOLD - 0.05  # eşiğin altı
        analyzer = SentimentAnalyzer(backend=None)
        analyzer._tr_pipeline = _make_pipe("positive", low)

        result = analyzer.analyze("Belirsiz Türkçe haber")

        assert len(result.notes) >= 1
        joined = " ".join(result.notes)
        # Türkçe uyarı: "Düşük model güveni" + "ironi" gibi anahtarlar.
        assert "Düşük model güveni" in joined or "ironi" in joined

    def test_high_confidence_has_no_warning_note(self):
        analyzer = SentimentAnalyzer(backend=None)
        analyzer._tr_pipeline = _make_pipe("positive", 0.95)

        result = analyzer.analyze("Açık ve net Türkçe haber metni")

        assert result.notes == []


# ---------------------------------------------------------------------------
# analyze_batch() — sıralı toplu analiz
# ---------------------------------------------------------------------------


class TestAnalyzeBatch:
    def test_batch_returns_results_in_same_order(self):
        """``analyze_batch`` girdi sırasını korur."""
        analyzer = SentimentAnalyzer(backend=None)

        # Her metin için ayrı mock dönüşü — return değerini side_effect ile sırala.
        responses = [
            [{"label": "positive", "score": 0.9}],
            [{"label": "negative", "score": 0.8}],
            [{"label": "neutral", "score": 0.7}],
        ]
        analyzer._en_pipeline = MagicMock(side_effect=responses)

        texts = ["Great news", "Bad news", "Just news"]
        results = analyzer.analyze_batch(texts, language="en")

        assert len(results) == 3
        assert results[0].label is SentimentLabel.POSITIVE
        assert results[1].label is SentimentLabel.NEGATIVE
        assert results[2].label is SentimentLabel.NEUTRAL


# ---------------------------------------------------------------------------
# save_news_sentiment() — DB integration (in-memory SQLite)
# ---------------------------------------------------------------------------


class TestSaveNewsSentiment:
    def test_save_news_sentiment_updates_row_fields(self, db_session):
        """``save_news_sentiment`` → ``sentiment``, ``sentiment_score`` ve
        ``processed_at`` güncellenir."""
        # Arrange — önce NewsFeed satırı seed et.
        instrument = Instrument(ticker="AAPL", name="Apple")
        db_session.add(instrument)
        db_session.flush()

        news = NewsFeed(
            instrument_id=instrument.id,
            source="reuters",
            title="Apple beats earnings",
            url="https://example.com/aapl",
            published_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
            language="en",
            sentiment=None,
            sentiment_score=None,
        )
        db_session.add(news)
        db_session.flush()
        news_id = news.id

        # Act
        analyzer = SentimentAnalyzer(backend=None)
        analyzer._en_pipeline = _make_pipe("positive", 0.88)
        result = analyzer.analyze("Apple beats earnings", language="en")

        updated = analyzer.save_news_sentiment(db_session, news_id, result)
        db_session.flush()

        # Assert
        assert updated is not None
        refreshed = db_session.get(NewsFeed, news_id)
        assert refreshed.sentiment == SentimentLabel.POSITIVE.value  # 'positive'
        assert refreshed.sentiment_score == pytest.approx(0.88, abs=1e-3)
        assert refreshed.processed_at is not None

    def test_save_news_sentiment_missing_row_returns_none(self, db_session):
        analyzer = SentimentAnalyzer(backend=None)
        result = SentimentResult(
            label=SentimentLabel.POSITIVE,
            score=0.5,
            raw_scores={"positive": 0.5},
            language="en",
            model_name="x",
            notes=[],
        )
        # 999 numaralı satır yok — None dönmeli, exception YOK.
        out = analyzer.save_news_sentiment(db_session, 999, result)
        assert out is None
