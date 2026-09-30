"""Smoothing and stability filters for one- and two-dimensional gaze data."""

from collections import deque
import math
from statistics import median
import sys

import config


class GazeSmoother:
    """Smooth gaze ratios and report when recent values are stable."""

    def __init__(self) -> None:
        # Legacy scalar state is retained for callers that use horizontal gaze only.
        self.buffer: deque[float] = deque(maxlen=config.STABILITY_FRAMES)
        self.smoothed_value: float | None = None

        self.horizontal_raw: deque[float] = deque(
            maxlen=config.HORIZONTAL_MEDIAN_WINDOW
        )
        self.vertical_raw: deque[float] = deque(
            maxlen=config.VERTICAL_MEDIAN_WINDOW
        )
        self.horizontal_buffer: deque[float] = deque(
            maxlen=config.STABILITY_FRAMES
        )
        self.vertical_buffer: deque[float] = deque(maxlen=config.STABILITY_FRAMES)
        self.smoothed_horizontal: float | None = None
        self.smoothed_vertical: float | None = None

    def update(self, raw_ratio: float) -> float:
        """Smooth one horizontal gaze ratio and add it to the legacy buffer."""
        value = float(raw_ratio)
        if self.smoothed_value is None:
            self.smoothed_value = value
        else:
            alpha = config.SMOOTHING_ALPHA
            self.smoothed_value = (
                alpha * value + (1.0 - alpha) * self.smoothed_value
            )

        self.buffer.append(self.smoothed_value)
        return self.smoothed_value

    def update_pair(
        self, horizontal_ratio: float, vertical_ratio: float
    ) -> tuple[float, float]:
        """Median-filter and exponentially smooth a two-dimensional gaze sample."""
        self.horizontal_raw.append(float(horizontal_ratio))
        self.vertical_raw.append(float(vertical_ratio))

        horizontal = median(self.horizontal_raw)
        vertical = median(self.vertical_raw)
        horizontal_alpha = config.HORIZONTAL_SMOOTHING_ALPHA
        vertical_alpha = config.VERTICAL_SMOOTHING_ALPHA

        if self.smoothed_horizontal is None:
            self.smoothed_horizontal = horizontal
            self.smoothed_vertical = vertical
        else:
            self.smoothed_horizontal = (
                horizontal_alpha * horizontal
                + (1.0 - horizontal_alpha) * self.smoothed_horizontal
            )
            self.smoothed_vertical = (
                vertical_alpha * vertical
                + (1.0 - vertical_alpha) * self.smoothed_vertical
            )

        self.horizontal_buffer.append(self.smoothed_horizontal)
        self.vertical_buffer.append(self.smoothed_vertical)
        return self.smoothed_horizontal, self.smoothed_vertical

    def is_stable(self) -> bool:
        """Return whether the legacy scalar buffer is full and sufficiently tight."""
        return (
            len(self.buffer) == config.STABILITY_FRAMES
            and max(self.buffer) - min(self.buffer) < 0.05
        )

    def is_pair_stable(self) -> bool:
        """Return whether both gaze axes have full, sufficiently tight buffers."""
        if (
            len(self.horizontal_buffer) < config.STABILITY_FRAMES
            or len(self.vertical_buffer) < config.STABILITY_FRAMES
        ):
            return False

        horizontal_range = max(self.horizontal_buffer) - min(
            self.horizontal_buffer
        )
        vertical_range = max(self.vertical_buffer) - min(self.vertical_buffer)
        return (
            horizontal_range < config.GAZE_STABILITY_THRESHOLD_X
            and vertical_range < config.GAZE_STABILITY_THRESHOLD_Y
        )

    def get_stable_value(self) -> float | None:
        """Return the legacy buffer average when stable."""
        if not self.is_stable():
            return None
        return sum(self.buffer) / len(self.buffer)

    def get_stable_pair(self) -> tuple[float, float] | None:
        """Return averaged horizontal and vertical ratios when both are stable."""
        if not self.is_pair_stable():
            return None
        return (
            sum(self.horizontal_buffer) / len(self.horizontal_buffer),
            sum(self.vertical_buffer) / len(self.vertical_buffer),
        )

    def reset(self) -> None:
        """Clear scalar and paired filter state."""
        self.buffer.clear()
        self.smoothed_value = None
        self.horizontal_raw.clear()
        self.vertical_raw.clear()
        self.horizontal_buffer.clear()
        self.vertical_buffer.clear()
        self.smoothed_horizontal = None
        self.smoothed_vertical = None


