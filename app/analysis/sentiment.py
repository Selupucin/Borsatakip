"""HuggingFace transformers tabanlı sentiment analizi (TR + EN).

Doküman §3.5 (NLP modelleri) ve agent kuralları (lazy load, normalize
skor, dil otomatik tespiti) referans alınarak yazılmıştır.

Tasarım kuralları:
- Modeller **lazy load** edilir. ``SentimentAnalyzer`` instance oluşturulurken
  ağ trafiği YOK; ilk ``analyze()`` çağrısında ilgili dil için pipeline
  indirilir ve bellekte tutulur.
- ``transformers`` / ``torch`` yüklü değilse ``backend=None`` modu çalışır —
  herhangi bir ``analyze()`` çağrısı ``RuntimeError`` fırlatır. Test
  ortamında ``SentimentAnalyzer._tr_pipeline`` / ``_en_pipeline`` doğrudan
  callable bir mock ile monkey-patch edilebilir.
- Skor normalize: ``positive → +score``, ``negative → -score``, ``neutral → 0``.
  ``score`` ham model olasılığı (0..1), ``SentimentResult.score`` ise
  ``[-1, +1]`` aralığına sıkıştırılmış işaretli değer.
- Dil tespiti basit kural: TR'ye özgü karakterler (ç, ğ, ı, ö, ş, ü) varsa
  ``'tr'``, yoksa ``'en'``. Daha gelişmiş tespit Faz 3'te ``langdetect``
  ile değiştirilebilir.
- İroni/sektörel jargon uyarısı: ham model olasılığı ``< 0.6`` ise
  ``SentimentResult.notes`` alanına Türkçe uyarı yazılır.

Para hesaplaması yok; yalnızca NLP. ``NewsFeed`` güncellemesi
``Numeric(5, 4)`` kolonuna ``float`` üzerinden yapılır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

from loguru import logger

# ---------------------------------------------------------------------------
# Backend tespiti (transformers + torch). Bulunmazsa mock-only mod.
# ---------------------------------------------------------------------------

BACKEND: Optional[str]

try:  # pragma: no cover - import side-effect
    import transformers  # type: ignore[import-not-found]  # noqa: F401
    import torch  # type: ignore[import-not-found]  # noqa: F401

    BACKEND = "transformers"
except ImportError:  # pragma: no cover - fallback path
    BACKEND = None
    logger.warning(
        "transformers/torch yüklü değil; SentimentAnalyzer yalnızca mock "
        "edildiğinde çalışır (BACKEND=None)."
    )


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


class SentimentLabel(str, Enum):
    """Üç sınıflı sentiment etiketi (DB ``news_feed.sentiment`` ile uyumlu)."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


@dataclass
class SentimentResult:
    """Bir metin parçası için sentiment analiz sonucu.

    Attributes
    ----------
    label:
        ``positive`` / ``negative`` / ``neutral``.
    score:
        ``[-1, +1]`` aralığında normalize edilmiş işaretli skor.
        ``positive → +ham_score``, ``negative → -ham_score``, ``neutral → 0``.
    raw_scores:
        Model ham çıktıları (etiket → olasılık eşlemesi).
    language:
        Analizde kullanılan dil (``'tr'`` veya ``'en'``).
    model_name:
        Kullanılan HuggingFace model ismi (debug/audit için).
    notes:
        İroni / düşük güven uyarıları (Türkçe). Boş liste varsayılan.
    """

    label: SentimentLabel
    score: float
    raw_scores: dict[str, float]
    language: str
    model_name: str
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

_TR_CHARS = frozenset("çğıöşüÇĞİÖŞÜ")
# Düşük güven eşiği: ham model olasılığı bunun altındaysa ironi uyarısı eklenir.
LOW_CONFIDENCE_THRESHOLD = 0.6


def _normalize_label(raw_label: str) -> SentimentLabel:
    """HuggingFace pipeline ham etiketini normalize SentimentLabel'e çevirir.

    FinBERT: ``positive`` / ``negative`` / ``neutral`` (küçük harf).
    Bazı TR modelleri büyük harf veya farklı isimlendirme kullanır
    (``LABEL_0`` / ``LABEL_1`` veya ``POSITIVE`` / ``NEGATIVE``). Basit
    eşleme uygulanır; bilinmeyen etiketler ``NEUTRAL``'a düşer.
    """

    key = (raw_label or "").strip().lower()
    if key in {"positive", "pos", "label_2", "5 stars", "4 stars"}:
        return SentimentLabel.POSITIVE
    if key in {"negative", "neg", "label_0", "1 star", "2 stars"}:
        return SentimentLabel.NEGATIVE
    if key in {"neutral", "neu", "label_1", "3 stars"}:
        return SentimentLabel.NEUTRAL
    # Bilinmeyen — savunmacı.
    return SentimentLabel.NEUTRAL


