from __future__ import annotations

import os
import contextlib
import re
import threading
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import BASE_CLIENT_HEIGHT, BASE_CLIENT_WIDTH, PlayerInfoConfig
from .models import PlayerStats, TruckPlayerIdentity


SUFFIX_MULTIPLIERS = {
    "K": 1_000,
    "M": 1_000_000,
    "B": 1_000_000_000,
}


FIELD_FOCUS = {
    "level": (0.18, 0.10, 0.78, 0.92),
    "stamina": (0.35, 0.00, 0.90, 1.00),
    "food": (0.18, 0.00, 1.00, 1.00),
    "iron": (0.18, 0.00, 1.00, 1.00),
    "gold": (0.14, 0.00, 1.00, 1.00),
    "power": (0.10, 0.12, 1.00, 1.00),
    "diamonds": (0.45, 0.40, 1.00, 1.00),
}


INTEGER_FIELDS = {"level", "stamina", "diamonds"}
RESOURCE_FIELDS = {"food", "iron", "gold"}
DIAMONDS_CONTEXT_PADDING = (72, 0, 0, 36)
RESOURCE_ICON_HSV = {
    "food": ((5, 50, 80), (30, 255, 255)),
    "iron": ((95, 40, 70), (125, 255, 255)),
    "gold": ((15, 90, 120), (45, 255, 255)),
}
RESOURCE_ANCHOR_EXPANSION = {
    "food": (4, 2, 4, 2),
    "iron": (4, 2, 4, 2),
    "gold": (12, 3, 8, 3),
}


@contextlib.contextmanager
def suppress_console_noise():
    stdout_fd = None
    stderr_fd = None
    devnull = None
    try:
        try:
            stdout_fd = os.dup(1)
            stderr_fd = os.dup(2)
            devnull = open(os.devnull, "w", encoding="utf-8", errors="ignore")
            devnull_fd = devnull.fileno()
            os.dup2(devnull_fd, 1)
            os.dup2(devnull_fd, 2)
        except OSError:
            stdout_fd = None
            stderr_fd = None
            devnull = open(os.devnull, "w", encoding="utf-8", errors="ignore")
        with contextlib.redirect_stdout(devnull):
            with contextlib.redirect_stderr(devnull):
                yield
    finally:
        if stdout_fd is not None:
            os.dup2(stdout_fd, 1)
            os.close(stdout_fd)
        if stderr_fd is not None:
            os.dup2(stderr_fd, 2)
            os.close(stderr_fd)
        if devnull is not None:
            devnull.close()


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