class ScreenGazeSmoother:
    """Median-filter and smooth gaze after calibration maps it to pixels."""

    def __init__(self) -> None:
        self.raw_x: deque[float] = deque(
            maxlen=config.SCREEN_SMOOTHING_MEDIAN_WINDOW
        )
        self.raw_y: deque[float] = deque(
            maxlen=config.SCREEN_SMOOTHING_MEDIAN_WINDOW
        )
        self.history_x: deque[float] = deque(
            maxlen=config.SCREEN_STABILITY_FRAMES
        )
        self.history_y: deque[float] = deque(
            maxlen=config.SCREEN_STABILITY_FRAMES
        )
        self.smoothed_x: float | None = None
        self.smoothed_y: float | None = None

    def update_pair(self, x: float, y: float) -> tuple[float, float]:
        """Apply a short median filter followed by independent X/Y EMAs."""
        self.raw_x.append(float(x))
        self.raw_y.append(float(y))
        filtered_x = median(self.raw_x)
        filtered_y = median(self.raw_y)
        if self.smoothed_x is None:
            self.smoothed_x, self.smoothed_y = filtered_x, filtered_y
        else:
            self.smoothed_x = (
                config.SCREEN_X_SMOOTHING_ALPHA * filtered_x
                + (1.0 - config.SCREEN_X_SMOOTHING_ALPHA) * self.smoothed_x
            )
            self.smoothed_y = (
                config.SCREEN_Y_SMOOTHING_ALPHA * filtered_y
                + (1.0 - config.SCREEN_Y_SMOOTHING_ALPHA) * self.smoothed_y
            )
        self.history_x.append(self.smoothed_x)
        self.history_y.append(self.smoothed_y)
        return self.smoothed_x, self.smoothed_y

    def is_pair_stable(self) -> bool:
        """Return whether recent mapped coordinates occupy a compact region."""
        if (
            len(self.history_x) < config.SCREEN_STABILITY_FRAMES
            or len(self.history_y) < config.SCREEN_STABILITY_FRAMES
        ):
            return False
        movement = max(
            max(self.history_x) - min(self.history_x),
            max(self.history_y) - min(self.history_y),
        )
        return movement <= config.SCREEN_STABILITY_PIXELS

    def get_stable_pair(self) -> tuple[float, float] | None:
        if not self.is_pair_stable():
            return None
        return (
            sum(self.history_x) / len(self.history_x),
            sum(self.history_y) / len(self.history_y),
        )

    def reset(self) -> None:
        self.raw_x.clear()
        self.raw_y.clear()
        self.history_x.clear()
        self.history_y.clear()
        self.smoothed_x = None
        self.smoothed_y = None


