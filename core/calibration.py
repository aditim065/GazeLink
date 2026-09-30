"""Robust user-specific gaze calibration and screen-coordinate mapping."""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
from statistics import median
import sys
import tempfile
from typing import Iterable, Sequence

import numpy as np

try:
    from .. import config
    from ..utils.runtime import get_data_path, get_logger
except ImportError:
    import config
    from utils.runtime import get_data_path, get_logger


class Calibrator:
    """Fit a compact two-dimensional gaze model from nine screen targets."""

    QUALITY_GOOD = "GOOD"
    QUALITY_ACCEPTABLE = "ACCEPTABLE"
    QUALITY_MODERATE = "MODERATE"
    QUALITY_UNUSABLE = "UNUSABLE"

    TARGETS = (
        ("top_left", 0.12, 0.15),
        ("top_center", 0.50, 0.15),
        ("top_right", 0.88, 0.15),
        ("middle_left", 0.12, 0.50),
        ("center", 0.50, 0.50),
        ("middle_right", 0.88, 0.50),
        ("bottom_left", 0.12, 0.85),
        ("bottom_center", 0.50, 0.85),
        ("bottom_right", 0.88, 0.85),
    )
    REQUIRED_POINTS = tuple(point[0] for point in TARGETS)
    TARGET_COORDINATES = {
        name: (x, y) for name, x, y in TARGETS
    }
    VERIFICATION_TARGETS = (
        ("verify_left", 0.18, 0.50),
        ("verify_center", 0.50, 0.50),
        ("verify_right", 0.82, 0.50),
        ("verify_top", 0.50, 0.22),
        ("verify_bottom", 0.50, 0.78),
    )
    VERIFICATION_COORDINATES = {
        name: (x, y) for name, x, y in VERIFICATION_TARGETS
    }
    PURSUIT_WAYPOINTS = (
        (0.10, 0.15),
        (0.90, 0.15),
        (0.90, 0.38),
        (0.10, 0.38),
        (0.10, 0.62),
        (0.90, 0.62),
        (0.90, 0.85),
        (0.10, 0.85),
    )
    REGIONAL_CALIBRATION_POINTS = {
        "left": ("top_left", "middle_left", "bottom_left"),
        "center": ("top_center", "center", "bottom_center"),
        "right": ("top_right", "middle_right", "bottom_right"),
        "top": ("top_left", "top_center", "top_right"),
        "bottom": ("bottom_left", "bottom_center", "bottom_right"),
    }
    READINESS_EXCELLENT = "EXCELLENT"
    READINESS_GOOD = "GOOD"
    READINESS_EMERGENCY = "EMERGENCY READY"
    READINESS_POOR = "POOR"
    FEATURE_NAMES = (
        "left_horizontal",
        "right_horizontal",
        "left_vertical",
        "right_vertical",
        "average_horizontal",
        "average_vertical",
        "face_center_x",
        "face_center_y",
        "normalized_eye_distance",
    )
    POLYNOMIAL_TERMS = (
        "1",
        *FEATURE_NAMES,
        "average_horizontal^2",
        "average_vertical^2",
        "average_horizontal*average_vertical",
        "left_horizontal*right_horizontal",
        "left_vertical*right_vertical",
        "average_horizontal*face_center_x",
        "average_vertical*face_center_y",
    )

    def __init__(self) -> None:
        self.method = config.CALIBRATION_STATIC_MODE
        self.duration_seconds = 0.0
        self.selected_pursuit_lag_ms: int | None = None
        self.pursuit_samples: list[dict[str, object]] = []
        self.pursuit_targets: list[tuple[float, float, float]] = []
        self.pursuit_coverage: dict[str, object] = {}
        self.pursuit_holdout_metrics: dict[str, object] = {}
        self.pursuit_total_frames = 0
        self.pursuit_valid_frames = 0
        self.online_correction = [0.0, 0.0]
        self.target_tolerances: dict[str, float] = {}
        self.samples: list[float] = []
        self.point_samples: dict[str, list[tuple[float, ...]]] = {
            point: [] for point in self.REQUIRED_POINTS
        }
        self.coefficients_x: np.ndarray | None = None
        self.coefficients_y: np.ndarray | None = None
        self.feature_mean: np.ndarray | None = None
        self.feature_scale: np.ndarray | None = None
        self.feature_min: np.ndarray | None = None
        self.feature_max: np.ndarray | None = None
        self.face_reference: dict[str, float] = {}
        self.correction_points: list[dict[str, float]] = []
        self.quality_metrics: dict[str, object] = {}
        self.verification_metrics: dict[str, object] = {}
        self.verification_results: list[dict[str, object]] = []
        self.readiness_metrics: dict[str, object] = {}
        self.screen_width: int | None = None
        self.screen_height: int | None = None
        self.last_error = ""

        # Kept for compatibility with the previous mapper and debug panel.
        self.calibrated_min = None
        self.calibrated_max = None
        self.calibrated_left = None
        self.calibrated_right = None
        self.calibrated_top = None
        self.calibrated_bottom = None
        self.center_horizontal = None
        self.center_vertical = None

    def set_method(self, method: str) -> None:
        """Select one supported collection method without changing its math."""
        if method not in {
            config.CALIBRATION_STATIC_MODE,
            config.CALIBRATION_FAST_MODE,
            config.CALIBRATION_PURSUIT_MODE,
        }:
            raise ValueError(f"Unsupported calibration mode: {method}")
        self.method = method

    def set_target_tolerances(self, tolerances: dict[str, float]) -> None:
        """Store UI-derived tolerances used only for capability readiness."""
        cleaned = {}
        for name in ("emergency_pixels", "typing_pixels"):
            try:
                value = float(tolerances[name])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0:
                cleaned[name] = value
        self.target_tolerances = cleaned

    def add_pursuit_target(self, timestamp: float, x: float, y: float) -> bool:
        """Record the known moving-target position for later lag alignment."""
        try:
            values = float(timestamp), float(x), float(y)
        except (TypeError, ValueError):
            return False
        if not all(math.isfinite(value) for value in values):
            return False
        self.pursuit_targets.append(values)
        self.pursuit_total_frames += 1
        return True

    def add_pursuit_sample(self, timestamp: float, features: Sequence[float]) -> bool:
        """Store one valid feature sample; blink/invalid frames are ignored."""
        vector = self._validated_vector(features)
        try:
            sample_time = float(timestamp)
        except (TypeError, ValueError):
            return False
        if vector is None or not math.isfinite(sample_time):
            return False
        self.pursuit_samples.append({
            "timestamp": sample_time,
            "features": tuple(float(value) for value in vector),
        })
        self.pursuit_valid_frames += 1
        return True

    @classmethod
    def build_pursuit_path(cls, sample_count: int | None = None) -> list[tuple[float, float]]:
        """Return an arc-length sampled, rounded serpentine path.

        This is an experimental webcam/MediaPipe adaptation, not a claim of
        research-grade smooth-pursuit calibration accuracy.
        """
        count = max(2, int(sample_count or config.PURSUIT_PATH_SAMPLES))
        points = np.asarray(cls.PURSUIT_WAYPOINTS, dtype=float)
        dense: list[np.ndarray] = []
        subdivisions = 120
        for index in range(len(points) - 1):
            p0 = points[max(0, index - 1)]
            p1 = points[index]
            p2 = points[index + 1]
            p3 = points[min(len(points) - 1, index + 2)]
            for step in range(subdivisions):
                t = step / subdivisions
                value = 0.5 * (
                    2.0 * p1
                    + (-p0 + p2) * t
                    + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t * t
                    + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t * t * t
                )
                dense.append(np.clip(value, (0.08, 0.12), (0.92, 0.88)))
        dense.append(points[-1])
        curve = np.asarray(dense)
        lengths = np.concatenate(([0.0], np.cumsum(np.linalg.norm(np.diff(curve, axis=0), axis=1))))
        requested = np.linspace(0.0, lengths[-1], count)
        x = np.interp(requested, lengths, curve[:, 0])
        y = np.interp(requested, lengths, curve[:, 1])
        return [(float(px), float(py)) for px, py in zip(x, y)]

    @staticmethod
    def pursuit_position(path: Sequence[Sequence[float]], progress: float) -> tuple[float, float]:
        """Interpolate a precomputed pursuit path at progress 0..1."""
        if not path:
            raise ValueError("Pursuit path is empty")
        position = max(0.0, min(1.0, float(progress))) * (len(path) - 1)
        before = int(math.floor(position))
        after = min(len(path) - 1, before + 1)
        fraction = position - before
        return (
            float(path[before][0]) * (1.0 - fraction) + float(path[after][0]) * fraction,
            float(path[before][1]) * (1.0 - fraction) + float(path[after][1]) * fraction,
        )

    def add_sample(self, ratio, vertical_ratio=None, point=None) -> bool:
        """Add a legacy value or a complete sample for a named target."""
        if point is None:
            if ratio is None:
                return False
            try:
                self.samples.append(float(ratio))
            except (TypeError, ValueError):
                return False
            return True
        if vertical_ratio is not None:
            # Compatibility with the old three-value callback. Such samples
            # cannot fit the new model but remain useful to legacy callers.
            return self.add_point_sample(point, ratio, vertical_ratio)
        return self.add_feature_sample(point, ratio)

    def add_feature_sample(self, point: str, features: Sequence[float]) -> bool:
        """Store one finite nine-value feature vector for a calibration point."""
        name = str(point).strip().lower()
        vector = self._validated_vector(features)
        if name not in self.point_samples or vector is None:
            return False
        self.point_samples[name].append(tuple(float(value) for value in vector))
        return True

    def add_point_sample(
        self, point: str, horizontal_ratio: float, vertical_ratio: float
    ) -> bool:
        """Retain the old API without pretending two values form a full model."""
        try:
            horizontal = float(horizontal_ratio)
            vertical = float(vertical_ratio)
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(horizontal) and math.isfinite(vertical)):
            return False
        name = str(point).strip().lower()
        if name not in self.point_samples:
            return False
        self.last_error = (
            "Nine-feature samples are required for polynomial calibration."
        )
        return False

    def clear_points(self, points: Iterable[str]) -> None:
        """Clear only selected calibration targets for regional retry."""
        for point in points:
            name = str(point).strip().lower()
            if name in self.point_samples:
                self.point_samples[name].clear()
        self._clear_model()

    def create_snapshot(self) -> dict[str, object]:
        """Return a fully independent in-memory copy of this candidate."""
        return copy.deepcopy(self.__dict__)

    def restore_snapshot(self, snapshot: dict[str, object]) -> None:
        """Restore every candidate field from an independent deep snapshot."""
        if not isinstance(snapshot, dict):
            raise TypeError("Calibration snapshot must be a dictionary")
        restored = copy.deepcopy(snapshot)
        required = {
            "point_samples",
            "coefficients_x",
            "coefficients_y",
            "feature_mean",
            "feature_scale",
            "feature_min",
            "feature_max",
            "correction_points",
            "face_reference",
            "quality_metrics",
            "verification_metrics",
            "verification_results",
            "readiness_metrics",
        }
        if not required.issubset(restored):
            raise ValueError("Calibration snapshot is incomplete")
        self.__dict__.clear()
        self.__dict__.update(restored)

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, object]) -> "Calibrator":
        """Create a separate working candidate from a preserved snapshot."""
        candidate = cls()
        candidate.restore_snapshot(snapshot)
        return candidate

    @staticmethod
    def comparison_score(metrics: dict[str, object]) -> float:
        """Return a balanced error score; lower candidate scores are better."""
        try:
            mean_error = float(metrics["mean_error"])
            median_error = float(metrics["median_error"])
            max_error = float(metrics["max_error"])
            regions = (
                float(metrics["left_mean_error"]),
                float(metrics["center_mean_error"]),
                float(metrics["right_mean_error"]),
            )
        except (KeyError, TypeError, ValueError):
            return math.inf
        values = (mean_error, median_error, max_error, *regions)
        if not all(math.isfinite(value) and value >= 0.0 for value in values):
            return math.inf
        worst_region = max(regions)
        regional_spread = worst_region - min(regions)
        return (
            mean_error
            + config.CALIBRATION_SCORE_MEDIAN_WEIGHT * median_error
            + config.CALIBRATION_SCORE_MAX_WEIGHT * max_error
            + config.CALIBRATION_SCORE_WORST_REGION_WEIGHT * worst_region
            + config.CALIBRATION_SCORE_REGIONAL_SPREAD_WEIGHT * regional_spread
        )

    @classmethod
    def is_better_candidate(
        cls,
        candidate_metrics: dict[str, object],
        reference_metrics: dict[str, object],
    ) -> bool:
        """Return True only for a genuinely lower balanced score."""
        return cls.comparison_score(candidate_metrics) < (
            cls.comparison_score(reference_metrics) - 1e-6
        )

    @staticmethod
    def _quality_scale(screen_width: int | None, screen_height: int | None) -> float:
        """Scale pixel thresholds relative to a 1920x1080 reference screen."""
        try:
            width = int(screen_width)
            height = int(screen_height)
        except (TypeError, ValueError):
            return 1.0
        if width <= 0 or height <= 0:
            return 1.0
        return math.hypot(width, height) / math.hypot(1920, 1080)

    @classmethod
    def quality_thresholds(
        cls, screen_width: int | None, screen_height: int | None
    ) -> dict[str, float]:
        """Return resolution-scaled absolute usability thresholds."""
        scale = cls._quality_scale(screen_width, screen_height)
        return {
            "good_mean": config.CALIBRATION_GOOD_MEAN_ERROR * scale,
            "good_max": config.CALIBRATION_GOOD_MAX_ERROR * scale,
            "good_region": config.CALIBRATION_GOOD_REGION_ERROR * scale,
            "acceptable_mean": config.CALIBRATION_ACCEPTABLE_MEAN_ERROR * scale,
            "acceptable_max": config.CALIBRATION_ACCEPTABLE_MAX_ERROR * scale,
            "acceptable_region": config.CALIBRATION_ACCEPTABLE_REGION_ERROR * scale,
            "moderate_mean": config.CALIBRATION_MODERATE_MEAN_ERROR * scale,
            "moderate_max": config.CALIBRATION_MODERATE_MAX_ERROR * scale,
            "moderate_region": config.CALIBRATION_MODERATE_REGION_ERROR * scale,
        }

    @classmethod
    def is_usable_quality(
        cls,
        metrics: dict[str, object],
        screen_width: int | None = 1920,
        screen_height: int | None = 1080,
    ) -> bool:
        """Return whether quality may be used under the current policy."""
        state = cls.classify_quality(
            metrics,
            screen_width=screen_width,
            screen_height=screen_height,
        )
        return state in {cls.QUALITY_GOOD, cls.QUALITY_ACCEPTABLE} or (
            state == cls.QUALITY_MODERATE
            and config.ALLOW_MODERATE_CALIBRATION
        )

    @classmethod
    def _quality_values(cls, metrics: dict[str, object]) -> dict[str, float] | None:
        """Extract complete finite overall and regional quality values."""
        try:
            values = {
                "mean": float(metrics["mean_error"]),
                "max": float(metrics["max_error"]),
                "left": float(metrics["left_mean_error"]),
                "center": float(metrics["center_mean_error"]),
                "right": float(metrics["right_mean_error"]),
            }
        except (KeyError, TypeError, ValueError):
            return None
        if not all(math.isfinite(value) and value >= 0.0 for value in values.values()):
            return None
        return values

    @classmethod
    def classify_quality(
        cls,
        metrics: dict[str, object],
        *,
        screen_width: int | None = 1920,
        screen_height: int | None = 1080,
    ) -> str:
        """Classify complete metrics into practical patient-facing levels."""
        values = cls._quality_values(metrics)
        if values is None:
            return cls.QUALITY_UNUSABLE
        thresholds = cls.quality_thresholds(screen_width, screen_height)
        worst_region = max(values["left"], values["center"], values["right"])
        levels = (
            (
                cls.QUALITY_GOOD,
                thresholds["good_mean"],
                thresholds["good_max"],
                thresholds["good_region"],
            ),
            (
                cls.QUALITY_ACCEPTABLE,
                thresholds["acceptable_mean"],
                thresholds["acceptable_max"],
                thresholds["acceptable_region"],
            ),
            (
                cls.QUALITY_MODERATE,
                thresholds["moderate_mean"],
                thresholds["moderate_max"],
                thresholds["moderate_region"],
            ),
        )
        for state, mean_limit, max_limit, region_limit in levels:
            if (
                values["mean"] <= mean_limit
                and values["max"] <= max_limit
                and worst_region <= region_limit
            ):
                return state
        return cls.QUALITY_UNUSABLE

    @classmethod
    def is_severely_poor_quality(
        cls,
        metrics: dict[str, object],
        screen_width: int | None = 1920,
        screen_height: int | None = 1080,
    ) -> bool:
        """Return whether quality is outside all permitted practical levels."""
        return cls.classify_quality(
            metrics,
            screen_width=screen_width,
            screen_height=screen_height,
        ) == cls.QUALITY_UNUSABLE

    @classmethod
    def is_preferred_candidate(
        cls,
        candidate_metrics: dict[str, object],
        reference_metrics: dict[str, object],
        screen_width: int | None = 1920,
        screen_height: int | None = 1080,
    ) -> bool:
        """Rank completed candidates by the existing balanced quality score."""
        return cls.is_better_candidate(candidate_metrics, reference_metrics)

    @classmethod
    def should_automatically_retry_points(
        cls,
        quality_state: str,
        poor_point_count: int,
        automatic_retry_count: int,
    ) -> bool:
        """Enforce the single bounded regional-retry policy."""
        return (
            quality_state in {cls.QUALITY_ACCEPTABLE, cls.QUALITY_MODERATE}
            and 0 < int(poor_point_count) <= config.MAX_AUTOMATIC_POOR_POINTS
            and int(automatic_retry_count)
            < config.MAX_AUTOMATIC_POOR_POINT_RETRIES
        )

    def finish(
        self,
        screen_width: int | None = None,
        screen_height: int | None = None,
    ) -> bool:
        """Fit ridge-regularized X and Y models after robust sample filtering."""
        if not any(self.point_samples.values()):
            return self._finish_legacy_horizontal()
        if screen_width is None or screen_height is None:
            self.last_error = "Calibration screen dimensions are required."
            return False
        if int(screen_width) <= 0 or int(screen_height) <= 0:
            self.last_error = "Calibration screen dimensions must be positive."
            return False

        minimum_valid_samples = (
            config.FAST_CALIBRATION_MIN_VALID_SAMPLES
            if self.method == config.CALIBRATION_FAST_MODE
            else config.CALIBRATION_MIN_VALID_SAMPLES
        )
        filtered: dict[str, np.ndarray] = {}
        for point in self.REQUIRED_POINTS:
            clean = self._robust_samples(
                self.point_samples[point], minimum_valid_samples
            )
            if len(clean) < minimum_valid_samples:
                self.last_error = (
                    f"Not enough valid samples for {point.replace('_', ' ')}: "
                    f"{len(clean)}/{minimum_valid_samples}."
                )
                return False
            filtered[point] = clean

        all_features = np.vstack(tuple(filtered.values()))
        self.feature_mean = np.median(all_features, axis=0)
        self.feature_scale = np.std(all_features, axis=0)
        self.feature_scale[self.feature_scale < 1e-6] = 1.0
        self.feature_min = np.min(all_features, axis=0)
        self.feature_max = np.max(all_features, axis=0)

        design_rows: list[np.ndarray] = []
        target_x: list[float] = []
        target_y: list[float] = []
        for point in self.REQUIRED_POINTS:
            x, y = self.TARGET_COORDINATES[point]
            for vector in filtered[point]:
                design_rows.append(self._polynomial_features(vector))
                target_x.append(x)
                target_y.append(y)
        design = np.vstack(design_rows)
        self.coefficients_x = self._ridge_fit(design, np.asarray(target_x))
        self.coefficients_y = self._ridge_fit(design, np.asarray(target_y))
        self.screen_width = int(screen_width)
        self.screen_height = int(screen_height)

        representatives = {
            point: np.median(samples, axis=0)
            for point, samples in filtered.items()
        }
        centre = representatives["center"]
        self.center_horizontal = float(centre[4])
        self.center_vertical = float(centre[5])
        self.calibrated_left = float(representatives["middle_left"][4])
        self.calibrated_right = float(representatives["middle_right"][4])
        self.calibrated_top = float(representatives["top_center"][5])
        self.calibrated_bottom = float(representatives["bottom_center"][5])
        self.calibrated_min = self.calibrated_left
        self.calibrated_max = self.calibrated_right
        self.face_reference = {
            "face_center_x": float(self.feature_mean[6]),
            "face_center_y": float(self.feature_mean[7]),
            "eye_distance": float(self.feature_mean[8]),
        }
        self.correction_points = []
        self.quality_metrics = self._model_quality(representatives)
        self.verification_metrics = {}
        self.verification_results = []
        self.readiness_metrics = {}
        self.last_error = ""
        return True

    def finish_pursuit(self, screen_width: int, screen_height: int) -> bool:
        """Select temporal lag, fit the existing ridge model, and hold out 20%."""
        width, height = self._validated_size(screen_width, screen_height)
        if len(self.pursuit_samples) < config.PURSUIT_MIN_VALID_SAMPLES:
            self.last_error = (
                "Not enough valid smooth-pursuit samples: "
                f"{len(self.pursuit_samples)}/{config.PURSUIT_MIN_VALID_SAMPLES}."
            )
            return False
        if len(self.pursuit_targets) < 2:
            self.last_error = "Smooth-pursuit target history is incomplete."
            return False

        candidates: list[dict[str, object]] = []
        for lag_ms in config.PURSUIT_LAG_CANDIDATES_MS:
            pairs = self._pursuit_pairs(float(lag_ms) / 1000.0)
            balanced, counts = self._balanced_pursuit_pairs(pairs)
            covered = sum(
                count >= config.PURSUIT_MIN_SAMPLES_PER_BIN
                for count in counts.values()
            )
            if (
                covered < config.PURSUIT_MIN_COVERED_BINS
                or len(balanced) < config.PURSUIT_MIN_VALID_SAMPLES // 2
            ):
                continue
            training, holdout = self._split_spatial_holdout(balanced)
            if not training or not holdout:
                continue
            model = self._fit_pursuit_dataset(training)
            metrics = self._evaluate_pursuit_model(model, holdout, width, height)
            candidates.append({
                "lag_ms": int(lag_ms),
                "model": model,
                "metrics": metrics,
                "coverage": counts,
                "training_count": len(training),
                "holdout_count": len(holdout),
                "selection_error": float(metrics["median_error"]),
            })
        if not candidates:
            self.last_error = (
                "Smooth-pursuit coverage was incomplete. Please keep following "
                "the dot across the full path."
            )
            return False

        best = min(candidates, key=lambda item: float(item["selection_error"]))
        model = dict(best["model"])
        self.feature_mean = np.asarray(model["feature_mean"], dtype=float)
        self.feature_scale = np.asarray(model["feature_scale"], dtype=float)
        self.feature_min = np.asarray(model["feature_min"], dtype=float)
        self.feature_max = np.asarray(model["feature_max"], dtype=float)
        self.coefficients_x = np.asarray(model["coefficients_x"], dtype=float)
        self.coefficients_y = np.asarray(model["coefficients_y"], dtype=float)
        self.screen_width, self.screen_height = width, height
        self.face_reference = {
            "face_center_x": float(self.feature_mean[6]),
            "face_center_y": float(self.feature_mean[7]),
            "eye_distance": float(self.feature_mean[8]),
        }
        self.method = config.CALIBRATION_PURSUIT_MODE
        self.selected_pursuit_lag_ms = int(best["lag_ms"])
        self.pursuit_coverage = {
            "grid": [config.PURSUIT_GRID_COLUMNS, config.PURSUIT_GRID_ROWS],
            "bin_counts": dict(best["coverage"]),
            "covered_bins": sum(
                value >= config.PURSUIT_MIN_SAMPLES_PER_BIN
                for value in dict(best["coverage"]).values()
            ),
            "training_samples": int(best["training_count"]),
            "holdout_samples": int(best["holdout_count"]),
            "tracking_reliability": round(
                self.pursuit_valid_frames / max(1, self.pursuit_total_frames), 4
            ),
        }
        self.pursuit_holdout_metrics = copy.deepcopy(dict(best["metrics"]))
        self.quality_metrics = copy.deepcopy(self.pursuit_holdout_metrics)
        self.verification_metrics = {}
        self.verification_results = []
        self.readiness_metrics = {}
        self.correction_points = []
        self.last_error = ""
        return True

    def _pursuit_pairs(self, lag_seconds: float) -> list[tuple[np.ndarray, float, float]]:
        target_times = np.asarray([item[0] for item in self.pursuit_targets], dtype=float)
        target_x = np.asarray([item[1] for item in self.pursuit_targets], dtype=float)
        target_y = np.asarray([item[2] for item in self.pursuit_targets], dtype=float)
        pairs = []
        for sample in self.pursuit_samples:
            timestamp = float(sample["timestamp"]) - lag_seconds
            if timestamp < target_times[0] or timestamp > target_times[-1]:
                continue
            vector = self._validated_vector(sample["features"])
            if vector is None:
                continue
            pairs.append((
                vector,
                float(np.interp(timestamp, target_times, target_x)),
                float(np.interp(timestamp, target_times, target_y)),
            ))
        return self._filter_pursuit_pairs(pairs)

    @staticmethod
    def _filter_pursuit_pairs(
        pairs: Sequence[tuple[np.ndarray, float, float]]
    ) -> list[tuple[np.ndarray, float, float]]:
        """Reject gross feature outliers and hard face-position drift."""
        if len(pairs) < 10:
            return list(pairs)
        features = np.vstack([pair[0] for pair in pairs])
        q1, q3 = np.percentile(features, (25, 75), axis=0)
        iqr = np.maximum(q3 - q1, 1e-6)
        gross_outlier = np.any(
            (features < q1 - 6.0 * iqr) | (features > q3 + 6.0 * iqr),
            axis=1,
        )
        face_reference = np.median(features[:, 6:9], axis=0)
        face_distance = np.linalg.norm(features[:, 6:8] - face_reference[:2], axis=1)
        eye_reference = max(1e-6, float(face_reference[2]))
        eye_drift = np.abs(features[:, 8] - eye_reference) / eye_reference
        accepted = (
            ~gross_outlier
            & (face_distance < config.FACE_DRIFT_BLOCK_TOLERANCE)
            & (eye_drift < config.EYE_DISTANCE_DRIFT_BLOCK_TOLERANCE)
        )
        return [pair for pair, keep in zip(pairs, accepted) if bool(keep)]

    @staticmethod
    def _pursuit_bin(x: float, y: float) -> tuple[int, int]:
        column = min(config.PURSUIT_GRID_COLUMNS - 1, max(0, int(x * config.PURSUIT_GRID_COLUMNS)))
        row = min(config.PURSUIT_GRID_ROWS - 1, max(0, int(y * config.PURSUIT_GRID_ROWS)))
        return column, row

    def _balanced_pursuit_pairs(
        self, pairs: Sequence[tuple[np.ndarray, float, float]]
    ) -> tuple[list[tuple[np.ndarray, float, float]], dict[str, int]]:
        bins: dict[tuple[int, int], list[tuple[np.ndarray, float, float]]] = {
            (column, row): []
            for row in range(config.PURSUIT_GRID_ROWS)
            for column in range(config.PURSUIT_GRID_COLUMNS)
        }
        for pair in pairs:
            bins[self._pursuit_bin(pair[1], pair[2])].append(pair)
        counts = {f"{column},{row}": len(values) for (column, row), values in bins.items()}
        balanced = []
        for values in bins.values():
            if len(values) <= config.PURSUIT_MAX_SAMPLES_PER_BIN:
                balanced.extend(values)
                continue
            indices = np.linspace(0, len(values) - 1, config.PURSUIT_MAX_SAMPLES_PER_BIN, dtype=int)
            balanced.extend(values[index] for index in indices)
        return balanced, counts

    def _split_spatial_holdout(
        self, pairs: Sequence[tuple[np.ndarray, float, float]]
    ) -> tuple[list[tuple[np.ndarray, float, float]], list[tuple[np.ndarray, float, float]]]:
        bins: dict[tuple[int, int], list[tuple[np.ndarray, float, float]]] = {}
        for pair in pairs:
            bins.setdefault(self._pursuit_bin(pair[1], pair[2]), []).append(pair)
        training, holdout = [], []
        stride = max(2, int(round(1.0 / config.PURSUIT_HOLDOUT_FRACTION)))
        for values in bins.values():
            for index, pair in enumerate(values):
                (holdout if index % stride == stride - 1 else training).append(pair)
        return training, holdout

    def _fit_pursuit_dataset(
        self, pairs: Sequence[tuple[np.ndarray, float, float]]
    ) -> dict[str, np.ndarray]:
        features = np.vstack([pair[0] for pair in pairs])
        feature_mean = np.median(features, axis=0)
        feature_scale = np.std(features, axis=0)
        feature_scale[feature_scale < 1e-6] = 1.0
        design = np.vstack([
            self._polynomial_features_for(vector, feature_mean, feature_scale)
            for vector in features
        ])
        return {
            "feature_mean": feature_mean,
            "feature_scale": feature_scale,
            "feature_min": np.min(features, axis=0),
            "feature_max": np.max(features, axis=0),
            "coefficients_x": self._ridge_fit(design, np.asarray([pair[1] for pair in pairs])),
            "coefficients_y": self._ridge_fit(design, np.asarray([pair[2] for pair in pairs])),
        }

    def _evaluate_pursuit_model(
        self,
        model: dict[str, np.ndarray],
        pairs: Sequence[tuple[np.ndarray, float, float]],
        width: int,
        height: int,
    ) -> dict[str, object]:
        results = []
        for index, (vector, target_x, target_y) in enumerate(pairs):
            terms = self._polynomial_features_for(
                vector, model["feature_mean"], model["feature_scale"]
            )
            predicted_x = self._clamp(float(terms @ model["coefficients_x"]))
            predicted_y = self._clamp(float(terms @ model["coefficients_y"]))
            dx = (target_x - predicted_x) * (width - 1)
            dy = (target_y - predicted_y) * (height - 1)
            horizontal = "left" if target_x < 1 / 3 else "right" if target_x > 2 / 3 else "center"
            vertical = "top" if target_y < 1 / 3 else "bottom" if target_y > 2 / 3 else "middle"
            results.append({
                "name": f"holdout_{vertical}_{horizontal}_{index}",
                "error_x": dx,
                "error_y": dy,
                "error": math.hypot(dx, dy),
            })
        metrics = self._error_metrics(results)
        metrics["absolute_max_error"] = metrics["max_error"]
        metrics["max_error"] = float(np.percentile([item["error"] for item in results], 95))
        return metrics

    def predict_normalized(
        self, features: Sequence[float]
    ) -> tuple[float, float]:
        """Predict an uncorrected normalized screen location."""
        self._require_model()
        vector = self._validated_vector(features)
        if vector is None:
            raise ValueError("Invalid gaze feature vector")
        terms = self._polynomial_features(vector)
        x = float(terms @ self.coefficients_x)
        y = float(terms @ self.coefficients_y)
        return self._clamp(x), self._clamp(y)

    def predict_with_debug(
        self,
        features: Sequence[float],
        screen_width: int,
        screen_height: int,
        *,
        apply_correction: bool = True,
        recent_stability: bool = True,
    ) -> dict[str, object]:
        """Predict local pixels and return mapping, correction, and confidence."""
        width, height = self._validated_size(screen_width, screen_height)
        raw_nx, raw_ny = self.predict_normalized(features)
        correction_nx, correction_ny = (0.0, 0.0)
        if apply_correction:
            correction_nx, correction_ny = self._spatial_correction(
                raw_nx, raw_ny, width, height
            )
        corrected_nx = self._clamp(raw_nx + correction_nx)
        corrected_ny = self._clamp(raw_ny + correction_ny)
        confidence = self.calculate_confidence(features, recent_stability)
        return {
            "predicted_x": raw_nx * (width - 1),
            "predicted_y": raw_ny * (height - 1),
            "correction_x": correction_nx * (width - 1),
            "correction_y": correction_ny * (height - 1),
            "corrected_x": corrected_nx * (width - 1),
            "corrected_y": corrected_ny * (height - 1),
            "confidence": confidence["confidence"],
            "face_drift": confidence["face_drift"],
            "head_drift": confidence["head_drift"],
        }

    def map_features_to_screen(
        self, features: Sequence[float], screen_width: int, screen_height: int
    ) -> tuple[int, int]:
        """Map one complete feature vector into corrected local pixels."""
        result = self.predict_with_debug(features, screen_width, screen_height)
        return int(result["corrected_x"]), int(result["corrected_y"])

    def calculate_confidence(
        self, features: Sequence[float], recent_stability: bool = True
    ) -> dict[str, object]:
        """Estimate confidence from feature range, face drift, and stability."""
        vector = self._validated_vector(features)
        if vector is None or self.feature_min is None or self.feature_max is None:
            return {"confidence": 0.0, "face_drift": math.inf, "head_drift": True}
        span = np.maximum(self.feature_max - self.feature_min, self.feature_scale)
        below = np.maximum(self.feature_min - vector, 0.0) / span
        above = np.maximum(vector - self.feature_max, 0.0) / span
        range_score = math.exp(-2.0 * float(np.mean(below + above)))

        dx = float(vector[6] - self.face_reference.get("face_center_x", vector[6]))
        dy = float(vector[7] - self.face_reference.get("face_center_y", vector[7]))
        face_drift = math.hypot(dx, dy)
        reference_distance = max(
            1e-6, self.face_reference.get("eye_distance", float(vector[8]))
        )
        distance_drift = abs(float(vector[8]) - reference_distance) / reference_distance
        centre_score = self._drift_score(
            face_drift,
            config.FACE_DRIFT_WARNING_TOLERANCE,
            config.FACE_DRIFT_BLOCK_TOLERANCE,
        )
        distance_score = self._drift_score(
            distance_drift,
            config.EYE_DISTANCE_DRIFT_WARNING_TOLERANCE,
            config.EYE_DISTANCE_DRIFT_BLOCK_TOLERANCE,
        )
        # Pixel stability is advisory. Target identity in the controller is the
        # authoritative dwell-stability signal.
        stability_score = 1.0 if recent_stability else 0.90
        confidence = range_score * min(centre_score, distance_score) * stability_score
        return {
            "confidence": self._clamp(confidence),
            "face_drift": face_drift,
            "eye_distance_drift": distance_drift,
            "face_drift_warning": (
                face_drift > config.FACE_DRIFT_WARNING_TOLERANCE
                or distance_drift > config.EYE_DISTANCE_DRIFT_WARNING_TOLERANCE
            ),
            "head_drift": (
                face_drift >= config.FACE_DRIFT_BLOCK_TOLERANCE
                or distance_drift >= config.EYE_DISTANCE_DRIFT_BLOCK_TOLERANCE
            ),
        }

    @staticmethod
    def _drift_score(value: float, warning: float, blocking: float) -> float:
        """Keep small motion unpenalized, then taper toward a hard limit."""
        if value <= warning:
            return 1.0
        if value >= blocking or blocking <= warning:
            return 0.0
        return 1.0 - (value - warning) / (blocking - warning)

    def set_verification_results(self, results: Sequence[dict]) -> dict[str, object]:
        """Store verification errors and build an IDW spatial correction map."""
        if self.screen_width is None or self.screen_height is None:
            raise RuntimeError("Calibrator not ready")
        normalized_results: list[dict[str, float | str]] = []
        for result in results:
            try:
                actual_x = float(result["actual_x"])
                actual_y = float(result["actual_y"])
                predicted_x = float(result["predicted_x"])
                predicted_y = float(result["predicted_y"])
                name = str(result["name"])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                spread = max(0.0, float(result.get("spread", 80.0)))
                valid_frames = max(0, int(result.get("valid_frames", 1)))
                expected_frames = max(
                    valid_frames, int(result.get("expected_frames", 1))
                )
            except (TypeError, ValueError):
                spread, valid_frames, expected_frames = 80.0, 1, 1
            status = str(result.get("verification_status", "OK"))
            if status not in {"OK", "LOW_SAMPLES", "NO_GAZE"}:
                status = "LOW_SAMPLES"
            error_x = actual_x - predicted_x
            error_y = actual_y - predicted_y
            normalized_results.append({
                "name": name,
                "actual_x": actual_x,
                "actual_y": actual_y,
                "predicted_x": predicted_x,
                "predicted_y": predicted_y,
                "error_x": error_x,
                "error_y": error_y,
                "error": math.hypot(error_x, error_y),
                "spread": spread,
                "valid_frames": valid_frames,
                "expected_frames": expected_frames,
                "verification_status": status,
            })
        if not normalized_results:
            raise ValueError("No valid verification results")
        self.verification_results = copy.deepcopy(normalized_results)
        self.quality_metrics = self._error_metrics(normalized_results)
        self.verification_metrics = copy.deepcopy(self.quality_metrics)
        self.correction_points = [
            {
                "x": float(item["predicted_x"]) / max(1, self.screen_width - 1),
                "y": float(item["predicted_y"]) / max(1, self.screen_height - 1),
                "dx": float(item["error_x"]) / max(1, self.screen_width - 1),
                "dy": float(item["error_y"]) / max(1, self.screen_height - 1),
                "name": str(item["name"]),
            }
            for item in normalized_results
        ]
        self.readiness_metrics = self.calculate_readiness(normalized_results)
        return dict(self.quality_metrics)

    @staticmethod
    def verification_outcome(
        valid_sample_count: int, collection_elapsed: float
    ) -> tuple[str, str] | None:
        """Return a bounded point outcome, or None while collection continues."""
        samples = max(0, int(valid_sample_count))
        elapsed = max(0.0, float(collection_elapsed))
        if samples >= config.VERIFICATION_TARGET_VALID_SAMPLES:
            return "OK", "sample target"
        if elapsed < config.VERIFICATION_MAX_SECONDS:
            return None
        if samples >= config.VERIFICATION_MIN_VALID_SAMPLES:
            return "OK", "timeout"
        if samples > 0:
            return "LOW_SAMPLES", "timeout"
        return "NO_GAZE", "timeout"

    def calculate_readiness(self, results: Sequence[dict]) -> dict[str, object]:
        """Calculate patient-facing readiness from independent verification."""
        required_regions = ("left", "center", "right", "top", "bottom")
        if not self.screen_width or not self.screen_height:
            return self._invalid_readiness("Missing calibration screen dimensions")
        usable_results: list[dict] = []
        for item in results:
            try:
                actual_x = float(item["actual_x"])
                actual_y = float(item["actual_y"])
                error = float(item["error"])
            except (KeyError, TypeError, ValueError):
                continue
            if all(math.isfinite(value) for value in (actual_x, actual_y, error)):
                usable_results.append(item)

        # Match by physical target location, not by a naming convention. This
        # also gives existing nine-point saved profiles a safe readiness score.
        by_region: dict[str, dict] = {}
        remaining = list(usable_results)
        for region, target_x, target_y in self.VERIFICATION_TARGETS:
            region_name = region.removeprefix("verify_")
            if not remaining:
                break
            expected_x = target_x * (self.screen_width - 1)
            expected_y = target_y * (self.screen_height - 1)
            selected = min(
                remaining,
                key=lambda item: math.hypot(
                    float(item["actual_x"]) - expected_x,
                    float(item["actual_y"]) - expected_y,
                ),
            )
            by_region[region_name] = selected
            remaining.remove(selected)
        if len(by_region) != len(required_regions):
            return self._invalid_readiness("Missing verification regions")

        scale = self._quality_scale(self.screen_width, self.screen_height)
        errors = [max(0.0, float(by_region[name]["error"])) for name in required_regions]
        spreads = [
            max(0.0, float(by_region[name].get("spread", 80.0)))
            for name in required_regions
        ]
        reference_errors = [value / max(scale, 1e-6) for value in errors]
        reference_spreads = [value / max(scale, 1e-6) for value in spreads]

        median_error = float(np.median(reference_errors))
        maximum_error = max(reference_errors)
        accuracy = self._piecewise_score(
            median_error, config.READINESS_ACCURACY_POINTS
        )
        outlier_fraction = self._clamp_range(
            (maximum_error - config.READINESS_OUTLIER_START_PIXELS)
            / max(
                1.0,
                config.READINESS_OUTLIER_MAX_PIXELS
                - config.READINESS_OUTLIER_START_PIXELS,
            )
        )
        accuracy = max(
            0.0,
            accuracy - outlier_fraction * config.READINESS_OUTLIER_MAX_PENALTY,
        )

        median_spread = float(np.median(reference_spreads))
        stability = self._piecewise_score(
            median_spread, config.READINESS_STABILITY_POINTS
        )

        region_scores = {
            name: self._piecewise_score(
                reference_errors[index], config.READINESS_ACCURACY_POINTS
            )
            for index, name in enumerate(required_regions)
        }
        region_values = list(region_scores.values())
        region_mean = float(np.mean(region_values))
        weakest_score = min(region_values)
        regional_spread = max(region_values) - weakest_score
        coverage = self._clamp_score(
            0.55 * region_mean + 0.45 * weakest_score - 0.15 * regional_spread
        )

        valid_frames = sum(
            max(0, int(by_region[name].get("valid_frames", 1)))
            for name in required_regions
        )
        expected_frames = sum(
            max(1, int(by_region[name].get("expected_frames", 1)))
            for name in required_regions
        )
        valid_ratio = min(1.0, valid_frames / max(1, expected_frames))
        tracking = self._piecewise_score(
            valid_ratio, config.READINESS_TRACKING_POINTS
        )

        readiness = self._clamp_score(
            0.40 * accuracy
            + 0.25 * stability
            + 0.25 * coverage
            + 0.10 * tracking
        )
        catastrophic = [
            name for name, error in zip(required_regions, reference_errors)
            if error > config.READINESS_CATASTROPHIC_REGION_ERROR
        ]
        if len(catastrophic) >= 2:
            readiness = min(readiness, config.READINESS_TWO_REGION_CAP)
        elif catastrophic:
            readiness = min(readiness, config.READINESS_SINGLE_REGION_CAP)

        emergency_readiness = self._clamp_score(
            0.20 * accuracy
            + 0.30 * stability
            + 0.35 * coverage
            + 0.15 * tracking
        )
        typing_readiness = self._clamp_score(
            0.50 * accuracy
            + 0.30 * stability
            + 0.15 * coverage
            + 0.05 * tracking
        )
        # Capability scores use current UI geometry when supplied by the
        # controller: the same pixel miss is safer for large emergency cards
        # than for enlarged letter targets.
        if self.target_tolerances:
            actual_median_error = median_error * scale
            emergency_geometry = self._piecewise_score(
                actual_median_error
                / max(1.0, self.target_tolerances.get("emergency_pixels", 1.0)),
                config.READINESS_TARGET_TOLERANCE_POINTS,
            )
            typing_geometry = self._piecewise_score(
                actual_median_error
                / max(1.0, self.target_tolerances.get("typing_pixels", 1.0)),
                config.READINESS_TARGET_TOLERANCE_POINTS,
            )
            emergency_readiness = self._clamp_score(
                0.40 * emergency_geometry
                + 0.25 * stability
                + 0.25 * coverage
                + 0.10 * tracking
            )
            typing_readiness = self._clamp_score(
                0.40 * typing_geometry
                + 0.25 * stability
                + 0.25 * coverage
                + 0.10 * tracking
            )
        rounded_readiness = int(round(readiness))
        weakest_region = min(region_scores, key=region_scores.get)
        region_labels = {
            name: self._region_label(score)
            for name, score in region_scores.items()
        }
        return {
            "valid": True,
            "score": rounded_readiness,
            "level": self.readiness_level(rounded_readiness),
            "accuracy_score": round(accuracy, 2),
            "stability_score": round(stability, 2),
            "coverage_score": round(coverage, 2),
            "tracking_score": round(tracking, 2),
            "emergency_readiness": int(round(emergency_readiness)),
            "typing_readiness": int(round(typing_readiness)),
            "emergency_ready": (
                rounded_readiness >= config.READINESS_EMERGENCY_MIN
                and emergency_readiness >= config.READINESS_EMERGENCY_MIN
            ),
            "typing_ready": (
                rounded_readiness >= config.READINESS_GOOD_MIN
                and typing_readiness >= config.READINESS_GOOD_MIN
            ),
            "region_scores": {key: round(value, 2) for key, value in region_scores.items()},
            "region_labels": region_labels,
            "region_errors": {
                name: round(errors[index], 2)
                for index, name in enumerate(required_regions)
            },
            "weakest_region": weakest_region,
            "catastrophic_regions": catastrophic,
            "median_error": round(median_error * scale, 2),
            "max_error": round(maximum_error * scale, 2),
            "median_spread": round(median_spread * scale, 2),
            "valid_frame_ratio": round(valid_ratio, 4),
        }

    @classmethod
    def readiness_level(cls, score: float) -> str:
        if score >= config.READINESS_EXCELLENT_MIN:
            return cls.READINESS_EXCELLENT
        if score >= config.READINESS_GOOD_MIN:
            return cls.READINESS_GOOD
        if score >= config.READINESS_EMERGENCY_MIN:
            return cls.READINESS_EMERGENCY
        return cls.READINESS_POOR

    def readiness_passes(self) -> bool:
        return bool(
            self.readiness_metrics.get("valid")
            and float(self.readiness_metrics.get("score", 0))
            >= config.READINESS_EMERGENCY_MIN
        )

    def get_weakest_region(self) -> str | None:
        region = self.readiness_metrics.get("weakest_region")
        return str(region) if region in self.REGIONAL_CALIBRATION_POINTS else None

    @classmethod
    def regional_calibration_points(cls, region: str) -> list[str]:
        return list(cls.REGIONAL_CALIBRATION_POINTS.get(str(region).lower(), ()))

    @classmethod
    def regional_improvement_available(
        cls,
        region: str | None,
        attempts: dict[str, int],
    ) -> bool:
        """Return whether this region still has its one bounded improvement try."""
        if region not in cls.REGIONAL_CALIBRATION_POINTS:
            return False
        try:
            used = max(0, int(attempts.get(str(region), 0)))
        except (AttributeError, TypeError, ValueError):
            return False
        return used < config.MAX_REGIONAL_IMPROVEMENT_ATTEMPTS

    @classmethod
    def is_better_readiness_candidate(
        cls,
        candidate: dict[str, object],
        reference: dict[str, object],
        retry_region: str | None = None,
    ) -> bool:
        """Require meaningful overall or safe regional readiness improvement."""
        if not candidate.get("valid"):
            return False
        if not reference.get("valid"):
            return True
        new_score = float(candidate.get("score", 0))
        old_score = float(reference.get("score", 0))
        new_regions = dict(candidate.get("region_scores", {}))
        old_regions = dict(reference.get("region_scores", {}))
        common = set(new_regions) & set(old_regions)
        if not common:
            return new_score >= old_score + config.MIN_READINESS_IMPROVEMENT
        weakest_drop = min(float(new_regions[name]) for name in common) - min(
            float(old_regions[name]) for name in common
        )
        max_error_safe = float(candidate.get("max_error", math.inf)) <= (
            float(reference.get("max_error", math.inf))
            + config.MAX_RETRY_ERROR_INCREASE_PIXELS
        )
        overall_improved = (
            new_score >= old_score + config.MIN_READINESS_IMPROVEMENT
            and weakest_drop >= -config.MAX_OTHER_REGION_DEGRADATION
            and max_error_safe
        )
        regional_improved = False
        if retry_region in common:
            regional_gain = float(new_regions[retry_region]) - float(
                old_regions[retry_region]
            )
            other_drops = [
                float(old_regions[name]) - float(new_regions[name])
                for name in common if name != retry_region
            ]
            regional_improved = (
                regional_gain >= config.MIN_REGIONAL_SCORE_IMPROVEMENT
                and new_score >= old_score - 1
                and max(other_drops, default=0.0)
                <= config.MAX_OTHER_REGION_DEGRADATION
                and max_error_safe
            )
        return overall_improved or regional_improved

    @staticmethod
    def _piecewise_score(value: float, points: Sequence[Sequence[float]]) -> float:
        ordered = sorted((float(x), float(y)) for x, y in points)
        if value <= ordered[0][0]:
            return Calibrator._clamp_score(ordered[0][1])
        for (x1, y1), (x2, y2) in zip(ordered, ordered[1:]):
            if value <= x2:
                fraction = (value - x1) / max(1e-9, x2 - x1)
                return Calibrator._clamp_score(y1 + fraction * (y2 - y1))
        return Calibrator._clamp_score(ordered[-1][1])

    @staticmethod
    def _clamp_score(value: float) -> float:
        return max(0.0, min(100.0, float(value)))

    @staticmethod
    def _clamp_range(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _region_label(score: float) -> str:
        if score >= config.READINESS_REGION_GOOD_MIN:
            return "GOOD"
        if score >= config.READINESS_REGION_MODERATE_MIN:
            return "MODERATE"
        return "WEAK"

    @classmethod
    def _invalid_readiness(cls, reason: str) -> dict[str, object]:
        return {
            "valid": False,
            "score": 0,
            "level": cls.READINESS_POOR,
            "reason": reason,
            "accuracy_score": 0.0,
            "stability_score": 0.0,
            "coverage_score": 0.0,
            "tracking_score": 0.0,
            "emergency_readiness": 0,
            "typing_readiness": 0,
            "emergency_ready": False,
            "typing_ready": False,
            "region_scores": {},
            "region_labels": {},
            "region_errors": {},
            "weakest_region": None,
            "catastrophic_regions": [],
        }

    def quality_passes(self) -> bool:
        """Return whether readiness permits at least emergency communication."""
        if self.readiness_metrics:
            return self.readiness_passes()
        return self.is_usable_quality(
            self.quality_metrics, self.screen_width, self.screen_height
        )

    def get_poor_points(self) -> list[str]:
        """Return verification targets whose error exceeds the point limit."""
        if self.screen_width is None or self.screen_height is None:
            return list(self.REQUIRED_POINTS)
        scale = math.hypot(self.screen_width, self.screen_height) / math.hypot(1920, 1080)
        limit = config.CALIBRATION_POINT_ERROR_LIMIT * scale
        point_errors = self.quality_metrics.get("point_errors", {})
        return [
            name for name in self.REQUIRED_POINTS
            if float(point_errors.get(name, math.inf)) > limit
        ]

    def save_profile(self, path: str | Path | None = None) -> bool:
        """Save numeric calibration data only; no camera images are stored."""
        if not self.is_calibrated_2d():
            return False
        target = self._profile_path(path)
        payload = {
            "version": 3,
            "method": self.method,
            "duration_seconds": self.duration_seconds,
            "selected_pursuit_lag_ms": self.selected_pursuit_lag_ms,
            "pursuit_coverage": self.pursuit_coverage,
            "pursuit_holdout_metrics": self.pursuit_holdout_metrics,
            "target_tolerances": self.target_tolerances,
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "feature_names": list(self.FEATURE_NAMES),
            "polynomial_terms": list(self.POLYNOMIAL_TERMS),
            "coefficients_x": self.coefficients_x.tolist(),
            "coefficients_y": self.coefficients_y.tolist(),
            "feature_mean": self.feature_mean.tolist(),
            "feature_scale": self.feature_scale.tolist(),
            "feature_min": self.feature_min.tolist(),
            "feature_max": self.feature_max.tolist(),
            "face_reference": self.face_reference,
            "correction_points": self.correction_points,
            "quality_metrics": self.quality_metrics,
            "verification_metrics": self.verification_metrics,
            "verification_results": self.verification_results,
            "readiness_metrics": self.readiness_metrics,
            "legacy_values": {
                "left": self.calibrated_left,
                "center_h": self.center_horizontal,
                "right": self.calibrated_right,
                "top": self.calibrated_top,
                "center_v": self.center_vertical,
                "bottom": self.calibrated_bottom,
            },
        }
        temporary_path: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, target)
            temporary_path = None
            print(f"[CAL] saved calibration profile: {target.name}")
            get_logger().info("calibration saved path=%s method=%s", target, self.method)
            return True
        except OSError as exc:
            self.last_error = f"Could not save calibration profile: {exc}"
            get_logger().exception("calibration save failed path=%s", target)
            return False
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def load_profile(
        self, screen_width: int, screen_height: int, path: str | Path | None = None
    ) -> bool:
        """Load a profile only when its screen resolution exactly matches."""
        target = self._profile_path(path)
        if (
            path is None
            and self.method == config.CALIBRATION_STATIC_MODE
            and not target.exists()
        ):
            legacy = get_data_path(config.CALIBRATION_PROFILE_PATH)
            if legacy.exists():
                target = legacy
        if not target.is_file():
            self._clear_model()
            self.last_error = "No saved calibration profile was found."
            print("[CAL] no calibration profile found")
            get_logger().info("no calibration profile found path=%s", target)
            return False
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            payload_method = str(
                payload.get("method", config.CALIBRATION_STATIC_MODE)
            )
            if payload_method != self.method:
                self._clear_model()
                self.last_error = "Saved calibration method does not match."
                print("[CAL] invalid calibration profile - starting new calibration")
                get_logger().info(
                    "calibration profile method mismatch saved=%s expected=%s",
                    payload_method,
                    self.method,
                )
                return False
            if (
                int(payload["screen_width"]) != int(screen_width)
                or int(payload["screen_height"]) != int(screen_height)
            ):
                self._clear_model()
                self.last_error = "Saved calibration resolution does not match."
                print("[CAL] invalid calibration profile - starting new calibration")
                get_logger().info(
                    "calibration profile resolution mismatch path=%s", target
                )
                return False
            if payload.get("feature_names") != list(self.FEATURE_NAMES):
                self._clear_model()
                self.last_error = "Saved calibration feature format does not match."
                print("[CAL] invalid calibration profile - starting new calibration")
                get_logger().info(
                    "calibration profile feature mismatch path=%s", target
                )
                return False
            self.coefficients_x = np.asarray(payload["coefficients_x"], dtype=float)
            self.coefficients_y = np.asarray(payload["coefficients_y"], dtype=float)
            self.feature_mean = np.asarray(payload["feature_mean"], dtype=float)
            self.feature_scale = np.asarray(payload["feature_scale"], dtype=float)
            self.feature_min = np.asarray(payload["feature_min"], dtype=float)
            self.feature_max = np.asarray(payload["feature_max"], dtype=float)
            expected_features = len(self.FEATURE_NAMES)
            expected_coefficients = len(self.POLYNOMIAL_TERMS)
            arrays = (
                (self.coefficients_x, (expected_coefficients,)),
                (self.coefficients_y, (expected_coefficients,)),
                (self.feature_mean, (expected_features,)),
                (self.feature_scale, (expected_features,)),
                (self.feature_min, (expected_features,)),
                (self.feature_max, (expected_features,)),
            )
            if any(
                value.shape != shape or not np.all(np.isfinite(value))
                for value, shape in arrays
            ) or np.any(self.feature_scale <= 0):
                raise ValueError("saved calibration arrays are invalid")
            self.face_reference = dict(payload["face_reference"])
            self.correction_points = list(payload.get("correction_points", []))
            self.quality_metrics = dict(payload.get("quality_metrics", {}))
            self.verification_metrics = dict(
                payload.get("verification_metrics", self.quality_metrics)
            )
            self.verification_results = list(
                payload.get("verification_results", [])
            )
            self.screen_width = int(screen_width)
            self.screen_height = int(screen_height)
            self.readiness_metrics = dict(payload.get("readiness_metrics", {}))
            self.duration_seconds = float(payload.get("duration_seconds", 0.0))
            selected_lag = payload.get("selected_pursuit_lag_ms")
            self.selected_pursuit_lag_ms = (
                int(selected_lag) if selected_lag is not None else None
            )
            self.pursuit_coverage = dict(payload.get("pursuit_coverage", {}))
            self.pursuit_holdout_metrics = dict(
                payload.get("pursuit_holdout_metrics", {})
            )
            self.target_tolerances = dict(payload.get("target_tolerances", {}))
            if not self.readiness_metrics and self.verification_results:
                self.readiness_metrics = self.calculate_readiness(
                    self.verification_results
                )
            legacy = payload.get("legacy_values", {})
            self.calibrated_left = legacy.get("left")
            self.center_horizontal = legacy.get("center_h")
            self.calibrated_right = legacy.get("right")
            self.calibrated_top = legacy.get("top")
            self.center_vertical = legacy.get("center_v")
            self.calibrated_bottom = legacy.get("bottom")
            self.calibrated_min = self.calibrated_left
            self.calibrated_max = self.calibrated_right
            self.last_error = ""
            self._require_model()
            print("[CAL] loaded saved calibration")
            get_logger().info("loaded calibration path=%s method=%s", target, self.method)
            return True
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._clear_model()
            self.last_error = f"Saved calibration profile is unavailable: {exc}"
            print("[CAL] invalid calibration profile - starting new calibration")
            get_logger().warning("invalid calibration profile path=%s error=%s", target, exc)
            return False

    def is_calibrated(self) -> bool:
        return self.coefficients_x is not None or (
            self.calibrated_min is not None and self.calibrated_max is not None
        )

    def is_calibrated_2d(self) -> bool:
        return self.coefficients_x is not None and self.coefficients_y is not None

    def get_error(self) -> str:
        return self.last_error

    def reset(self) -> None:
        selected_method = self.method
        self.pursuit_samples.clear()
        self.pursuit_targets.clear()
        self.pursuit_coverage = {}
        self.pursuit_holdout_metrics = {}
        self.pursuit_total_frames = 0
        self.pursuit_valid_frames = 0
        self.selected_pursuit_lag_ms = None
        self.duration_seconds = 0.0
        self.online_correction = [0.0, 0.0]
        self.samples.clear()
        for samples in self.point_samples.values():
            samples.clear()
        self._clear_model()
        self.method = selected_method
        self.last_error = ""

    # Legacy mapping methods are retained for external callers; the controller
    # uses map_features_to_screen for the new user-specific model.
    def map_to_screen(self, ratio, screen_width):
        if self.calibrated_min is None or self.calibrated_max is None:
            raise RuntimeError("Calibrator not ready")
        return self._map_axis(ratio, self.calibrated_min, self.calibrated_max, screen_width)

    def map_to_screen_2d(self, horizontal_ratio, vertical_ratio, screen_width, screen_height):
        if None in (self.calibrated_left, self.calibrated_right, self.calibrated_top, self.calibrated_bottom):
            raise RuntimeError("Calibrator not ready")
        return (
            self._map_axis(horizontal_ratio, self.calibrated_left, self.calibrated_right, screen_width),
            self._map_axis(vertical_ratio, self.calibrated_top, self.calibrated_bottom, screen_height),
        )

    def get_horizontal_mapping_debug(self, ratio, screen_width):
        x = self.map_to_screen(ratio, screen_width)
        return {"raw_mapped_x": x, "gain_adjusted_x": x}

    def _polynomial_features(self, features: Sequence[float]) -> np.ndarray:
        return self._polynomial_features_for(
            features, self.feature_mean, self.feature_scale
        )

    @staticmethod
    def _polynomial_features_for(
        features: Sequence[float], feature_mean: np.ndarray, feature_scale: np.ndarray
    ) -> np.ndarray:
        vector = np.asarray(features, dtype=float)
        normalized = (vector - feature_mean) / feature_scale
        lh, rh, lv, rv, horizontal, vertical, face_x, face_y, _eye_distance = normalized
        return np.asarray((
            1.0, *normalized,
            horizontal * horizontal,
            vertical * vertical,
            horizontal * vertical,
            lh * rh,
            lv * rv,
            horizontal * face_x,
            vertical * face_y,
        ), dtype=float)

    def online_adaptation_anchor(
        self,
        recent_gaze_points: Sequence[Sequence[float]],
        target_center: Sequence[float],
        *,
        completed_dwell: bool,
        high_confidence: bool,
        unambiguous: bool,
    ) -> tuple[float, float]:
        """Return a tiny future-use correction; disabled by default.

        This scaffold never refits polynomial coefficients and is not called by
        live control while ONLINE_GAZE_ADAPTATION is false.
        """
        if not (
            config.ONLINE_GAZE_ADAPTATION
            and completed_dwell
            and high_confidence
            and unambiguous
            and recent_gaze_points
        ):
            return tuple(self.online_correction)
        points = np.asarray(recent_gaze_points, dtype=float)
        target = np.asarray(target_center, dtype=float)
        if points.ndim != 2 or points.shape[1] != 2 or target.shape != (2,):
            return tuple(self.online_correction)
        delta = (target - np.median(points, axis=0)) * config.ONLINE_ADAPTATION_RATE
        magnitude = float(np.linalg.norm(delta))
        if magnitude > config.MAX_ONLINE_CORRECTION_PER_EVENT:
            delta *= config.MAX_ONLINE_CORRECTION_PER_EVENT / magnitude
        updated = np.asarray(self.online_correction) + delta
        total = float(np.linalg.norm(updated))
        if total > config.MAX_TOTAL_ONLINE_CORRECTION:
            updated *= config.MAX_TOTAL_ONLINE_CORRECTION / total
        self.online_correction = [float(updated[0]), float(updated[1])]
        return tuple(self.online_correction)

    @staticmethod
    def _ridge_fit(design: np.ndarray, target: np.ndarray) -> np.ndarray:
        penalty = np.eye(design.shape[1]) * config.CALIBRATION_RIDGE_LAMBDA
        penalty[0, 0] = 0.0
        matrix = design.T @ design + penalty
        vector = design.T @ target
        try:
            return np.linalg.solve(matrix, vector)
        except np.linalg.LinAlgError:
            return np.linalg.lstsq(matrix, vector, rcond=None)[0]

    def _model_quality(self, representatives: dict[str, np.ndarray]) -> dict[str, object]:
        results = []
        for name, vector in representatives.items():
            nx, ny = self.predict_normalized(vector)
            target_x, target_y = self.TARGET_COORDINATES[name]
            predicted_x = nx * (self.screen_width - 1)
            predicted_y = ny * (self.screen_height - 1)
            actual_x = target_x * (self.screen_width - 1)
            actual_y = target_y * (self.screen_height - 1)
            dx, dy = actual_x - predicted_x, actual_y - predicted_y
            results.append({"name": name, "error_x": dx, "error_y": dy, "error": math.hypot(dx, dy)})
        return self._error_metrics(results)

    @staticmethod
    def _error_metrics(results: Sequence[dict]) -> dict[str, object]:
        errors = [float(item["error"]) for item in results]
        horizontal = [abs(float(item["error_x"])) for item in results]
        vertical = [abs(float(item["error_y"])) for item in results]
        regions = {}
        for region, marker in (("left", "left"), ("center", "center"), ("right", "right"), ("top", "top"), ("middle", "middle"), ("bottom", "bottom")):
            selected = [float(item["error"]) for item in results if marker in str(item["name"])]
            regions[f"{region}_mean_error"] = float(np.mean(selected)) if selected else None
        return {
            "mean_error": float(np.mean(errors)),
            "median_error": float(np.median(errors)),
            "max_error": float(np.max(errors)),
            "mean_horizontal_error": float(np.mean(horizontal)),
            "mean_vertical_error": float(np.mean(vertical)),
            "point_errors": {str(item["name"]): float(item["error"]) for item in results},
            **regions,
        }

    def _spatial_correction(self, x: float, y: float, width: int, height: int) -> tuple[float, float]:
        if not self.correction_points:
            return 0.0, 0.0
        weighted_x = weighted_y = total_weight = 0.0
        for point in self.correction_points:
            distance_squared = (x - float(point["x"])) ** 2 + (y - float(point["y"])) ** 2
            if distance_squared < 1e-8:
                dx, dy = float(point["dx"]), float(point["dy"])
                break
            weight = 1.0 / distance_squared
            weighted_x += weight * float(point["dx"])
            weighted_y += weight * float(point["dy"])
            total_weight += weight
        else:
            dx, dy = weighted_x / total_weight, weighted_y / total_weight
        correction_x = dx * max(1, width - 1)
        correction_y = dy * max(1, height - 1)
        magnitude = math.hypot(correction_x, correction_y)
        scale = math.hypot(width, height) / math.hypot(1920, 1080)
        maximum = config.MAX_SPATIAL_CORRECTION_PIXELS * scale
        if magnitude > maximum > 0:
            correction_x *= maximum / magnitude
            correction_y *= maximum / magnitude
        return correction_x / max(1, width - 1), correction_y / max(1, height - 1)

    @staticmethod
    def _robust_samples(
        samples: Sequence[Sequence[float]],
        minimum_required: int = config.CALIBRATION_MIN_VALID_SAMPLES,
    ) -> np.ndarray:
        valid = [Calibrator._validated_vector(sample) for sample in samples]
        array = np.asarray([sample for sample in valid if sample is not None], dtype=float)
        if len(array) == 0:
            return np.empty((0, len(Calibrator.FEATURE_NAMES)))
        centre = np.median(array, axis=0)
        mad = np.median(np.abs(array - centre), axis=0)
        usable = mad > 1e-8
        if not np.any(usable):
            return array
        robust_z = np.zeros_like(array)
        robust_z[:, usable] = np.abs(array[:, usable] - centre[usable]) / (1.4826 * mad[usable])
        clean = array[np.all(robust_z[:, usable] <= config.CALIBRATION_MAD_THRESHOLD, axis=1)]
        minimum = min(len(array), max(1, int(minimum_required)))
        if len(clean) < minimum:
            scores = np.max(robust_z[:, usable], axis=1)
            clean = array[np.argsort(scores)[:minimum]]
        return clean

    @staticmethod
    def _validated_vector(features: Sequence[float] | None) -> np.ndarray | None:
        if features is None or isinstance(features, (str, bytes)):
            return None
        try:
            vector = np.asarray(tuple(features), dtype=float)
        except (TypeError, ValueError):
            return None
        if vector.shape != (len(Calibrator.FEATURE_NAMES),) or not np.all(np.isfinite(vector)):
            return None
        return vector

    def _require_model(self) -> None:
        if any(value is None for value in (self.coefficients_x, self.coefficients_y, self.feature_mean, self.feature_scale)):
            raise RuntimeError("Calibrator not ready")

    def _clear_model(self) -> None:
        self.coefficients_x = self.coefficients_y = None
        self.feature_mean = self.feature_scale = None
        self.feature_min = self.feature_max = None
        self.face_reference = {}
        self.correction_points = []
        self.quality_metrics = {}
        self.verification_metrics = {}
        self.verification_results = []
        self.readiness_metrics = {}
        self.screen_width = self.screen_height = None
        self.calibrated_min = self.calibrated_max = None
        self.calibrated_left = self.calibrated_right = None
        self.calibrated_top = self.calibrated_bottom = None
        self.center_horizontal = self.center_vertical = None

    def _finish_legacy_horizontal(self) -> bool:
        if not self.samples:
            self.last_error = "No valid calibration samples were collected."
            return False
        self.calibrated_min = min(self.samples) - 0.05
        self.calibrated_max = max(self.samples) + 0.05
        self.last_error = ""
        return True

    @staticmethod
    def _map_axis(value, start, end, size):
        if int(size) <= 0:
            raise ValueError("Mapping size must be positive")
        span = end - start
        if abs(span) < 1e-9:
            raise RuntimeError("Calibration range is too small")
        normalized = Calibrator._clamp((value - start) / span)
        return int(normalized * (int(size) - 1))

    @staticmethod
    def _validated_size(width: int, height: int) -> tuple[int, int]:
        if int(width) <= 0 or int(height) <= 0:
            raise ValueError("Mapping dimensions must be positive")
        return int(width), int(height)

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _profile_path(self, path: str | Path | None) -> Path:
        if path is not None:
            return Path(path)
        filename = (
            config.CALIBRATION_PURSUIT_PROFILE_PATH
            if self.method == config.CALIBRATION_PURSUIT_MODE
            else (
                config.CALIBRATION_FAST_PROFILE_PATH
                if self.method == config.CALIBRATION_FAST_MODE
                else config.CALIBRATION_STATIC_PROFILE_PATH
            )
        )
        return get_data_path(filename)


