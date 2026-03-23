from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import BASE_CLIENT_HEIGHT, BASE_CLIENT_WIDTH, OcrConfig
from .models import PlayerStats


SUFFIX_MULTIPLIERS = {
    "K": 1_000,
    "M": 1_000_000,
    "B": 1_000_000_000,
}


FIELD_FOCUS = {
    "level": (0.18, 0.10, 0.78, 0.92),
    "stamina": (0.35, 0.00, 0.90, 1.00),
    "power": (0.32, 0.35, 1.00, 1.00),
    "diamonds": (0.45, 0.40, 1.00, 1.00),
}


INTEGER_FIELDS = {"level", "stamina", "diamonds"}
DIAMONDS_CONTEXT_PADDING = (72, 0, 0, 36)


def normalize_ocr_text(text: str) -> str:
    replacements = {
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "S": "5",
        "s": "5",
    }
    cleaned = text.strip().replace(",", "").replace(" ", "")
    return "".join(replacements.get(char, char) for char in cleaned)


def parse_numeric_text(text: str) -> float | None:
    normalized = normalize_ocr_text(text)
    if not normalized:
        return None
    grouped = re.fullmatch(r"\d{1,3}(?:\.\d{3})+", normalized)
    if grouped:
        return float(normalized.replace(".", ""))
    match = re.search(r"(-?\d+(?:\.\d+)?)([KMB]?)", normalized.upper())
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    if suffix:
        number *= SUFFIX_MULTIPLIERS[suffix]
    return number