def _signed_score(label: SentimentLabel, raw_score: float) -> float:
    """Etiket + ham olasılığı [-1, +1] işaretli skora çevirir."""

    raw_score = max(0.0, min(1.0, float(raw_score)))
    if label is SentimentLabel.POSITIVE:
        return raw_score
    if label is SentimentLabel.NEGATIVE:
        return -raw_score
    return 0.0


# ---------------------------------------------------------------------------
# Ana sınıf
# ---------------------------------------------------------------------------


class SentimentAnalyzer:
    """TR ve EN sentiment analizini tek arayüzden sunar.

    Modeller **lazy load** edilir. İlk ``analyze()`` çağrısı ilgili dil için
    pipeline'ı kurar; tekrar çağrılar belleğe alınmış pipeline'ı kullanır.

    Test ortamında ``self._tr_pipeline`` veya ``self._en_pipeline`` doğrudan
    callable bir mock ile değiştirilerek model indirme gereği ortadan
    kaldırılabilir (örn. ``monkeypatch.setattr(analyzer, "_tr_pipeline",
    fake_pipeline)``).
    """

    TR_MODEL = "savasy/bert-base-turkish-sentiment-cased"
    EN_MODEL = "ProsusAI/finbert"

    def __init__(
        self,
        tr_model_name: str | None = None,
        en_model_name: str | None = None,
        backend: str | None = None,
    ) -> None:
        self.tr_model_name = tr_model_name or self.TR_MODEL
        self.en_model_name = en_model_name or self.EN_MODEL
        self.backend = backend if backend is not None else BACKEND
        if self.backend not in {"transformers", None}:
            raise ValueError(
                f"Geçersiz backend: {self.backend!r}. "
                "'transformers' veya None olmalı."
            )

        # Lazy slotlar — None iken henüz model yüklenmedi.
        self._tr_pipeline: Optional[Callable[..., Any]] = None
        self._en_pipeline: Optional[Callable[..., Any]] = None

        if self.backend is None:
            logger.debug(
                "SentimentAnalyzer backend=None ile başlatıldı; gerçek "
                "analiz çağrıları RuntimeError verir (sadece mock için)."
            )

    # ----------------------------------------------------------- pipeline yükleme

    def _load_tr_pipeline(self) -> Callable[..., Any]:
        """TR sentiment pipeline'ını lazy yükler."""

        if self._tr_pipeline is not None:
            return self._tr_pipeline
        if self.backend is None:
            raise RuntimeError(
                "SentimentAnalyzer: backend=None — TR pipeline yüklenemez. "
                "Testte ``self._tr_pipeline`` slotunu mock ile değiştirin."
            )
        from transformers import pipeline  # type: ignore[import-not-found]

        logger.info(
            "TR sentiment modeli yükleniyor (ilk çağrı): {}", self.tr_model_name
        )
        self._tr_pipeline = pipeline(
            "sentiment-analysis", model=self.tr_model_name
        )
        return self._tr_pipeline

    def _load_en_pipeline(self) -> Callable[..., Any]:
        """EN (FinBERT) sentiment pipeline'ını lazy yükler."""

        if self._en_pipeline is not None:
            return self._en_pipeline
        if self.backend is None:
            raise RuntimeError(
                "SentimentAnalyzer: backend=None — EN pipeline yüklenemez. "
                "Testte ``self._en_pipeline`` slotunu mock ile değiştirin."
            )
        from transformers import pipeline  # type: ignore[import-not-found]

        logger.info(
            "EN sentiment modeli yükleniyor (ilk çağrı): {}", self.en_model_name
        )
        self._en_pipeline = pipeline(
            "sentiment-analysis", model=self.en_model_name
        )
        return self._en_pipeline

    # ----------------------------------------------------------- dil tespiti

    def detect_language(self, text: str) -> str:
        """Basit kuraldan: TR'ye özgü karakterler varsa 'tr', yoksa 'en'.

        - ``None`` veya boş string → ``'en'`` (varsayılan).
        - Büyük/küçük harf duyarsız: hem ``ç`` hem ``Ç`` TR sayılır.
        """

        if not text:
            return "en"
        for ch in text:
            if ch in _TR_CHARS:
                return "tr"
        return "en"

    # ----------------------------------------------------------- analiz

    def analyze(
        self, text: str, language: str | None = None
    ) -> SentimentResult:
        """Tek bir metin için sentiment analizi yapar.

        Parameters
        ----------
        text:
            Analiz edilecek serbest metin.
        language:
            ``'tr'`` veya ``'en'``. Verilmezse ``detect_language()`` ile
            otomatik seçilir.
        """

        if text is None:
            text = ""
        lang = (language or self.detect_language(text)).lower()
        if lang not in {"tr", "en"}:
            logger.warning(
                "Bilinmeyen dil {!r}; 'en' olarak ele alınacak.", lang
            )
            lang = "en"

        if lang == "tr":
            pipe = self._load_tr_pipeline()
            model_name = self.tr_model_name
        else:
            pipe = self._load_en_pipeline()
            model_name = self.en_model_name

        # HuggingFace pipeline genelde [{'label': 'POSITIVE', 'score': 0.98}]
        # döner. Bazı sürümlerde tek dict (liste değil) dönebilir — savunmacı.
        raw_output = pipe(text)
        first = raw_output[0] if isinstance(raw_output, list) else raw_output

        raw_label = str(first.get("label", "neutral"))
        raw_score = float(first.get("score", 0.0))
        label = _normalize_label(raw_label)
        signed = _signed_score(label, raw_score)

        # Ham olasılıkları sakla (UI tooltip ve audit için).
        raw_scores: dict[str, float] = {raw_label: raw_score}

        notes: list[str] = []
        if raw_score < LOW_CONFIDENCE_THRESHOLD:
            notes.append(
                f"Düşük model güveni (%{raw_score * 100:.0f}); ironi/alay "
                "veya sektörel jargon nedeniyle yanlış olabilir."
            )

        return SentimentResult(
            label=label,
            score=signed,
            raw_scores=raw_scores,
            language=lang,
            model_name=model_name,
            notes=notes,
        )

    def analyze_batch(
        self, texts: list[str], language: str | None = None
    ) -> list[SentimentResult]:
        """Birden çok metni sırayla analiz eder.

        Şu an arka arkaya tekil çağrı yapar (basitlik); transformers
        pipeline batching özelliği Faz 3'te eklenebilir.
        """

        return [self.analyze(t, language=language) for t in texts]

    # ----------------------------------------------------------- DB persist

    def save_news_sentiment(
        self, session, news_id: int, result: SentimentResult
    ):
        """``NewsFeed`` satırını sentiment bilgisiyle günceller.

        ``sentiment`` (etiket), ``sentiment_score`` (Numeric(5,4)) ve
        ``processed_at`` alanları yazılır. Diğer alanlar dokunulmaz.

        Parameters
        ----------
        session:
            SQLAlchemy ``Session`` örneği. Commit çağrı yeri caller'a aittir.
        news_id:
            Güncellenecek ``NewsFeed`` satırının PK'sı.
        result:
            Bu metnin sentiment sonucu.

        Returns
        -------
        NewsFeed
            Güncellenmiş ORM nesnesi (None ise satır bulunamamıştır).
        """

        # İçe aktarmayı geciktiriyoruz — modül app.db olmadan da import edilebilsin.
        from app.db.models import NewsFeed  # noqa: WPS433

        row = session.get(NewsFeed, news_id)
        if row is None:
            logger.warning(
                "save_news_sentiment: NewsFeed id={} bulunamadı.", news_id
            )
            return None

        # Numeric(5, 4) → en fazla 1.0000; signed score zaten [-1, +1].
        row.sentiment = result.label.value
        row.sentiment_score = float(result.score)
        row.processed_at = datetime.now(tz=timezone.utc)
        session.add(row)
        return row


__all__ = [
    "BACKEND",
    "LOW_CONFIDENCE_THRESHOLD",
    "SentimentLabel",
    "SentimentResult",
    "SentimentAnalyzer",
]