class GazeIntentTracker:
    """Resolve a sticky intended target from recent smoothed gaze evidence."""

    def __init__(self) -> None:
        self.history: deque[tuple[float, float, float]] = deque(
            maxlen=config.INTENT_HISTORY_MAX_SAMPLES
        )
        self.current_target: str | None = None
        self.challenger_target: str | None = None
        self.challenger_since: float | None = None
        self.last_result: dict[str, object] = self._empty_result()

    def add_point(self, x: float, y: float, timestamp: float) -> None:
        """Add one valid smoothed point and expire old history."""
        values = (float(timestamp), float(x), float(y))
        if not all(math.isfinite(value) for value in values):
            return
        self.history.append(values)
        self._expire(timestamp)

    def prioritize_target(
        self,
        target: str,
        x: float,
        y: float,
        timestamp: float,
    ) -> None:
        """Discard incompatible history when one target contains the gaze."""
        if target != self.current_target:
            self.history.clear()
            self.current_target = target
            self._clear_challenger()
        self.add_point(x, y, timestamp)

    def resolve(
        self,
        targets: dict[str, dict[str, object]],
        timestamp: float,
        *,
        minimum_score: float | None = None,
    ) -> dict[str, object]:
        """Return the sticky intent target and ranked distance-based scores."""
        self._expire(timestamp)
        minimum = (
            config.INTENT_MIN_SCORE
            if minimum_score is None
            else float(minimum_score)
        )
        scores = self.score_targets(targets, timestamp)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))

        if self.current_target not in targets:
            self.current_target = None
            self._clear_challenger()
        if len(self.history) < config.INTENT_MIN_SAMPLES:
            return self._result(scores, ranked, "INSUFFICIENT")
        if not ranked:
            self.current_target = None
            self._clear_challenger()
            return self._result(scores, ranked, "NO_TARGET")

        winner, winner_score = ranked[0]
        runner_up_score = ranked[1][1] if len(ranked) > 1 else 0.0
        winner_margin = winner_score - runner_up_score
        retain_score = minimum * config.INTENT_RETAIN_SCORE_FACTOR

        if winner_score < minimum:
            if (
                self.current_target is None
                or scores.get(self.current_target, 0.0) < retain_score
            ):
                self.current_target = None
                self._clear_challenger()
                return self._result(scores, ranked, "NO_TARGET")
            self._clear_challenger()
            return self._result(scores, ranked, "LOCKED")

        if self.current_target is None:
            if winner_margin < config.INTENT_WIN_MARGIN:
                return self._result(scores, ranked, "AMBIGUOUS")
            self.current_target = winner
            self._clear_challenger()
            return self._result(scores, ranked, "LOCKED")

        if winner == self.current_target:
            self._clear_challenger()
            return self._result(scores, ranked, "LOCKED")

        current_score = scores.get(self.current_target, 0.0)
        clearly_stronger = (
            winner_margin >= config.INTENT_WIN_MARGIN
            and winner_score - current_score >= config.INTENT_WIN_MARGIN
        )
        if not clearly_stronger:
            self._clear_challenger()
            if current_score >= retain_score:
                return self._result(scores, ranked, "LOCKED")
            return self._result(scores, ranked, "AMBIGUOUS")

        if self.challenger_target != winner:
            self.challenger_target = winner
            self.challenger_since = timestamp
            return self._result(scores, ranked, "SWITCH_PENDING")
        if (
            self.challenger_since is not None
            and timestamp - self.challenger_since
            >= config.INTENT_SWITCH_HOLD_SECONDS
        ):
            self.current_target = winner
            self._clear_challenger()
            return self._result(scores, ranked, "LOCKED")
        return self._result(scores, ranked, "SWITCH_PENDING")

    def score_targets(
        self,
        targets: dict[str, dict[str, object]],
        timestamp: float,
    ) -> dict[str, float]:
        """Score all targets using size-normalized distance and recency."""
        self._expire(timestamp)
        if not self.history:
            return {}
        weights = []
        for sample_time, _x, _y in self.history:
            age_fraction = min(
                1.0,
                max(0.0, (timestamp - sample_time) / config.INTENT_HISTORY_SECONDS),
            )
            weights.append(
                1.0
                - age_fraction * (1.0 - config.INTENT_RECENCY_WEIGHT_MIN)
            )
        total_weight = sum(weights)
        if total_weight <= 0.0:
            return {}

        scores: dict[str, float] = {}
        for name, target in targets.items():
            try:
                center_x, center_y = target["center"]
                x1, y1, x2, y2 = target["bounds"]
                target_scale = float(target.get("intent_scale", 1.0))
                half_diagonal = math.hypot(x2 - x1, y2 - y1) / 2.0
                effective_radius = max(
                    1.0,
                    half_diagonal
                    * config.INTENT_EFFECTIVE_RADIUS_SCALE
                    * target_scale,
                )
            except (KeyError, TypeError, ValueError):
                continue
            weighted_score = 0.0
            for weight, (_sample_time, gaze_x, gaze_y) in zip(
                weights, self.history
            ):
                distance = math.hypot(gaze_x - center_x, gaze_y - center_y)
                sample_score = max(0.0, 1.0 - distance / effective_radius)
                weighted_score += weight * sample_score
            scores[name] = weighted_score / total_weight
        return scores

    def clear(self) -> None:
        """Clear history and all sticky intent state after sustained loss."""
        self.history.clear()
        self.current_target = None
        self._clear_challenger()
        self.last_result = self._empty_result()

    def _expire(self, timestamp: float) -> None:
        cutoff = float(timestamp) - config.INTENT_HISTORY_SECONDS
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()

    def _result(
        self,
        scores: dict[str, float],
        ranked: list[tuple[str, float]],
        state: str,
    ) -> dict[str, object]:
        target = self.current_target
        intent_score = scores.get(target, 0.0) if target is not None else 0.0
        alternatives = [item for item in ranked if item[0] != target]
        second_target, second_score = (
            alternatives[0] if alternatives else (None, 0.0)
        )
        result = {
            "target": target,
            "score": intent_score,
            "second_target": second_target,
            "second_score": second_score,
            "margin": intent_score - second_score,
            "history_samples": len(self.history),
            "state": state,
            "top_scores": ranked[:3],
            "raw_winner": ranked[0][0] if ranked else None,
        }
        self.last_result = result
        return result

    def _clear_challenger(self) -> None:
        self.challenger_target = None
        self.challenger_since = None

    @staticmethod
    def _empty_result() -> dict[str, object]:
        return {
            "target": None,
            "score": 0.0,
            "second_target": None,
            "second_score": 0.0,
            "margin": 0.0,
            "history_samples": 0,
            "state": "EMPTY",
            "top_scores": [],
            "raw_winner": None,
        }