def _synthetic_features(x: float, y: float, reversed_x: bool = False, jitter: float = 0.0) -> tuple[float, ...]:
    """Create a plausible deterministic feature vector for non-camera tests."""
    horizontal = (0.30 + 0.40 * x) if reversed_x else (0.70 - 0.40 * x)
    left_h = horizontal + jitter
    right_h = horizontal * 0.97 + 0.012 - jitter * 0.5
    vertical = 0.28 + 0.44 * y
    left_v = vertical + jitter * 0.4
    right_v = vertical * 1.02 - 0.008 - jitter * 0.3
    return (
        left_h, right_h, left_v, right_v,
        (left_h + right_h) / 2.0,
        (left_v + right_v) / 2.0,
        0.50 + jitter * 0.1,
        0.42 - jitter * 0.1,
        0.22 + jitter * 0.05,
    )


def _synthetic_verification_results(
    errors: dict[str, float],
    *,
    spread: float,
    valid_ratio: float = 0.92,
) -> list[dict[str, object]]:
    """Build independent five-point results for readiness unit tests."""
    expected = 24
    valid = max(0, min(expected, int(round(expected * valid_ratio))))
    results: list[dict[str, object]] = []
    for name, x, y in Calibrator.VERIFICATION_TARGETS:
        region = name.removeprefix("verify_")
        actual_x = x * 1919
        actual_y = y * 1079
        error = float(errors.get(region, 0.0))
        results.append({
            "name": name,
            "actual_x": actual_x,
            "actual_y": actual_y,
            "predicted_x": actual_x - error,
            "predicted_y": actual_y,
            "error_x": error,
            "error_y": 0.0,
            "error": abs(error),
            "spread": spread,
            "valid_frames": valid,
            "expected_frames": expected,
        })
    return results


