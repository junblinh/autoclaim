"""
ocr.py — OCRReader
Wraps EasyOCR to read text from the CLAIM column region.

Performance target: < 70 ms per OCR call (GPU optional).

Returns structured row-level results:
    [{row_id, text, bbox, confidence}]
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class OCRResult:
    """One text detection from EasyOCR mapped to a row."""

    row_id: int
    text: str
    bbox: tuple[int, int, int, int]  # (x, y, w, h) relative to captured region
    confidence: float
    raw_bbox: list  # raw EasyOCR polygon

    @property
    def center(self) -> tuple[int, int]:
        x, y, w, h = self.bbox
        return x + w // 2, y + h // 2


class OCRReader:
    """
    Wraps EasyOCR for CLAIM column text detection.

    Initialisation triggers the EasyOCR model download on first run
    (cached to ~/.EasyOCR after that).

    Usage::

        reader = OCRReader(ocr_cfg)
        results = reader.read_claim_column(frame_bgr, claim_col_region)
        for r in results:
            if r.text.upper() == "CLAIM":
                ...
    """

    def __init__(self, ocr_cfg: dict) -> None:
        self._cfg = ocr_cfg
        self._confidence_threshold: float = float(
            ocr_cfg.get("confidence_threshold", 0.4)
        )
        self._claim_keyword: str = str(ocr_cfg.get("claim_keyword", "CLAIM")).upper()
        self._cache_enabled: bool = bool(ocr_cfg.get("cache_identical_frames", True))

        # Lazy-load EasyOCR (heavy import — only done once)
        self._reader = None
        self._languages: list[str] = ocr_cfg.get("languages", ["en"])
        self._gpu: bool = bool(ocr_cfg.get("gpu", False))

        # Frame-level cache: skip OCR if content hasn't changed
        self._last_frame_hash: str = ""
        self._last_results: list[OCRResult] = []

        # Timing
        self._last_ocr_ms: float = 0.0

    def _ensure_reader(self) -> None:
        """Lazily initialise EasyOCR (called on first read)."""
        if self._reader is None:
            import easyocr  # noqa: PLC0415
            self._reader = easyocr.Reader(
                self._languages,
                gpu=self._gpu,
                verbose=False,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_claim_column(
        self,
        claim_col_frame: np.ndarray,
        row_rects: list[dict] | None = None,
    ) -> list[OCRResult]:
        """
        Run OCR on the pre-cropped CLAIM column image.

        Parameters
        ----------
        claim_col_frame:
            BGR image of the CLAIM column region only.
        row_rects:
            Optional list of row bounding boxes [{y_top, y_bottom}] for
            assigning row_id to each result.  If None, row_id is derived
            from vertical position.

        Returns
        -------
        List of OCRResult, one per detected text block.
        """
        self._ensure_reader()

        # Cache check — skip OCR if frame hasn't changed
        if self._cache_enabled:
            frame_hash = self._hash_frame(claim_col_frame)
            if frame_hash == self._last_frame_hash and self._last_results:
                return self._last_results
            self._last_frame_hash = frame_hash

        t0 = time.perf_counter()

        raw = self._reader.readtext(
            claim_col_frame,
            detail=1,
            paragraph=False,
        )

        self._last_ocr_ms = (time.perf_counter() - t0) * 1000

        results = self._parse_results(raw, row_rects)
        self._last_results = results
        return results

    def find_claim_rows(
        self,
        claim_col_frame: np.ndarray,
        row_rects: list[dict] | None = None,
    ) -> list[OCRResult]:
        """
        Return only results where text matches the CLAIM keyword.
        """
        all_results = self.read_claim_column(claim_col_frame, row_rects)
        return [r for r in all_results if r.text.upper() == self._claim_keyword]

    def has_username(
        self,
        claim_col_frame: np.ndarray,
        username: str,
        row_id: int,
        row_rects: list[dict] | None = None,
    ) -> bool:
        """
        Return True if *username* is now visible in the CLAIM cell for *row_id*.
        Used to confirm a successful claim.
        """
        results = self.read_claim_column(claim_col_frame, row_rects)
        username_upper = username.upper()
        for r in results:
            if r.row_id == row_id and username_upper in r.text.upper():
                return True
        return False

    @property
    def last_ocr_ms(self) -> float:
        """Time taken for the last OCR call (milliseconds)."""
        return self._last_ocr_ms

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_results(
        self,
        raw: list,
        row_rects: list[dict] | None,
    ) -> list[OCRResult]:
        """
        Convert EasyOCR raw output to OCRResult objects.

        EasyOCR returns: [(bbox_polygon, text, confidence), ...]
        where bbox_polygon is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]].
        """
        results: list[OCRResult] = []

        for idx, (polygon, text, confidence) in enumerate(raw):
            if confidence < self._confidence_threshold:
                continue

            # Convert polygon to axis-aligned bounding box
            xs = [p[0] for p in polygon]
            ys = [p[1] for p in polygon]
            x = int(min(xs))
            y = int(min(ys))
            w = int(max(xs) - x)
            h = int(max(ys) - y)

            # Assign row_id
            row_id = self._assign_row_id(y, y + h, row_rects) if row_rects else idx

            results.append(
                OCRResult(
                    row_id=row_id,
                    text=text.strip(),
                    bbox=(x, y, w, h),
                    confidence=confidence,
                    raw_bbox=polygon,
                )
            )

        return results

    @staticmethod
    def _assign_row_id(y_top: int, y_bottom: int, row_rects: list[dict]) -> int:
        """
        Find which row rect the OCR result vertically overlaps the most.
        Returns row index or -1 if no overlap found.
        """
        best_id = -1
        best_overlap = 0
        center_y = (y_top + y_bottom) / 2

        for i, rect in enumerate(row_rects):
            ry = rect.get("y", rect.get("top", 0))
            rh = rect.get("height", rect.get("h", 0))
            # Check if OCR center_y falls within this row
            if ry <= center_y <= ry + rh:
                overlap = min(y_bottom, ry + rh) - max(y_top, ry)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_id = i

        return best_id

    @staticmethod
    def _hash_frame(frame: np.ndarray) -> str:
        """Fast perceptual hash for change detection."""
        # Downsample 8x for speed
        small = frame[::8, ::8]
        return hashlib.md5(small.tobytes()).hexdigest()