def parse_level_text(text: str) -> int | None:
    if not text:
        return None
    normalized = re.sub(r"\s+", "", text.upper())
    match = re.search(r"LV\.?(\d{1,3})", normalized)
    if not match:
        match = re.search(r"\b(\d{1,3})\b", normalized)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def normalize_dialog_text(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def normalize_truck_player_name(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    cleaned = re.sub(r"(#\d+)(\[[^\]]+\])", r"\1 \2", cleaned)
    cleaned = re.sub(r"^#(\d{4})\d+", r"#\1", cleaned)
    cleaned = re.sub(r"^\d+\s*(?=#\d+)", "", cleaned)
    cleaned = re.sub(r"(?<=#\d{4})\d+(?:\.\d+)?[KMB]\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+\d+(?:\.\d+)?[KMB]$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_truck_player_identity(text: str) -> TruckPlayerIdentity:
    full_name = normalize_truck_player_name(text)
    if not full_name:
        return TruckPlayerIdentity()
    match = re.match(r"^#(\d{4})\d*\s*(\[[^\]]+\])?\s*(.*)$", full_name)
    if not match:
        return TruckPlayerIdentity(full_name=full_name, player_name=full_name)
    server_id = f"#{match.group(1)}" if match.group(1) else None
    alliance_tag = match.group(2) or None
    player_name = (match.group(3) or "").strip() or None
    normalized_full_name = " ".join(
        part for part in (server_id, alliance_tag, player_name) if part
    ).strip()
    return TruckPlayerIdentity(
        full_name=normalized_full_name or full_name,
        server_id=server_id,
        alliance_tag=alliance_tag,
        player_name=player_name,
    )


def _truck_name_regions(panel_rect: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
    left, top, right, bottom = _truck_info_card_region(panel_rect)
    panel_width = max(1, right - left)
    panel_height = max(1, bottom - top)
    return [
        (
            left + int(panel_width * 0.19),
            top + int(panel_height * 0.18),
            left + int(panel_width * 0.72),
            top + int(panel_height * 0.32),
        ),
        (
            left + int(panel_width * 0.18),
            top + int(panel_height * 0.17),
            left + int(panel_width * 0.74),
            top + int(panel_height * 0.34),
        ),
    ]


def _truck_level_regions(panel_rect: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
    left, top, right, bottom = _truck_info_card_region(panel_rect)
    panel_width = max(1, right - left)
    panel_height = max(1, bottom - top)
    return [
        (
            left + int(panel_width * 0.19),
            top + int(panel_height * 0.31),
            left + int(panel_width * 0.44),
            top + int(panel_height * 0.45),
        ),
        (
            left + int(panel_width * 0.18),
            top + int(panel_height * 0.30),
            left + int(panel_width * 0.46),
            top + int(panel_height * 0.47),
        ),
    ]


def _truck_power_row_regions(panel_rect: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
    left, top, right, bottom = _truck_info_card_region(panel_rect)
    panel_width = max(1, right - left)
    panel_height = max(1, bottom - top)
    return [
        (
            left + int(panel_width * 0.19),
            top + int(panel_height * 0.43),
            left + int(panel_width * 0.48),
            top + int(panel_height * 0.59),
        ),
        (
            left + int(panel_width * 0.18),
            top + int(panel_height * 0.42),
            left + int(panel_width * 0.50),
            top + int(panel_height * 0.61),
        ),
    ]


def _truck_info_card_region(panel_rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = panel_rect
    panel_width = max(1, right - left)
    panel_height = max(1, bottom - top)
    return (
        left + int(panel_width * 0.04),
        top + int(panel_height * 0.68),
        left + int(panel_width * 0.95),
        top + int(panel_height * 0.985),
    )


@dataclass(slots=True)
class OcrRegionReader:
    config: PlayerInfoConfig
    _engine: object | None = field(init=False, default=None, repr=False)
    _disabled_reason: str | None = field(init=False, default=None, repr=False)
    _engine_lock: threading.RLock = field(init=False, default_factory=threading.RLock, repr=False)

    def extract_stats(self, frame: np.ndarray) -> PlayerStats:
        if self._disabled_reason:
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

    def extract_truck_power(self, frame: np.ndarray, icon_top_left: tuple[int, int], icon_size: tuple[int, int]) -> float | None:
        if self._disabled_reason:
            return None
        try:
            engine = self._get_engine()
        except Exception as exc:
            self._disabled_reason = str(exc)
            return None
        best_value: float | None = None
        best_score = -1
        for candidate_region in self._truck_power_regions(frame, icon_top_left, icon_size):
            crop = self._crop(frame, candidate_region)
            if crop.size == 0:
                continue
            crop = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            text = self._ocr_best_text(engine, crop, "truck_power")
            value = parse_numeric_text(text)
            if value is None:
                continue
            score = len(re.sub(r"\D", "", normalize_ocr_text(text)))
            if score > best_score:
                best_score = score
                best_value = value
        return best_value

    def extract_truck_power_from_panel(self, frame: np.ndarray, panel_rect: tuple[int, int, int, int]) -> float | None:
        if self._disabled_reason:
            return None
        try:
            engine = self._get_engine()
        except Exception as exc:
            self._disabled_reason = str(exc)
            return None

        scored_candidates: list[tuple[str, float, float]] = []
        candidates = _truck_power_row_regions(panel_rect)
        for index, region in enumerate(candidates):
            crop = self._crop(frame, self._clip_region(frame, region))
            if crop.size == 0:
                continue
            for text, confidence in self._ocr_candidates_with_variants(
                engine,
                crop,
                scale=5,
                merge_lines=False,
                allow_fallback_variants=index > 0,
            ):
                value = parse_numeric_text(text)
                if value is None:
                    continue
                normalized = normalize_ocr_text(text)
                score = len(re.sub(r"\D", "", normalized)) + confidence
                if any(unit in normalized for unit in ("K", "M", "B")):
                    score += 3
                if "." in normalized:
                    score += 1
                scored_candidates.append((normalized.upper(), value, score))
        if not scored_candidates:
            return None
        normalized_texts = {token for token, _, _ in scored_candidates}
        best_token = ""
        best_value: float | None = None
        best_score = -1.0
        for token, value, score in scored_candidates:
            if any(
                other != token
                and token.endswith(other)
                and len(token) > len(other)
                and len(token) - len(other) <= 2
                for other in normalized_texts
            ):
                score -= 2.5
            if score > best_score:
                best_score = score
                best_token = token
                best_value = value
        return best_value

    def extract_truck_player_identity_from_panel(
        self, frame: np.ndarray, panel_rect: tuple[int, int, int, int]
    ) -> TruckPlayerIdentity:
        if self._disabled_reason:
            return TruckPlayerIdentity()
        try:
            engine = self._get_engine()
        except Exception as exc:
            self._disabled_reason = str(exc)
            return TruckPlayerIdentity()

        best_text = ""
        best_score = -1.0
        for index, region in enumerate(_truck_name_regions(panel_rect)):
            crop = self._crop(frame, self._clip_region(frame, region))
            if crop.size == 0:
                continue
            for text, confidence in self._ocr_candidates_with_variants(
                engine,
                crop,
                scale=4,
                merge_lines=False,
                allow_fallback_variants=index > 0,
            ):
                cleaned = normalize_truck_player_name(text)
                if not cleaned:
                    continue
                score = confidence + len(cleaned) * 0.05
                if "#" in cleaned:
                    score += 5.0
                else:
                    score -= 4.0
                if "[" in cleaned and "]" in cleaned:
                    score += 1.5
                if re.search(r"\d+(?:\.\d+)?[KMB]", cleaned, flags=re.IGNORECASE):
                    score -= 2.0
                if "LV." in cleaned.upper() or "LV" == cleaned.upper():
                    score -= 3.0
                if "到站时间" in cleaned:
                    score -= 3.0
                if score > best_score:
                    best_score = score
                    best_text = cleaned
        return parse_truck_player_identity(best_text)

    def extract_truck_player_level_from_panel(self, frame: np.ndarray, panel_rect: tuple[int, int, int, int]) -> int | None:
        if self._disabled_reason:
            return None
        try:
            engine = self._get_engine()
        except Exception as exc:
            self._disabled_reason = str(exc)
            return None

        best_level: int | None = None
        best_score = -1.0
        for index, region in enumerate(_truck_level_regions(panel_rect)):
            crop = self._crop(frame, self._clip_region(frame, region))
            if crop.size == 0:
                continue
            for text, confidence in self._ocr_candidates_with_variants(
                engine,
                crop,
                scale=5,
                merge_lines=False,
                allow_fallback_variants=index > 0,
            ):
                level = parse_level_text(text)
                if level is None:
                    continue
                score = confidence + level * 0.001
                if "LV" in text.upper():
                    score += 1.0
                if score > best_score:
                    best_score = score
                    best_level = level
        return best_level

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

    def find_text_center_in_region(
        self,
        frame: np.ndarray,
        region: tuple[int, int, int, int],
        required_tokens: tuple[str, ...],
    ) -> tuple[int, int] | None:
        if self._disabled_reason:
            return None
        try:
            engine = self._get_engine()
        except Exception as exc:
            self._disabled_reason = str(exc)
            return None
        crop = self._crop(frame, region)
        if crop.size == 0:
            return None
        scale = 2
        enlarged = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        try:
            with self._engine_lock:
                result = engine.ocr(enlarged, cls=False)
        except Exception:
            return None
        normalized_tokens = tuple(normalize_dialog_text(token) for token in required_tokens)
        best_confidence = -1.0
        best_center: tuple[int, int] | None = None
        for text, confidence, center in self._extract_text_boxes(result, scale=scale):
            normalized = normalize_dialog_text(text)
            if not normalized:
                continue
            if not all(token in normalized for token in normalized_tokens):
                continue
            if confidence <= best_confidence:
                continue
            best_confidence = confidence
            best_center = (region[0] + center[0], region[1] + center[1])
        return best_center

    def _get_engine(self):
        with self._engine_lock:
            if self._engine is None:
                os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
                os.environ.setdefault("FLAGS_use_mkldnn", "0")
                os.environ.setdefault("FLAGS_enable_pir_api", "0")
                os.environ.setdefault("OMP_NUM_THREADS", "1")
                try:
                    with suppress_console_noise():
                        from paddleocr import PaddleOCR
                except ImportError as exc:
                    raise RuntimeError("paddleocr is required for OCR support") from exc
                with suppress_console_noise():
                    self._engine = PaddleOCR(
                        use_angle_cls=False,
                        lang=self.config.language,
                        use_gpu=self.config.use_gpu,
                        show_log=False,
                    )
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
        if field_name in RESOURCE_FIELDS:
            anchored = self._resource_anchor_region(frame, region, field_name)
            if anchored is not None and anchored not in candidates:
                expanded = self._expand_resource_anchor_region(frame, anchored, field_name)
                if expanded not in candidates:
                    candidates.append(expanded)
                candidates.append(anchored)
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

    def _resource_anchor_region(
        self,
        frame: np.ndarray,
        region: tuple[int, int, int, int],
        field_name: str,
    ) -> tuple[int, int, int, int] | None:
        bounds = RESOURCE_ICON_HSV.get(field_name)
        if bounds is None:
            return None
        left, top, right, bottom = region
        crop = frame[top:bottom, left:right]
        if crop.size == 0:
            return None
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        lower, upper = bounds
        mask = cv2.inRange(hsv, np.array(lower, dtype=np.uint8), np.array(upper, dtype=np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        icon_area = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(icon_area)
        if w < 8 or h < 8:
            return None
        digit_left = left + x + w + max(4, w // 8)
        digit_top = top + max(0, y - h // 6)
        digit_right = right
        digit_bottom = top + min(crop.shape[0], y + h + h // 5)
        return self._clip_region(frame, (digit_left, digit_top, digit_right, digit_bottom))

    def _expand_resource_anchor_region(
        self,
        frame: np.ndarray,
        region: tuple[int, int, int, int],
        field_name: str,
    ) -> tuple[int, int, int, int]:
        left, top, right, bottom = region
        width = max(1, right - left)
        height = max(1, bottom - top)
        pad_left, pad_top, pad_right, pad_bottom = RESOURCE_ANCHOR_EXPANSION.get(field_name, (4, 2, 4, 2))
        expanded = (
            left - max(pad_left, width // 6),
            top - max(pad_top, height // 6),
            right + max(pad_right, width // 10),
            bottom + max(pad_bottom, height // 6),
        )
        return self._clip_region(frame, expanded)

    def _truck_power_regions(
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
        if field_name in RESOURCE_FIELDS:
            upper_text = text.upper()
            if any(suffix in upper_text for suffix in ("K", "M", "B")):
                score += 1.5
            if "." in text:
                score += 0.75
            score += min(len(text), 8) * 0.05
        if field_name == "diamonds":
            score += len(text) * 0.1
        return score

    def _ocr_text(self, engine, crop: np.ndarray, field_name: str) -> str:
        if crop.size == 0:
            return ""
        prepared = self._prepare_crop(crop, field_name)
        text = self._ocr_best_text(engine, prepared, field_name)
        if text:
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
        scale = 4 if field_name in INTEGER_FIELDS or field_name in RESOURCE_FIELDS or field_name == "power" else 3
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
        try:
            with self._engine_lock:
                result = engine.ocr(image, cls=False)
        except Exception:
            return ""
        candidates = self._extract_candidates(result)
        return self._select_candidate(candidates, field_name)

    def _ocr_text_candidates(self, engine, image: np.ndarray, merge_lines: bool = True) -> list[tuple[str, float]]:
        try:
            with self._engine_lock:
                result = engine.ocr(image, cls=False)
        except Exception:
            return []
        return self._extract_candidates(result, merge_lines=merge_lines)

    def _ocr_candidates_with_variants(
        self,
        engine,
        crop: np.ndarray,
        scale: int = 4,
        merge_lines: bool = True,
        allow_fallback_variants: bool = True,
    ) -> list[tuple[str, float]]:
        if crop.size == 0:
            return []
        enlarged = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        candidates = self._ocr_text_candidates(engine, enlarged, merge_lines=merge_lines)
        if candidates:
            return candidates
        if not allow_fallback_variants:
            return []
        for variant in self._fallback_variants(enlarged):
            candidates.extend(self._ocr_text_candidates(engine, variant, merge_lines=merge_lines))
            if candidates:
                break
        return candidates

    def _extract_candidates(self, result, merge_lines: bool = True) -> list[tuple[str, float]]:
        candidates: list[tuple[str, float]] = []
        for line in result or []:
            line_candidates: list[tuple[float, str, float]] = []
            for item in line or []:
                if len(item) >= 2 and item[1]:
                    text = str(item[1][0])
                    confidence = float(item[1][1]) if len(item[1]) > 1 else 0.0
                    candidates.append((text, confidence))
                    box = item[0] if item else None
                    if box:
                        xs = [point[0] for point in box]
                        line_candidates.append((min(xs), text, confidence))
            if merge_lines and len(line_candidates) >= 2:
                ordered = sorted(line_candidates, key=lambda item: item[0])
                merged_text = "".join(text for _, text, _ in ordered)
                merged_confidence = sum(confidence for _, _, confidence in ordered) / len(ordered)
                candidates.append((merged_text, merged_confidence))
                for index in range(len(ordered) - 1):
                    pair = ordered[index : index + 2]
                    pair_text = "".join(text for _, text, _ in pair)
                    pair_confidence = sum(confidence for _, _, confidence in pair) / len(pair)
                    candidates.append((pair_text, pair_confidence))
        return candidates

    def _extract_text_boxes(self, result, scale: int = 1) -> list[tuple[str, float, tuple[int, int]]]:
        items: list[tuple[str, float, tuple[int, int]]] = []
        for line in result or []:
            for item in line or []:
                if len(item) < 2 or not item[1]:
                    continue
                box = item[0]
                text = str(item[1][0])
                confidence = float(item[1][1]) if len(item[1]) > 1 else 0.0
                if not box:
                    continue
                xs = [point[0] for point in box]
                ys = [point[1] for point in box]
                center = (int(round(sum(xs) / len(xs) / max(1, scale))), int(round(sum(ys) / len(ys) / max(1, scale))))
                items.append((text, confidence, center))
        return items

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
                match = re.search(r"\d+(?:\.\d+)?[KMB]?", normalized.upper())
                if not match:
                    continue
                token = match.group(0)
            else:
                match = re.search(r"\d+(?:\.\d+)?[KMB]?", normalized.upper())
                if not match:
                    continue
                token = match.group(0)
                if field_name in RESOURCE_FIELDS and token[-1:] not in {"K", "M", "B"} and "." in token:
                    token = f"{token}M"
            score = len(token) + confidence
            if field_name == "power":
                if token[-1:] in {"K", "M", "B"}:
                    score += 1.0
                else:
                    score -= 1.5
            if field_name in RESOURCE_FIELDS and token[-1:] in {"K", "M", "B"}:
                score += 0.8
            if score > best_score:
                best_score = score
                best_text = token
        return best_text