def run_synthetic_tests() -> None:
    """Exercise calibration math without a camera or GUI."""
    for reversed_x in (False, True):
        calibrator = Calibrator()
        for name, x, y in Calibrator.TARGETS:
            for index in range(36):
                jitter = ((index % 7) - 3) * 0.00035
                assert calibrator.add_feature_sample(
                    name, _synthetic_features(x, y, reversed_x, jitter)
                )
            # Obvious finite outliers must not move the robust point estimate.
            calibrator.add_feature_sample(
                name, (4.0, -3.0, 5.0, -2.0, 0.5, 0.5, 1.5, -1.0, 2.0)
            )
        assert calibrator.finish(1920, 1080), calibrator.get_error()
        for x, y in ((0.12, 0.15), (0.50, 0.50), (0.88, 0.85), (0.70, 0.30)):
            predicted = calibrator.map_features_to_screen(
                _synthetic_features(x, y, reversed_x), 1920, 1080
            )
            assert abs(predicted[0] - x * 1919) < 45
            assert abs(predicted[1] - y * 1079) < 45
        assert calibrator._validated_vector((1.0, 2.0)) is None
        assert calibrator._validated_vector([float("nan")] * 9) is None

    # Verification correction must reduce a known right-side horizontal bias.
    right_features = _synthetic_features(0.88, 0.50)
    actual_x = 0.88 * 1919
    results = []
    for name, x, y in Calibrator.TARGETS:
        raw = calibrator.predict_with_debug(
            _synthetic_features(x, y, True), 1920, 1080,
            apply_correction=False,
        )
        bias = 100.0 if "right" in name else 0.0
        results.append({
            "name": name,
            "actual_x": x * 1919,
            "actual_y": y * 1079,
            "predicted_x": float(raw["predicted_x"]) - bias,
            "predicted_y": float(raw["predicted_y"]),
        })
    calibrator.set_verification_results(results)
    # Query the correction field at the deliberately biased right location.
    biased_x = (actual_x - 100.0) / 1919
    correction_x, _ = calibrator._spatial_correction(
        biased_x, 0.50, 1920, 1080
    )
    corrected_error = abs((biased_x + correction_x) * 1919 - actual_x)
    assert corrected_error < 100.0

    # Readiness levels use five short, independent verification targets.
    readiness_calibrator = Calibrator()
    readiness_calibrator.screen_width = 1920
    readiness_calibrator.screen_height = 1080
    excellent = readiness_calibrator.calculate_readiness(
        _synthetic_verification_results(
            {region: 60 for region in ("left", "center", "right", "top", "bottom")},
            spread=25,
        )
    )
    assert excellent["score"] >= config.READINESS_EXCELLENT_MIN
    assert excellent["level"] == Calibrator.READINESS_EXCELLENT
    assert excellent["emergency_ready"] and excellent["typing_ready"]

    good_readiness = readiness_calibrator.calculate_readiness(
        _synthetic_verification_results(
            {region: 130 for region in ("left", "center", "right", "top", "bottom")},
            spread=65,
        )
    )
    assert config.READINESS_GOOD_MIN <= good_readiness["score"] < config.READINESS_EXCELLENT_MIN
    assert good_readiness["level"] == Calibrator.READINESS_GOOD

    emergency_readiness = readiness_calibrator.calculate_readiness(
        _synthetic_verification_results(
            {region: 210 for region in ("left", "center", "right", "top", "bottom")},
            spread=110,
        )
    )
    assert config.READINESS_EMERGENCY_MIN <= emergency_readiness["score"] < config.READINESS_GOOD_MIN
    assert emergency_readiness["level"] == Calibrator.READINESS_EMERGENCY
    assert not emergency_readiness["typing_ready"]

    poor_readiness = readiness_calibrator.calculate_readiness(
        _synthetic_verification_results(
            {region: 380 for region in ("left", "center", "right", "top", "bottom")},
            spread=230,
        )
    )
    assert poor_readiness["score"] < config.READINESS_EMERGENCY_MIN
    assert poor_readiness["level"] == Calibrator.READINESS_POOR

    one_sided = readiness_calibrator.calculate_readiness(
        _synthetic_verification_results(
            {"left": 80, "center": 80, "right": 450, "top": 80, "bottom": 80},
            spread=35,
        )
    )
    assert one_sided["score"] <= config.READINESS_SINGLE_REGION_CAP
    assert one_sided["weakest_region"] == "right"
    assert Calibrator.regional_calibration_points("right") == [
        "top_right", "middle_right", "bottom_right"
    ]

    def readiness_candidate(score: int, right: int, maximum: int) -> dict[str, object]:
        regions = {"left": 78, "center": 76, "right": right, "top": 75, "bottom": 74}
        return {
            "valid": True,
            "score": score,
            "max_error": maximum,
            "region_scores": regions,
        }

    before_66 = readiness_candidate(66, 42, 330)
    improved_78 = readiness_candidate(78, 68, 280)
    assert Calibrator.is_better_readiness_candidate(
        improved_78, before_66, "right"
    )
    before_78 = readiness_candidate(78, 68, 280)
    worse_70 = readiness_candidate(70, 55, 340)
    assert not Calibrator.is_better_readiness_candidate(
        worse_70, before_78, "right"
    )

    # Blink frames are excluded: a normal valid-frame ratio remains highly rated.
    blinking = readiness_calibrator.calculate_readiness(
        _synthetic_verification_results(
            {region: 100 for region in ("left", "center", "right", "top", "bottom")},
            spread=45,
            valid_ratio=0.82,
        )
    )
    assert blinking["tracking_score"] >= 95
    assert blinking["score"] >= config.READINESS_GOOD_MIN

    attempts = {region: 0 for region in Calibrator.REGIONAL_CALIBRATION_POINTS}
    assert Calibrator.regional_improvement_available("right", attempts)
    attempts["right"] = 1
    for _ in range(5):
        assert not Calibrator.regional_improvement_available("right", attempts)

    # Store the new readiness fields in the profile serialization test below.
    calibrator.set_verification_results(
        _synthetic_verification_results(
            {region: 100 for region in ("left", "center", "right", "top", "bottom")},
            spread=45,
        )
    )

    with tempfile.TemporaryDirectory() as directory:
        profile = Path(directory) / "profile.json"
        assert calibrator.save_profile(profile)
        loaded = Calibrator()
        assert loaded.load_profile(1920, 1080, profile)
        assert loaded.readiness_metrics == calibrator.readiness_metrics
        loaded_point = loaded.map_features_to_screen(right_features, 1920, 1080)
        assert 0 <= loaded_point[0] < 1920 and 0 <= loaded_point[1] < 1080
        mismatch = Calibrator()
        assert not mismatch.load_profile(1280, 720, profile)

    def metrics(mean, median_value, maximum, left, center, right):
        return {
            "mean_error": mean,
            "median_error": median_value,
            "max_error": maximum,
            "left_mean_error": left,
            "center_mean_error": center,
            "right_mean_error": right,
        }

    old = metrics(129, 101, 301, 144, 141, 102)
    worse = metrics(170, 150, 420, 205, 170, 145)
    assert not Calibrator.is_better_candidate(worse, old)

    old_worse = metrics(170, 145, 350, 190, 175, 160)
    improved = metrics(125, 100, 260, 135, 120, 115)
    assert Calibrator.is_better_candidate(improved, old_worse)

    lower_mean_bad_max = metrics(120, 98, 620, 150, 135, 125)
    assert not Calibrator.is_better_candidate(lower_mean_bad_max, old)

    good = metrics(120, 100, 280, 150, 170, 180)
    acceptable = metrics(165, 145, 340, 220, 190, 205)
    moderate = metrics(205, 180, 440, 290, 250, 270)
    unusable = metrics(380, 330, 760, 520, 410, 460)
    assert Calibrator.classify_quality(good) == Calibrator.QUALITY_GOOD
    assert Calibrator.classify_quality(acceptable) == Calibrator.QUALITY_ACCEPTABLE
    assert Calibrator.classify_quality(moderate) == Calibrator.QUALITY_MODERATE
    assert Calibrator.classify_quality(unusable) == Calibrator.QUALITY_UNUSABLE

    # The worse regional retry must not replace the acceptable initial result.
    worse_retry = metrics(190, 170, 410, 270, 250, 260)
    assert not Calibrator.is_preferred_candidate(worse_retry, acceptable)

    # A genuinely improved moderate retry becomes the preserved session best.
    moderate_initial = metrics(205, 185, 440, 290, 285, 275)
    moderate_retry = metrics(195, 170, 420, 280, 260, 270)
    assert Calibrator.is_preferred_candidate(moderate_retry, moderate_initial)
    assert Calibrator.classify_quality(moderate_retry) == Calibrator.QUALITY_MODERATE

    calibrator.screen_width, calibrator.screen_height = 1920, 1080
    calibrator.readiness_metrics = {}
    calibrator.quality_metrics = dict(unusable)
    assert not calibrator.quality_passes()
    calibrator.quality_metrics = dict(moderate)
    assert config.ALLOW_MODERATE_CALIBRATION and calibrator.quality_passes()

    # Five bad verification rounds can request at most one automatic point retry.
    automatic_retry_count = 0
    automatic_retries_started = 0
    for _attempt in range(5):
        if Calibrator.should_automatically_retry_points(
            Calibrator.QUALITY_MODERATE, 2, automatic_retry_count
        ):
            automatic_retry_count += 1
            automatic_retries_started += 1
    assert automatic_retries_started == 1
    assert not Calibrator.should_automatically_retry_points(
        Calibrator.QUALITY_GOOD, 2, 0
    )
    assert not Calibrator.should_automatically_retry_points(
        Calibrator.QUALITY_UNUSABLE, 2, 0
    )

    calibrator.quality_metrics = dict(old)
    preserved = calibrator.create_snapshot()
    original_coefficients = preserved["coefficients_x"].copy()
    original_sample = preserved["point_samples"]["center"][0]
    calibrator.coefficients_x[0] += 999.0
    calibrator.point_samples["center"][0] = tuple([9.0] * 9)
    calibrator.correction_points.append({"x": 9.0, "y": 9.0, "dx": 9.0, "dy": 9.0})
    assert np.array_equal(preserved["coefficients_x"], original_coefficients)
    assert preserved["point_samples"]["center"][0] == original_sample

    retry_all = Calibrator()
    retry_all.quality_metrics = dict(worse)
    assert not Calibrator.is_better_candidate(
        retry_all.quality_metrics, preserved["quality_metrics"]
    )
    retry_all.restore_snapshot(preserved)
    assert retry_all.quality_metrics == old
    assert np.array_equal(retry_all.coefficients_x, original_coefficients)

    # Experimental pursuit path covers the configured 5x3 grid without jumps.
    path = Calibrator.build_pursuit_path(801)
    path_bins = {Calibrator._pursuit_bin(x, y) for x, y in path}
    assert len(path_bins) >= config.PURSUIT_MIN_COVERED_BINS
    steps = np.linalg.norm(np.diff(np.asarray(path), axis=0), axis=1)
    assert float(np.max(steps)) < 3.0 * float(np.median(steps))

    # Synthetic delayed gaze recovers the configured 150 ms pursuit lag.
    pursuit = Calibrator()
    pursuit.set_method(config.CALIBRATION_PURSUIT_MODE)
    duration = config.PURSUIT_DURATION_SECONDS
    true_lag = 0.15
    for index in range(801):
        timestamp = index * duration / 800
        target = Calibrator.pursuit_position(path, timestamp / duration)
        pursuit.add_pursuit_target(timestamp, *target)
        gaze_time = timestamp - true_lag
        if gaze_time < 0:
            continue
        gaze_target = Calibrator.pursuit_position(path, gaze_time / duration)
        pursuit.add_pursuit_sample(
            timestamp, _synthetic_features(gaze_target[0], gaze_target[1])
        )
    # Invalid/blink samples are skipped without erasing accumulated samples.
    before_blink = len(pursuit.pursuit_samples)
    assert not pursuit.add_pursuit_sample(duration / 2, None)
    assert len(pursuit.pursuit_samples) == before_blink
    assert pursuit.finish_pursuit(1920, 1080), pursuit.get_error()
    assert pursuit.selected_pursuit_lag_ms == 150
    assert pursuit.pursuit_coverage["covered_bins"] >= config.PURSUIT_MIN_COVERED_BINS
    assert pursuit.pursuit_coverage["holdout_samples"] > 0
    assert pursuit.pursuit_holdout_metrics["median_error"] < 100
    with tempfile.TemporaryDirectory() as directory:
        pursuit_file = Path(directory) / "pursuit.json"
        assert pursuit.save_profile(pursuit_file)
        loaded_pursuit = Calibrator()
        loaded_pursuit.set_method(config.CALIBRATION_PURSUIT_MODE)
        assert loaded_pursuit.load_profile(1920, 1080, pursuit_file)
        assert loaded_pursuit.method == config.CALIBRATION_PURSUIT_MODE
        assert loaded_pursuit.selected_pursuit_lag_ms == 150
        assert loaded_pursuit.pursuit_coverage == pursuit.pursuit_coverage

    # Method-specific defaults never overwrite another calibration profile.
    static_path = Calibrator()._profile_path(None)
    fast_path_calibrator = Calibrator()
    fast_path_calibrator.set_method(config.CALIBRATION_FAST_MODE)
    fast_profile = fast_path_calibrator._profile_path(None)
    pursuit_path_calibrator = Calibrator()
    pursuit_path_calibrator.set_method(config.CALIBRATION_PURSUIT_MODE)
    pursuit_profile = pursuit_path_calibrator._profile_path(None)
    assert static_path.name == config.CALIBRATION_STATIC_PROFILE_PATH
    assert fast_profile.name == config.CALIBRATION_FAST_PROFILE_PATH
    assert pursuit_profile.name == config.CALIBRATION_PURSUIT_PROFILE_PATH
    assert len({static_path, fast_profile, pursuit_profile}) == 3
    assert config.ONLINE_GAZE_ADAPTATION is False
    assert config.PURSUIT_AUTOMATIC_RETRIES == 0

    # Verification completion is bounded by samples or the hard deadline.
    assert Calibrator.verification_outcome(12, 0.3) == ("OK", "sample target")
    assert Calibrator.verification_outcome(7, 1.0) is None
    assert Calibrator.verification_outcome(7, 2.0) == ("OK", "timeout")
    assert Calibrator.verification_outcome(5, 2.0) == ("LOW_SAMPLES", "timeout")
    assert Calibrator.verification_outcome(0, 2.0) == ("NO_GAZE", "timeout")
    completed_points = 0
    for sample_count in (12, 13, 12, 15, 12):
        if Calibrator.verification_outcome(sample_count, 0.6) is not None:
            completed_points += 1
    assert completed_points == 5
    # Blink frames add no samples, but elapsed time still reaches a decision.
    simulated_samples = 0
    for elapsed in (0.2, 0.7, 1.2, 2.0):
        outcome = Calibrator.verification_outcome(simulated_samples, elapsed)
    assert outcome == ("NO_GAZE", "timeout")
    assert (
        config.VERIFICATION_SETTLE_SECONDS
        + config.VERIFICATION_MAX_SECONDS
        + config.VERIFICATION_ADVANCE_MESSAGE_SECONDS
    ) < 3.0
    print("Calibration synthetic tests passed.")


if __name__ == "__main__":
    if "--synthetic-test" in sys.argv:
        run_synthetic_tests()
    else:
        print("Run with --synthetic-test for non-camera calibration checks.")