def run_intent_synthetic_tests() -> None:
    """Validate target intent behavior without camera or Tkinter."""
    targets = {
        "G": {"center": (20.0, 50.0), "bounds": (0.0, 10.0, 40.0, 90.0)},
        "H": {"center": (70.0, 50.0), "bounds": (50.0, 10.0, 90.0, 90.0)},
        "J": {"center": (120.0, 50.0), "bounds": (100.0, 10.0, 140.0, 90.0)},
    }

    tracker = GazeIntentTracker()
    for index, point in enumerate(((68, 48), (72, 52), (69, 51), (71, 49))):
        tracker.add_point(*point, 0.03 * index)
    result = tracker.resolve(targets, 0.10)
    assert result["target"] == "H"

    # Two brief samples toward G must not dislodge the established H intent.
    tracker.add_point(48, 50, 0.13)
    tracker.add_point(42, 50, 0.16)
    result = tracker.resolve(targets, 0.16)
    assert result["target"] == "H"

    # Consistent new evidence starts a challenger, then switches after hold.
    for index in range(6):
        timestamp = 0.50 + index * 0.04
        tracker.add_point(119 + index % 2, 50, timestamp)
        result = tracker.resolve(targets, timestamp)
    assert result["target"] == "H" and result["state"] == "SWITCH_PENDING"
    tracker.add_point(121, 50, 0.90)
    result = tracker.resolve(targets, 0.90)
    assert result["target"] == "J"

    # Equal boundary evidence must not rapidly alternate an established target.
    tracker.clear()
    for index in range(4):
        tracker.add_point(70, 50, 1.00 + index * 0.03)
    assert tracker.resolve(targets, 1.10)["target"] == "H"
    for index, x in enumerate((45, 45, 45, 45)):
        timestamp = 1.13 + index * 0.03
        tracker.add_point(x, 50, timestamp)
        result = tracker.resolve(targets, timestamp)
    assert result["target"] == "H"

    # A short blink adds no samples and leaves history and intent untouched.
    sample_count = len(tracker.history)
    blink_result = tracker.resolve(targets, timestamp + 0.10)
    assert len(tracker.history) == sample_count
    assert blink_result["target"] == "H"
    tracker.clear()
    assert not tracker.history and tracker.current_target is None

    # The same pixel error is more forgiving for a physically larger target.
    size_tracker = GazeIntentTracker()
    for index in range(4):
        size_tracker.add_point(170, 100, 2.00 + index * 0.02)
    size_targets = {
        "LARGE": {"center": (100, 100), "bounds": (-150, 50, 350, 150)},
        "SMALL": {"center": (100, 100), "bounds": (75, 75, 125, 125)},
    }
    size_scores = size_tracker.score_targets(size_targets, 2.08)
    assert size_scores["LARGE"] > size_scores["SMALL"]

    # Clear physical containment must override stale evidence from a neighbour.
    direct_tracker = GazeIntentTracker()
    for index in range(4):
        direct_tracker.add_point(20, 50, 3.00 + index * 0.03)
    assert direct_tracker.resolve(targets, 3.10)["target"] == "G"
    direct_tracker.prioritize_target("H", 70, 50, 3.13)
    direct_result = direct_tracker.resolve(targets, 3.13)
    assert direct_result["target"] == "H"
    assert len(direct_tracker.history) == 1
    print("Gaze intent synthetic tests passed.")


if __name__ == "__main__" and "--intent-test" in sys.argv:
    run_intent_synthetic_tests()