@dataclass(slots=True)
class OcrRegionReader:
    config: OcrConfig
    _engine: object | None = field(init=False, default=None, repr=False)
    _disabled_reason: str | None = field(init=False, default=None, repr=False)

    def extract_stats(self, frame: np.ndarray) -> PlayerStats:
        if not self.config.enabled or self._disabled_reason:
            return PlayerStats()
        try:
            engine = self._get_engine()
        except Exception as exc:
            self._disabled_reason = str(exc)
            return PlayerStats()
        stats = PlayerStats()
        for field_name in stats.__dataclass_fields__:
            region = self.config.regions.get(field_name)
            if not region:
                continue
            resolved_region = self._resolve_region(frame, region)
            text = self._read_field_text(engine, frame, resolved_region, field_name)
            value = parse_numeric_text(text)
            if value is None:
                continue
            if field_name in {"level", "stamina"}:
                setattr(stats, field_name, int(round(value)))
            else:
                setattr(stats, field_name, value)
        return stats

    def extract_cargo_power(self, frame: np.ndarray, icon_top_left: tuple[int, int], icon_size: tuple[int, int]) -> float | None:
        if not self.config.enabled or self._disabled_reason:
            return None
        try:
            engine = self._get_engine()
        except Exception as exc:
            self._disabled_reason = str(exc)
            return None
        best_value: float | None = None
        best_score = -1
        for candidate_region in self._cargo_power_regions(frame, icon_top_left, icon_size):
            crop = self._crop(frame, candidate_region)
            if crop.size == 0:
                continue
            crop = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            text = self._ocr_best_text(engine, crop, "cargo_power")
            value = parse_numeric_text(text)
            if value is None:
                continue
            score = len(re.sub(r"\D", "", normalize_ocr_text(text)))
            if score > best_score:
                best_score = score
                best_value = value
        return best_value

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    def describe_regions(self, frame: np.ndarray) -> dict[str, tuple[int, int, int, int]]:
        return {
            field_name: self._resolve_region(frame, region)
            for field_name, region in self.config.regions.items()
        }

    def describe_frame(self, frame: np.ndarray) -> dict[str, float | int]:
        height, width = frame.shape[:2]
        return {
            "width": width,
            "height": height,
            "scale_x": round(width / max(1, BASE_CLIENT_WIDTH), 4),
            "scale_y": round(height / max(1, BASE_CLIENT_HEIGHT), 4),
            "ocr_base_width": self.config.base_width,
            "ocr_base_height": self.config.base_height,
        }

    def _get_engine(self):
        if self._engine is None:
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            try:
                from paddleocr import PaddleOCR
            except ImportError as exc:
                raise RuntimeError("paddleocr is required for OCR support") from exc
            self._engine = PaddleOCR(use_angle_cls=False, lang=self.config.language, use_gpu=self.config.use_gpu, show_log=False)
        return self._engine

    def _crop(self, frame: np.ndarray, region: tuple[int, int, int, int]) -> np.ndarray:
        left, top, right, bottom = region
        clipped = frame[max(0, top) : max(0, bottom), max(0, left) : max(0, right)]
        if clipped.size == 0:
            return clipped
        return cv2.cvtColor(clipped, cv2.COLOR_BGR2RGB)

    def _resolve_region(self, frame: np.ndarray, region: tuple[int, int, int, int] | tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        frame_height, frame_width = frame.shape[:2]
        if all(isinstance(value, float) and 0.0 <= value <= 1.0 for value in region):
            left = int(frame_width * region[0])
            top = int(frame_height * region[1])
            right = int(frame_width * region[2])
            bottom = int(frame_height * region[3])
            return self._clip_region(frame, (left, top, right, bottom))

        base_width = max(1, self.config.base_width)
        base_height = max(1, self.config.base_height)
        scale_x = frame_width / base_width
        scale_y = frame_height / base_height
        left = int(round(region[0] * scale_x))
        top = int(round(region[1] * scale_y))
        right = int(round(region[2] * scale_x))
        bottom = int(round(region[3] * scale_y))
        return self._clip_region(frame, (left, top, right, bottom))

    def _clip_region(self, frame: np.ndarray, region: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        frame_height, frame_width = frame.shape[:2]
        left, top, right, bottom = region
        left = max(0, min(frame_width - 1, left))
        top = max(0, min(frame_height - 1, top))
        right = max(left + 1, min(frame_width, right))
        bottom = max(top + 1, min(frame_height, bottom))
        return (left, top, right, bottom)

    def _read_field_text(self, engine, frame: np.ndarray, region: tuple[int, int, int, int], field_name: str) -> str:
        best_text = ""
        best_score = -1.0
        for candidate_region in self._candidate_regions(frame, region, field_name):
            crop = self._crop(frame, candidate_region)
            text = self._ocr_text(engine, crop, field_name)
            score = self._candidate_text_score(text, field_name)
            if score > best_score:
                best_score = score
                best_text = text
        return best_text

    def _candidate_regions(
        self, frame: np.ndarray, region: tuple[int, int, int, int], field_name: str
    ) -> list[tuple[int, int, int, int]]:
        candidates = [region]
        if field_name == "diamonds":
            left, top, right, bottom = region
            pad_left, pad_top, pad_right, pad_bottom = DIAMONDS_CONTEXT_PADDING
            scale_x = frame.shape[1] / max(1, self.config.base_width)
            scale_y = frame.shape[0] / max(1, self.config.base_height)
            candidates.append(
                self._clip_region(
                    frame,
                    (
                        int(round(left - pad_left * scale_x)),
                        int(round(top - pad_top * scale_y)),
                        int(round(right + pad_right * scale_x)),
                        int(round(bottom + pad_bottom * scale_y)),
                    ),
                )
            )
        return candidates

    def _cargo_power_regions(
        self, frame: np.ndarray, icon_top_left: tuple[int, int], icon_size: tuple[int, int]
    ) -> list[tuple[int, int, int, int]]:
        icon_left, icon_top = icon_top_left
        icon_width, icon_height = icon_size
        gap = max(3, int(round(icon_width * 0.12)))
        width_options = (
            max(96, int(round(icon_width * 4.2))),
            max(118, int(round(icon_width * 5.0))),
            max(140, int(round(icon_width * 5.8))),
        )
        height_options = (
            max(28, int(round(icon_height * 1.15))),
            max(36, int(round(icon_height * 1.45))),
            max(44, int(round(icon_height * 1.75))),
        )
        top_offsets = (0, -max(2, icon_height // 8), max(2, icon_height // 8))
        regions: list[tuple[int, int, int, int]] = []
        for width in width_options:
            for height in height_options:
                for top_offset in top_offsets:
                    left = icon_left + icon_width + gap
                    top = icon_top + top_offset
                    regions.append(self._clip_region(frame, (left, top, left + width, top + height)))
        return regions

    def _candidate_text_score(self, text: str, field_name: str) -> float:
        if not text:
            return -1.0
        normalized = normalize_ocr_text(text)
        digits = re.sub(r"\D", "", normalized)
        if not digits:
            return -1.0
        score = float(len(digits))
        if field_name == "diamonds":
            score += len(text) * 0.1
        return score

    def _ocr_text(self, engine, crop: np.ndarray, field_name: str) -> str:
        if crop.size == 0:
            return ""
        prepared = self._prepare_crop(crop, field_name)
        text = self._ocr_best_text(engine, prepared, field_name)
        if text or field_name not in {"level", "stamina", "power", "diamonds"}:
            return text
        for variant in self._fallback_variants(prepared):
            text = self._ocr_best_text(engine, variant, field_name)
            if text:
                return text
        return ""

    def _prepare_crop(self, crop: np.ndarray, field_name: str) -> np.ndarray:
        focus = FIELD_FOCUS.get(field_name)
        if focus:
            height, width = crop.shape[:2]
            left = int(width * focus[0])
            top = int(height * focus[1])
            right = max(left + 1, int(width * focus[2]))
            bottom = max(top + 1, int(height * focus[3]))
            crop = crop[top:bottom, left:right]
        scale = 4 if field_name in INTEGER_FIELDS else 3
        height, width = crop.shape[:2]
        if width < 240 or height < 80:
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        return crop

    def _fallback_variants(self, crop: np.ndarray) -> list[np.ndarray]:
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        normalized = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
        _, otsu = cv2.threshold(normalized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        _, inv_otsu = cv2.threshold(normalized, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        return [
            cv2.cvtColor(normalized, cv2.COLOR_GRAY2RGB),
            cv2.cvtColor(otsu, cv2.COLOR_GRAY2RGB),
            cv2.cvtColor(inv_otsu, cv2.COLOR_GRAY2RGB),
        ]

    def _ocr_best_text(self, engine, image: np.ndarray, field_name: str) -> str:
        result = engine.ocr(image, cls=False)
        candidates = self._extract_candidates(result)
        return self._select_candidate(candidates, field_name)

    def _extract_candidates(self, result) -> list[tuple[str, float]]:
        candidates: list[tuple[str, float]] = []
        for line in result or []:
            for item in line or []:
                if len(item) >= 2 and item[1]:
                    text = str(item[1][0])
                    confidence = float(item[1][1]) if len(item[1]) > 1 else 0.0
                    candidates.append((text, confidence))
        return candidates

    def _select_candidate(self, candidates: list[tuple[str, float]], field_name: str) -> str:
        best_text = ""
        best_score = -1.0
        for raw_text, confidence in candidates:
            normalized = normalize_ocr_text(raw_text)
            if not normalized:
                continue
            if field_name in INTEGER_FIELDS:
                match = re.search(r"\d+", normalized)
                if not match:
                    continue
                token = match.group(0)
            elif field_name == "power":
                match = re.search(r"\d{1,3}(?:\.\d{3})+|\d+", normalized)
                if not match:
                    continue
                token = match.group(0)
            else:
                match = re.search(r"\d+(?:\.\d+)?[KMB]?", normalized.upper())
                if not match:
                    continue
                token = match.group(0)
            score = len(token) + confidence
            if score > best_score:
                best_score = score
                best_text = token
        return best_text
