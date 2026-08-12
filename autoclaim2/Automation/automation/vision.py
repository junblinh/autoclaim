"""
vision.py — ClaimDetector
Row detection and Select/Connect button localisation via OpenCV.

Provides:
  - Horizontal line analysis to segment table rows
  - Optional template matching for the "Select" button
  - Fallback column-offset calculation from config
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


@dataclass
class RowInfo:
    """Represents one detected table row."""

    index: int
    rect: tuple[int, int, int, int]   # (x, y, w, h) relative to table_region
    claim_bbox: Optional[tuple[int, int, int, int]] = None  # CLAIM cell rect
    select_btn_pos: Optional[tuple[int, int]] = None        # centre of Select btn (abs screen)
    ocr_text: str = ""
    ocr_confidence: float = 0.0


class ClaimDetector:
    """
    Detects table rows in a captured frame and locates action buttons.

    Usage::

        detector = ClaimDetector(row_detection_cfg, table_region, select_column)
        rows = detector.detect_rows(frame_bgr)
        for row in rows:
            print(row.index, row.select_btn_pos)
    """

    def __init__(
        self,
        row_detection_cfg: dict,
        table_region: dict,
        select_column: dict,
        claim_column: dict,
        connect_button: dict,
    ) -> None:
        self._cfg = row_detection_cfg
        self._table = table_region
        self._select_col = select_column
        self._claim_col = claim_column
        self._connect_btn = connect_button

        self._min_row_h: int = int(row_detection_cfg.get("min_row_height_px", 15))
        self._max_row_h: int = int(row_detection_cfg.get("max_row_height_px", 60))
        self._use_template: bool = bool(row_detection_cfg.get("use_template_matching", False))
        self._template_path: str = str(row_detection_cfg.get("template_select_btn", ""))
        self._template_conf: float = float(row_detection_cfg.get("template_confidence", 0.80))
        self._line_threshold: int = int(row_detection_cfg.get("line_thickness_threshold", 1))

        self._template: Optional[np.ndarray] = None
        self._load_template()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_rows(self, frame: np.ndarray) -> list[RowInfo]:
        """
        Detect all table rows from *frame* (the captured table_region image).

        Returns rows sorted top-to-bottom.
        """
        method = self._cfg.get("method", "horizontal_lines")
        if method == "horizontal_lines":
            return self._detect_by_lines(frame)
        else:
            return self._detect_by_lines(frame)  # extend with other methods if needed

    def find_select_button(
        self, row: RowInfo, frame: np.ndarray
    ) -> Optional[tuple[int, int]]:
        """
        Return the absolute screen coordinate of the Select button for *row*.

        Strategy:
          1. Template matching (if enabled and template exists)
          2. Column-offset calculation from select_column config
        """
        if self._use_template and self._template is not None:
            pos = self._template_match_in_row(frame, row)
            if pos:
                return pos

        return self._offset_select_position(row)

    def connect_button_pos(self) -> tuple[int, int]:
        """Return absolute screen position of the Connect button (center of region)."""
        if "width" in self._connect_btn and int(self._connect_btn.get("width", 0)) > 0:
            left = int(self._connect_btn.get("left", 0))
            top = int(self._connect_btn.get("top", 0))
            width = int(self._connect_btn.get("width", 0))
            height = int(self._connect_btn.get("height", 0))
            return left + width // 2, top + height // 2
            
        # Fallback for old point-based config
        return int(self._connect_btn.get("x", -1)), int(self._connect_btn.get("y", -1))

    def preprocess(self, frame: np.ndarray) -> np.ndarray:
        """Convert BGR frame to grayscale for line detection."""
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # ------------------------------------------------------------------
    # Row detection — horizontal line analysis
    # ------------------------------------------------------------------

    def _detect_by_lines(self, frame: np.ndarray) -> list[RowInfo]:
        """
        Find horizontal separators to delimit rows.

        Works by:
          1. Convert to grayscale
          2. Apply edge detection
          3. Detect horizontal lines via HoughLinesP or projection
          4. Use line y-positions as row boundaries
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # Horizontal projection: sum of pixel intensity per row
        # Rows with very uniform intensity indicate separator lines
        edges = cv2.Canny(gray, 50, 150)
        projection = np.sum(edges, axis=1).astype(np.float32)

        # Normalise
        if projection.max() > 0:
            projection /= projection.max()

        # Find rows that are "mostly edge" → separator lines
        threshold = 0.3
        is_line = projection > threshold

        # Group consecutive line pixels into separator bands
        separators = self._find_separator_bands(is_line, h)

        # Convert separator bands → row rectangles
        rows = self._separators_to_rows(separators, h, w, frame)
        return rows

    @staticmethod
    def _find_separator_bands(
        is_line: np.ndarray, height: int
    ) -> list[tuple[int, int]]:
        """
        Return list of (y_start, y_end) for each detected separator band.
        """
        bands: list[tuple[int, int]] = []
        in_band = False
        start = 0

        for y in range(height):
            if is_line[y] and not in_band:
                in_band = True
                start = y
            elif not is_line[y] and in_band:
                in_band = False
                bands.append((start, y))

        if in_band:
            bands.append((start, height - 1))

        return bands

    def _separators_to_rows(
        self,
        separators: list[tuple[int, int]],
        height: int,
        width: int,
        frame: np.ndarray,
    ) -> list[RowInfo]:
        """
        Build RowInfo objects from the gaps between separator bands.
        Also handles the edge case where no lines are detected (treat entire
        frame as one row zone and try to split by background colour changes).
        """
        rows: list[RowInfo] = []

        if not separators:
            # Fallback: try colour-based row segmentation
            return self._detect_by_colour_change(frame)

        # Add virtual top and bottom separators
        bounds = [0] + [s[1] for s in separators] + [height]

        for i in range(len(bounds) - 1):
            y_top = bounds[i]
            y_bot = bounds[i + 1]
            row_h = y_bot - y_top

            if self._min_row_h <= row_h <= self._max_row_h:
                rows.append(
                    RowInfo(
                        index=len(rows),
                        rect=(0, y_top, width, row_h),
                    )
                )

        return rows

    def _detect_by_colour_change(self, frame: np.ndarray) -> list[RowInfo]:
        """
        Fallback row detector based on alternating row background colours
        (common in striped GUI tables).
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        rows: list[RowInfo] = []

        # Compute row-average brightness
        row_avg = np.mean(gray, axis=1)
        # Smooth
        kernel = np.ones(3) / 3
        smoothed = np.convolve(row_avg, kernel, mode="same")

        # Detect sign changes in gradient → row boundaries
        diff = np.abs(np.gradient(smoothed))
        threshold = diff.max() * 0.25
        boundaries = [0] + list(np.where(diff > threshold)[0]) + [h]

        for i in range(len(boundaries) - 1):
            y_top = int(boundaries[i])
            y_bot = int(boundaries[i + 1])
            row_h = y_bot - y_top
            if self._min_row_h <= row_h <= self._max_row_h:
                rows.append(
                    RowInfo(index=len(rows), rect=(0, y_top, w, row_h))
                )

        return rows

    # ------------------------------------------------------------------
    # Button localisation
    # ------------------------------------------------------------------

    def _offset_select_position(self, row: RowInfo) -> tuple[int, int]:
        """
        Calculate Select button absolute screen position by combining
        the select_column config with the row's y position.
        """
        _, row_y_rel, _, row_h = row.rect

        # Select column horizontal centre (absolute screen x)
        sel_left = int(self._select_col.get("left", 0))
        sel_width = int(self._select_col.get("width", 60))
        btn_x = sel_left + sel_width // 2

        # Row centre y (absolute screen)
        table_top = int(self._table.get("top", 0))
        btn_y = table_top + row_y_rel + row_h // 2

        return btn_x, btn_y

    def _template_match_in_row(
        self, frame: np.ndarray, row: RowInfo
    ) -> Optional[tuple[int, int]]:
        """
        Run template matching inside the row's y-band within the select column.
        Returns absolute screen coordinate or None if not found.
        """
        if self._template is None:
            return None

        _, row_y, _, row_h = row.rect

        # Crop the row slice from the select column area
        sel_left_rel = max(
            0,
            int(self._select_col.get("left", 0)) - int(self._table.get("left", 0)),
        )
        sel_width = int(self._select_col.get("width", 60))
        roi = frame[row_y: row_y + row_h, sel_left_rel: sel_left_rel + sel_width]

        if roi.size == 0:
            return None

        result = cv2.matchTemplate(roi, self._template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < self._template_conf:
            return None

        # Convert back to absolute screen coords
        table_left = int(self._table.get("left", 0))
        table_top = int(self._table.get("top", 0))
        tmpl_h, tmpl_w = self._template.shape[:2]

        abs_x = table_left + sel_left_rel + max_loc[0] + tmpl_w // 2
        abs_y = table_top + row_y + max_loc[1] + tmpl_h // 2
        return abs_x, abs_y

    def _load_template(self) -> None:
        """Load the Select button template image if configured."""
        if not self._use_template or not self._template_path:
            return
        p = Path(self._template_path)
        if p.exists():
            self._template = cv2.imread(str(p))
        else:
            self._template = None

    def reload_template(self) -> None:
        """Reload template from disk (call after calibration saves one)."""
        self._load_template()
