"""Nine-point gaze calibration and verification window."""

from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import messagebox
from collections.abc import Callable, Sequence
from statistics import median
from typing import Any

try:
    from .. import config
    from ..core.calibration import Calibrator
    from ..core.eye_tracker import EyeTracker
    from ..utils.language_manager import get_default_language_manager
    from ..utils.runtime import get_logger
except ImportError:
    import config
    from core.calibration import Calibrator
    from core.eye_tracker import EyeTracker
    from utils.language_manager import get_default_language_manager
    from utils.runtime import get_logger


class CalibrationScreen(tk.Tk):
    """Collect nine-point features, verify predictions, and support retries."""

    POINTS = Calibrator.TARGETS
    DOT_RADIUS = 24
    RING_START_RADIUS = 72
    PATIENT_FONT = "Nirmala UI"
    VERIFICATION_SETTLE = "VERIFICATION_SETTLE"
    VERIFICATION_COLLECT = "VERIFICATION_COLLECT"
    VERIFICATION_ADVANCE = "VERIFICATION_ADVANCE"
    VERIFICATION_COMPLETE = "VERIFICATION_COMPLETE"

    @staticmethod
    def _updates_should_continue(finished: bool, mode: str) -> bool:
        """Keep Tk updates alive for every active collection/check state."""
        return not finished and mode != "results"

    def __init__(
        self,
        on_sample: Callable[[str, Sequence[float]], None],
        on_complete: Callable[[], Calibrator | None],
        on_verified: Callable[[bool], None] | None = None,
        on_retry_points: Callable[..., Calibrator] | None = None,
        language_manager=None,
        calibration_mode: str = config.CALIBRATION_STATIC_MODE,
        on_pursuit_frame: Callable[[float, float, float, Any], None] | None = None,
        on_switch_method: Callable[[str], Calibrator] | None = None,
        skip_verification: bool = False,
        on_stage_change: Callable[[str], None] | None = None,
        on_result_ready: Callable[[dict[str, object]], None] | None = None,
        on_patient_action: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.on_sample = on_sample
        self.on_complete = on_complete
        self.on_verified = on_verified
        self.on_retry_points = on_retry_points
        self.language_manager = language_manager or get_default_language_manager()
        self.calibration_method = calibration_mode
        self.fast_calibration = (
            calibration_mode == config.CALIBRATION_FAST_MODE
        )
        self.on_pursuit_frame = on_pursuit_frame
        self.on_switch_method = on_switch_method
        self.skip_verification = bool(skip_verification)
        self.on_stage_change = on_stage_change
        self.on_result_ready = on_result_ready
        self.on_patient_action = on_patient_action
        self.eye_tracker = EyeTracker()
        self.calibrator: Calibrator | None = None
        self.mode = (
            "pursuit"
            if calibration_mode == config.CALIBRATION_PURSUIT_MODE
            else "collection"
        )
        self.active_points = (
            [] if self.mode == "pursuit" else [point[0] for point in self.POINTS]
        )
        self.pursuit_path = Calibrator.build_pursuit_path()
        self.calibration_run_started_at = time.monotonic()
        self.fresh_collection_performed = True
        self.current_index = 0
        self.target_started_at = 0.0
        self.target_x = self.target_y = 0
        self.ring_id: int | None = None
        self.after_id: str | None = None
        self.finished = False
        self.camera_retry_prompt_active = False
        self.valid_samples: list[Any] = []
        self.verification_expected_frames = 0
        self.verification_state: str | None = None
        self.verification_collect_started_at = 0.0
        self.verification_advance_at = 0.0
        self.verification_pending_status = "OK"
        self.verification_last_logged_samples = 0
        self.verification_results: list[dict[str, float | str]] = []
        self.previous_result: dict[str, float | str] | None = None
        self.status_message = ""
        self.result_frame: tk.Frame | None = None
        self.best_session_candidate: dict[str, object] | None = None
        self.retry_origin_candidate: dict[str, object] | None = None
        self.retry_origin_metrics: dict[str, object] | None = None
        self.retry_points: list[str] = []
        self.retry_was_all = False
        self.automatic_poor_retry_count = 0
        self.full_calibration_attempted = True
        self.last_quality_state = Calibrator.QUALITY_UNUSABLE
        self.last_improved_vs_previous = False
        self.regional_improvement_attempts = {
            region: 0 for region in Calibrator.REGIONAL_CALIBRATION_POINTS
        }
        self.retry_region: str | None = None
        self.fast_point_retry_counts = {
            point: 0 for point in Calibrator.REQUIRED_POINTS
        }
        self.fast_transition_until: float | None = None

        self.title(f"GazeLink - {self._t('calibration')}")
        self.configure(bg="black")
        self.attributes("-fullscreen", True)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.screen_width = self.winfo_screenwidth()
        self.screen_height = self.winfo_screenheight()
        self.frame_delay_ms = max(1, int(1000 / config.FPS_TARGET))

        self.eye_tracker.start()
        self._show_target()
        self.after_id = self.after(self.frame_delay_ms, self._update)

    def start_profile_validation(self, calibrator: Calibrator) -> None:
        """Replace full collection with a quick centre check of a saved profile."""
        self.calibrator = calibrator
        self.fresh_collection_performed = False
        self.mode = "profile_validation"
        self.active_points = ["center"]
        self.current_index = 0
        self.verification_results.clear()
        self.previous_result = None
        self.status_message = "Checking saved calibration at the centre"
        self._show_target()

    def _show_target(self) -> None:
        self.canvas.delete("all")
        self.fast_transition_until = None
        if self.mode == "pursuit":
            self._show_pursuit_target()
            return
        name = self.active_points[self.current_index]
        if self.fast_calibration and self.mode == "collection":
            print(
                f"[FAST CAL] point {self.current_index + 1}/"
                f"{len(self.active_points)}"
            )
            print(
                f"[FAST CAL] duration="
                f"{config.FAST_CALIBRATION_POINT_SECONDS:.2f}s"
            )
        coordinates = (
            Calibrator.VERIFICATION_COORDINATES
            if self.mode == "verification"
            else Calibrator.TARGET_COORDINATES
        )
        x_fraction, y_fraction = coordinates[name]
        self.target_x = int(x_fraction * (self.screen_width - 1))
        self.target_y = int(y_fraction * (self.screen_height - 1))
        self.valid_samples = []
        self.verification_expected_frames = 0
        self.target_started_at = time.monotonic()

        heading = (
            self.language_manager.format_verification_status(
                self.current_index + 1, len(self.active_points)
            )
            if self.mode in {"verification", "profile_validation"}
            else self.language_manager.get_instruction("calibration_progress")
        )
        self.canvas.create_text(
            self.screen_width // 2, 42, text=heading,
            fill="white", font=(self.PATIENT_FONT, 23, "bold"), tags="instruction"
        )
        if self.status_message:
            self.canvas.create_text(
                self.screen_width // 2, 112, text=self.status_message,
                fill="#FFD166", font=(self.PATIENT_FONT, 15, "bold"), tags="instruction"
            )
        self._draw_dot(self.target_x, self.target_y, config.ACCENT, "target_dot")
        radius = self.RING_START_RADIUS
        self.ring_id = self.canvas.create_oval(
            self.target_x - radius, self.target_y - radius,
            self.target_x + radius, self.target_y + radius,
            outline=config.ACCENT, width=5, tags="target"
        )
        if config.CALIBRATION_DEBUG:
            for result in self.verification_results:
                self._draw_verification_result(result)
        if self.mode in {"verification", "profile_validation"}:
            self.verification_state = self.VERIFICATION_SETTLE
            self.verification_collect_started_at = 0.0
            self.verification_last_logged_samples = 0
            self._log_verification("settle started")

    def _show_pursuit_target(self) -> None:
        """Show the single experimental moving target."""
        self.target_started_at = time.monotonic()
        self.valid_samples = []
        self.verification_expected_frames = 0
        self.target_x = int(self.pursuit_path[0][0] * (self.screen_width - 1))
        self.target_y = int(self.pursuit_path[0][1] * (self.screen_height - 1))
        self.canvas.create_text(
            self.screen_width // 2, 42,
            text="Follow the moving dot naturally with your eyes.",
            fill="white", font=(self.PATIENT_FONT, 23, "bold"),
            tags="instruction",
        )
        self.canvas.create_text(
            self.screen_width // 2, 80,
            text="Try to keep your head comfortable and steady.",
            fill="#C8D1E0", font=(self.PATIENT_FONT, 17),
            justify="center", tags="instruction",
        )
        self._draw_dot(self.target_x, self.target_y, config.ACCENT, "pursuit_dot")
        radius = self.RING_START_RADIUS
        self.ring_id = self.canvas.create_oval(
            self.target_x - radius, self.target_y - radius,
            self.target_x + radius, self.target_y + radius,
            outline="#FFFFFF", width=4, tags="pursuit_ring",
        )

    def _update(self) -> None:
        if self.finished or self.mode == "results":
            return
        self.after_id = None
        _, ratios = self.eye_tracker.get_gaze_frame()
        debug = self.eye_tracker.get_debug_info()
        if (
            int(debug.get("read_failure_count", 0))
            >= config.CAMERA_READ_FAILURE_LIMIT
            and not self.camera_retry_prompt_active
        ):
            self.camera_retry_prompt_active = True
            get_logger().error(
                "camera frame reads failed during calibration: %s",
                debug.get("camera_error") or self.eye_tracker.last_error,
            )
            self.canvas.delete("camera_status")
            self.canvas.create_text(
                self.screen_width // 2,
                145,
                text=self.language_manager.get_text("camera_unavailable"),
                fill="#FFD166",
                font=(self.PATIENT_FONT, 17, "bold"),
                tags="camera_status",
            )
            self.after_idle(self._prompt_camera_retry)
        now = time.monotonic()
        if self.fast_calibration and self.fast_transition_until is not None:
            if now >= self.fast_transition_until:
                self._show_target()
            if self._updates_should_continue(self.finished, self.mode):
                self.after_id = self.after(self.frame_delay_ms, self._update)
            return
        features = debug.get("feature_vector")
        elapsed = now - self.target_started_at

        if self.mode == "pursuit":
            self._update_pursuit(elapsed, features, bool(debug.get("valid")))
            if elapsed >= config.PURSUIT_DURATION_SECONDS:
                self._finish_pursuit_collection()
            # _finish_pursuit_collection may switch mode to verification.
            # Always keep the shared Tk callback alive after that transition.
            if self._updates_should_continue(self.finished, self.mode):
                self.after_id = self.after(self.frame_delay_ms, self._update)
            return
        if self.mode in {"verification", "profile_validation"}:
            self._update_verification(
                time.monotonic(), features, bool(debug.get("valid")), debug
            )
            if self._updates_should_continue(self.finished, self.mode):
                self.after_id = self.after(self.frame_delay_ms, self._update)
            return
        if self.mode == "collection":
            self._collect_sample(elapsed, features, bool(debug.get("valid")))
            duration = self._point_duration_seconds()
            settle = self._point_collection_delay_seconds()
            target_count = self._point_target_samples()

        if self.fast_calibration:
            # The experimental ring advances exactly once across the complete
            # point duration. It only restarts for the one explicit retry.
            progress = min(max(0.0, elapsed) / max(0.01, duration), 1.0)
        else:
            active_elapsed = max(0.0, elapsed - settle)
            active_duration = max(0.01, duration - settle)
            progress = min(active_elapsed / active_duration, 1.0)
        self._update_ring(progress)
        self._draw_sample_status(target_count, debug)

        if elapsed >= duration:
            self._finish_current_target()
        if self._updates_should_continue(self.finished, self.mode):
            self.after_id = self.after(self.frame_delay_ms, self._update)

    def _prompt_camera_retry(self) -> None:
        """Allow recovery from a disconnected or busy webcam without crashing."""
        if self.finished:
            return
        try:
            retry = messagebox.askretrycancel(
                "GazeLink Camera",
                self.language_manager.get_text("camera_unavailable"),
                parent=self,
            )
        except tk.TclError:
            retry = False
        if retry and self.eye_tracker.start():
            self.camera_retry_prompt_active = False
            self.canvas.delete("camera_status")
            get_logger().info("camera reopened during calibration")
            return
        if retry:
            self.camera_retry_prompt_active = False
            get_logger().error(
                "camera retry failed during calibration: %s",
                self.eye_tracker.last_error,
            )
            return
        self.close()

    def _update_verification(
        self,
        now: float,
        features: Any,
        valid: bool,
        debug: dict[str, Any],
    ) -> None:
        """Advance one bounded verification point through explicit states."""
        if self.verification_state == self.VERIFICATION_SETTLE:
            settle_elapsed = now - self.target_started_at
            self._update_ring(
                min(1.0, settle_elapsed / max(0.01, config.VERIFICATION_SETTLE_SECONDS))
            )
            self._draw_verification_status(debug)
            if settle_elapsed >= config.VERIFICATION_SETTLE_SECONDS:
                self.verification_state = self.VERIFICATION_COLLECT
                self.verification_collect_started_at = now
                self._log_verification("collecting")
            return

        if self.verification_state == self.VERIFICATION_COLLECT:
            self.verification_expected_frames += 1
            self._collect_verification_prediction(features, valid)
            if (
                config.CALIBRATION_DEBUG
                and len(self.valid_samples) >= self.verification_last_logged_samples + 4
            ):
                self.verification_last_logged_samples = len(self.valid_samples)
                self._log_verification(f"{len(self.valid_samples)} valid samples")
            collection_elapsed = now - self.verification_collect_started_at
            outcome = Calibrator.verification_outcome(
                len(self.valid_samples), collection_elapsed
            )
            self._update_ring(
                min(
                    1.0,
                    len(self.valid_samples)
                    / max(1, config.VERIFICATION_TARGET_VALID_SAMPLES),
                )
            )
            self._draw_verification_status(debug)
            if outcome is not None:
                status, reason = outcome
                self._complete_verification_point(status, reason, now)
            return

        if self.verification_state == self.VERIFICATION_ADVANCE:
            if now >= self.verification_advance_at:
                self._advance_verification_point()

    def _collect_verification_prediction(self, features: Any, valid: bool) -> None:
        # Blink/invalid frames consume time but never erase valid predictions.
        if not valid or features is None or self.calibrator is None:
            return
        try:
            predicted = self.calibrator.predict_with_debug(
                features, self.screen_width, self.screen_height,
                apply_correction=False,
            )
        except (RuntimeError, ValueError):
            return
        self.valid_samples.append(
            (float(predicted["predicted_x"]), float(predicted["predicted_y"]))
        )

    def _complete_verification_point(
        self, status: str, reason: str, now: float
    ) -> None:
        """Store an OK/degraded result and always enter ADVANCE."""
        name = self.active_points[self.current_index]
        if self.valid_samples:
            predicted_x = median(item[0] for item in self.valid_samples)
            predicted_y = median(item[1] for item in self.valid_samples)
            spread = median(
                math.hypot(item[0] - predicted_x, item[1] - predicted_y)
                for item in self.valid_samples
            )
        else:
            # A missing point must be represented as poor, never as perfect.
            predicted_x = 0.0 if self.target_x > self.screen_width / 2 else float(self.screen_width - 1)
            predicted_y = 0.0 if self.target_y > self.screen_height / 2 else float(self.screen_height - 1)
            spread = float(math.hypot(self.screen_width, self.screen_height))
        result: dict[str, float | str] = {
            "name": name,
            "actual_x": float(self.target_x),
            "actual_y": float(self.target_y),
            "predicted_x": predicted_x,
            "predicted_y": predicted_y,
            "spread": spread,
            "valid_frames": len(self.valid_samples),
            "expected_frames": self.verification_expected_frames,
            "verification_status": status,
        }
        self.previous_result = result
        self.verification_results.append(result)
        self.verification_pending_status = status
        self.verification_state = self.VERIFICATION_ADVANCE
        self.verification_advance_at = now + config.VERIFICATION_ADVANCE_MESSAGE_SECONDS
        if reason == "sample target":
            self._log_verification("completed by sample target")
        else:
            self._log_verification(
                f"timeout with {len(self.valid_samples)} samples -> {status} -> advancing"
            )
        self.canvas.delete("sample_status")
        self.canvas.create_text(
            self.target_x, self.target_y + 108,
            text=("Using available samples" if reason == "timeout" else "Check complete"),
            fill="#FFD166" if status != "OK" else "#C8D1E0",
            font=(self.PATIENT_FONT, 15, "bold"), tags="sample_status",
        )

    def _advance_verification_point(self) -> None:
        self.current_index += 1
        self.status_message = ""
        if self.current_index < len(self.active_points):
            self._show_target()
            return
        self.verification_state = self.VERIFICATION_COMPLETE
        self._log_verification("all checks complete")
        if self.mode == "profile_validation":
            self._finish_profile_validation()
        else:
            self._finish_verification()

    def _draw_verification_status(self, debug: dict[str, Any]) -> None:
        self.canvas.delete("sample_status")
        text = (
            f"Valid samples: {len(self.valid_samples)} / "
            f"{config.VERIFICATION_TARGET_VALID_SAMPLES}"
            if config.CALIBRATION_DEBUG
            else "Collecting..."
        )
        self.canvas.create_text(
            self.target_x, self.target_y + 108,
            text=text, fill="#C8D1E0", font=(self.PATIENT_FONT, 15),
            justify="center", tags="sample_status",
        )

    def _log_verification(self, message: str) -> None:
        if config.CALIBRATION_DEBUG:
            display_index = min(self.current_index + 1, len(self.active_points))
            print(f"Verification {display_index}: {message}")

    def _update_pursuit(self, elapsed: float, features: Any, valid: bool) -> None:
        progress = min(1.0, elapsed / max(0.01, config.PURSUIT_DURATION_SECONDS))
        x_fraction, y_fraction = Calibrator.pursuit_position(
            self.pursuit_path, progress
        )
        self.target_x = int(x_fraction * (self.screen_width - 1))
        self.target_y = int(y_fraction * (self.screen_height - 1))
        radius = self.DOT_RADIUS
        self.canvas.coords(
            "pursuit_dot",
            self.target_x - radius,
            self.target_y - radius,
            self.target_x + radius,
            self.target_y + radius,
        )
        # The outer ring follows the same point but remains visually separate.
        if self.ring_id is not None:
            outer = self.RING_START_RADIUS
            self.canvas.coords(
                self.ring_id,
                self.target_x - outer, self.target_y - outer,
                self.target_x + outer, self.target_y + outer,
            )
        timestamp = time.monotonic()
        if self.on_pursuit_frame is not None:
            self.on_pursuit_frame(
                timestamp, x_fraction, y_fraction,
                features if valid else None,
            )
        if valid and features is not None:
            self.valid_samples.append(tuple(features))
        self.canvas.delete("sample_status")
        status = (
            f"{elapsed:0.1f} / {config.PURSUIT_DURATION_SECONDS:0.0f} seconds"
            + (
                f"   Valid samples: {len(self.valid_samples)}"
                if config.CALIBRATION_DEBUG else ""
            )
        )
        self.canvas.create_text(
            self.screen_width // 2, self.screen_height - 42,
            text=status, fill="#C8D1E0",
            font=(self.PATIENT_FONT, 15), tags="sample_status",
        )

    def _finish_pursuit_collection(self) -> None:
        self.calibrator = self.on_complete()
        if self.calibrator is None:
            self.mode = "results"
            self._show_model_failure()
            return
        self._start_verification()

    def _collect_sample(self, elapsed: float, features: Any, valid: bool) -> None:
        if (
            elapsed < self._point_collection_delay_seconds()
            or not valid
            or features is None
        ):
            return
        name = self.active_points[self.current_index]
        self.on_sample(name, features)
        self.valid_samples.append(tuple(features))

    def _finish_current_target(self) -> None:
        # Verification has its own hard-bounded state machine. This method is
        # now exclusively the preserved static-point collection path.
        minimum = self._point_minimum_samples()
        name = self.active_points[self.current_index]
        if self.fast_calibration:
            print(f"[FAST CAL] valid_samples={len(self.valid_samples)}")
        if len(self.valid_samples) < minimum:
            if self.fast_calibration:
                retry_count = self.fast_point_retry_counts.get(name, 0)
                if retry_count < 1:
                    self.fast_point_retry_counts[name] = retry_count + 1
                    print("[FAST CAL] retry")
                    self.status_message = (
                        f"Retrying this point once: {len(self.valid_samples)}/"
                        f"{minimum} valid samples."
                    )
                    if self.on_retry_points is not None:
                        self.calibrator = self.on_retry_points([name])
                    self._show_target()
                    return
                print("[FAST CAL] failed after one retry")
                self.status_message = (
                    f"Only {len(self.valid_samples)}/{minimum} valid samples "
                    "after one retry."
                )
                self.calibrator = self.on_complete()
                self.mode = "results"
                self._show_model_failure()
                return
            self.status_message = (
                f"Only {len(self.valid_samples)} valid samples. "
                "Blink normally; collection will continue when your eyes reopen."
            )
            settle = config.CALIBRATION_TRANSITION_SECONDS
            # Keep every valid sample already collected for this unchanged
            # target. Move the timer back to the sampling boundary so the
            # point simply remains active rather than restarting.
            self.target_started_at = time.monotonic() - settle
            self._update_ring(0.0)
            return

        if self.fast_calibration:
            print("[FAST CAL] accepted")
        self.current_index += 1
        self.status_message = ""
        if self.current_index < len(self.active_points):
            if self.fast_calibration:
                self._begin_fast_point_transition()
            else:
                self._show_target()
            return
        self._finish_collection()

    def _begin_fast_point_transition(self) -> None:
        """Hold the completed ring briefly before drawing the next point."""
        self.fast_transition_until = (
            time.monotonic() + config.FAST_CALIBRATION_TRANSITION_SECONDS
        )

    def _point_duration_seconds(self) -> float:
        return (
            config.FAST_CALIBRATION_POINT_SECONDS
            if self.fast_calibration
            else config.CALIBRATION_POINT_SECONDS
        )

    def _point_collection_delay_seconds(self) -> float:
        if self.fast_calibration:
            return config.FAST_CALIBRATION_SETTLE_SECONDS
        return config.CALIBRATION_TRANSITION_SECONDS

    def _point_target_samples(self) -> int:
        return (
            config.FAST_CALIBRATION_TARGET_SAMPLES
            if self.fast_calibration
            else config.CALIBRATION_TARGET_SAMPLES
        )

    def _point_minimum_samples(self) -> int:
        return (
            config.FAST_CALIBRATION_MIN_VALID_SAMPLES
            if self.fast_calibration
            else config.CALIBRATION_MIN_VALID_SAMPLES
        )

    def _finish_collection(self) -> None:
        self.calibrator = self.on_complete()
        if self.calibrator is None:
            # A fitting failure requires an explicit caregiver/user retry. It
            # must never start another nine-point collection automatically.
            self.mode = "results"
            self._show_model_failure()
            return
        self._start_verification()

    def _start_verification(self) -> None:
        if (
            self.skip_verification
            and self.calibration_method == config.CALIBRATION_PURSUIT_MODE
        ):
            self._show_verification_skipped()
            return
        # Calibration and verification are one uninterrupted patient task. The
        # combined explanation was already given before START.
        self._remove_result_controls()
        if self.on_stage_change is not None:
            self.on_stage_change("verification_checks")
        self.mode = "verification"
        self.active_points = [point[0] for point in Calibrator.VERIFICATION_TARGETS]
        self.current_index = 0
        self.verification_results = []
        self.previous_result = None
        self.status_message = ""
        self._show_target()

    def _show_verification_skipped(self) -> None:
        """Show an explicit developer-only unverified pursuit result."""
        if self.calibrator is None:
            self._show_model_failure()
            return
        self.mode = "results"
        self.verification_state = self.VERIFICATION_COMPLETE
        self.calibrator.verification_metrics = {"available": False}
        self.calibrator.verification_results = []
        self.calibrator.readiness_metrics = Calibrator._invalid_readiness(
            "Verification skipped by developer option"
        )
        self.canvas.delete("all")
        self._remove_result_controls()
        holdout = self.calibrator.pursuit_holdout_metrics
        self.canvas.create_text(
            self.screen_width // 2, int(self.screen_height * 0.20),
            text="DEVELOPER MODE\nVERIFICATION SKIPPED",
            fill="#FFD166", font=(self.PATIENT_FONT, 31, "bold"),
            justify="center",
        )
        self.canvas.create_text(
            self.screen_width // 2, int(self.screen_height * 0.45),
            text=(
                "Readiness unavailable\n\n"
                f"Pursuit hold-out median: {float(holdout.get('median_error', 0)):.0f}px\n"
                f"Pursuit hold-out mean: {float(holdout.get('mean_error', 0)):.0f}px\n"
                f"Selected lag: {self.calibrator.selected_pursuit_lag_ms} ms"
            ),
            fill="white", font=("Consolas", 18, "bold"), justify="center",
        )
        self.result_frame = tk.Frame(self, bg="black")
        self.result_frame.place(relx=0.5, rely=0.75, anchor="center")
        tk.Button(
            self.result_frame,
            text="CONTINUE (UNVERIFIED)",
            command=self._accept_unverified,
            bg="#4A90D9", fg="white",
            font=(self.PATIENT_FONT, 21, "bold"), padx=48, pady=20,
        ).pack()

    def _accept_unverified(self) -> None:
        """Accept only when the explicit developer bypass was supplied."""
        self._notify_patient_action("continue")
        if not self.skip_verification or self.calibrator is None:
            return
        if not self.calibrator.save_profile():
            print("Calibration profile could not be saved.")
            return
        if self.on_verified is not None:
            self.on_verified(True)
        self.close()

    def _finish_profile_validation(self) -> None:
        result = self.verification_results[0]
        error = math.hypot(
            float(result["actual_x"]) - float(result["predicted_x"]),
            float(result["actual_y"]) - float(result["predicted_y"]),
        )
        if (
            error <= config.PROFILE_CENTER_VALIDATION_LIMIT
            and self.calibrator is not None
            and (
                self.calibrator.quality_passes()
                or config.CALIBRATION_DEVELOPER_OVERRIDE
            )
        ):
            self.mode = "results"
            self.best_session_candidate = self.calibrator.create_snapshot()
            metrics = dict(self.calibrator.quality_metrics)
            if self.on_stage_change is not None:
                self.on_stage_change("complete")
            self._show_readiness_result(
                notice=(
                    f"Saved profile centre error: {error:.0f}px"
                    if config.CALIBRATION_DEBUG else ""
                )
            )
            return
        self.status_message = (
            f"Saved calibration missed centre by {error:.0f}px. Recalibrating."
        )
        if self.calibrator is not None:
            self.calibrator.reset()
        if self.on_retry_points is not None:
            self.on_retry_points(Calibrator.REQUIRED_POINTS)
        self.mode = "collection"
        self.active_points = list(Calibrator.REQUIRED_POINTS)
        self.current_index = 0
        self._show_target()

    def _finish_verification(self) -> None:
        if self.calibrator is None:
            return
        self.calibrator.set_verification_results(self.verification_results)
        if self.on_stage_change is not None:
            self.on_stage_change("complete")
        if self.fresh_collection_performed:
            self.calibrator.duration_seconds = (
                time.monotonic() - self.calibration_run_started_at
            )
        self.mode = "results"
        candidate_snapshot = self.calibrator.create_snapshot()
        candidate_readiness = dict(self.calibrator.readiness_metrics)
        previous_readiness = self._snapshot_readiness(self.retry_origin_candidate)
        previous_score = int(previous_readiness.get("score", 0))
        retry_region = self.retry_region

        if self.best_session_candidate is None:
            self.best_session_candidate = candidate_snapshot
            candidate_kept = True
        else:
            best_readiness = self._snapshot_readiness(self.best_session_candidate)
            candidate_kept = Calibrator.is_better_readiness_candidate(
                candidate_readiness, best_readiness, retry_region
            )
            if candidate_kept:
                self.best_session_candidate = candidate_snapshot

        if not candidate_kept and self.best_session_candidate is not None:
            # Restore every model and readiness field from the protected best.
            self.calibrator.restore_snapshot(self.best_session_candidate)
        active_score = int(self.calibrator.readiness_metrics.get("score", 0))
        self.last_improved_vs_previous = bool(
            previous_readiness and candidate_kept and active_score > previous_score
        )
        self._clear_retry_state()
        if previous_readiness:
            notice = (
                f"Calibration improved: {previous_score} → {active_score}"
                if candidate_kept and active_score > previous_score
                else (
                    "The previous calibration was better.\n\n"
                    f"Your score remains: {active_score} / 100"
                )
            )
        else:
            notice = ""
        self._show_readiness_result(
            notice=notice,
            previous_score=previous_score if previous_readiness else None,
            improved=bool(candidate_kept and active_score > previous_score),
            previous_kept=bool(previous_readiness and not candidate_kept),
        )

    def _show_readiness_result(
        self,
        notice: str = "",
        *,
        previous_score: int | None = None,
        improved: bool = False,
        previous_kept: bool = False,
    ) -> None:
        """Show the mandatory readiness score before communication starts."""
        if self.calibrator is None:
            self._show_model_failure()
            return
        readiness = dict(self.calibrator.readiness_metrics)
        score = int(readiness.get("score", 0))
        level = str(readiness.get("level", Calibrator.READINESS_POOR))
        weak_region = self.calibrator.get_weakest_region()
        weak_label = (
            str(readiness.get("region_labels", {}).get(weak_region, ""))
            if weak_region else ""
        )
        can_improve = bool(
            self.calibration_method in {
                config.CALIBRATION_STATIC_MODE,
                config.CALIBRATION_FAST_MODE,
            }
            and
            level in {Calibrator.READINESS_GOOD, Calibrator.READINESS_EMERGENCY}
            and Calibrator.regional_improvement_available(
                weak_region, self.regional_improvement_attempts
            )
        )

        self.canvas.delete("all")
        self._remove_result_controls()
        self.canvas.create_text(
            self.screen_width // 2,
            int(self.screen_height * 0.045),
            text=self.language_manager.get_instruction("calibration_complete"),
            fill="#D9E2F1",
            font=(self.PATIENT_FONT, 16, "bold"),
            justify="center",
            width=int(self.screen_width * 0.84),
        )
        self.canvas.create_text(
            self.screen_width // 2,
            int(self.screen_height * 0.12),
            text=self._t("gaze_readiness"),
            fill="#8EC5FF",
            font=(self.PATIENT_FONT, 28, "bold"),
        )
        self.canvas.create_text(
            self.screen_width // 2,
            int(self.screen_height * 0.25),
            text=f"{score} / 100",
            fill="#55D187" if score >= config.READINESS_GOOD_MIN else "#FFD166",
            font=(self.PATIENT_FONT, 58, "bold"),
        )
        title = (
            self._t("calibration_low")
            if level == Calibrator.READINESS_POOR
            else self._t(
                {
                    Calibrator.READINESS_EXCELLENT: "excellent",
                    Calibrator.READINESS_GOOD: "good",
                    Calibrator.READINESS_EMERGENCY: "emergency_ready",
                }.get(level, "good")
            )
        )
        capabilities = []
        if readiness.get("emergency_ready"):
            capabilities.append(self._t("emergency_capability"))
        if readiness.get("typing_ready"):
            capabilities.append(self._t("typing_capability"))
        elif score >= config.READINESS_EMERGENCY_MIN:
            capabilities.append(self._t("typing_warning"))
        if level == Calibrator.READINESS_GOOD:
            capabilities.append(self._t("letters_slow"))
        if weak_region and weak_label == "WEAK" and level != Calibrator.READINESS_POOR:
            capabilities.append(self._region_patient_message(weak_region))
        if level == Calibrator.READINESS_POOR:
            capabilities = [self._t("adjust_position")]
        body = "\n".join(capabilities)
        if notice:
            body = f"{notice}\n\n{body}"
        self.canvas.create_text(
            self.screen_width // 2,
            int(self.screen_height * 0.39),
            text=title,
            fill="white",
            font=(self.PATIENT_FONT, 30, "bold"),
        )
        self.canvas.create_text(
            self.screen_width // 2,
            int(self.screen_height * 0.54),
            text=body,
            fill="#D9E2F1",
            font=(self.PATIENT_FONT, 20, "bold"),
            justify="center",
            width=int(self.screen_width * 0.78),
        )

        if config.CALIBRATION_DEBUG:
            self._draw_readiness_debug(readiness)

        self.result_frame = tk.Frame(self, bg="black")
        self.result_frame.place(relx=0.5, rely=0.79, anchor="center")
        actions: list[tuple[str, Callable[[], None], bool]] = []
        if score >= config.READINESS_EMERGENCY_MIN:
            actions.append((self._t("continue"), self._accept, True))
            if can_improve:
                actions.append((self._t("improve_accuracy"), self._improve_accuracy, False))
            if self.calibration_method == config.CALIBRATION_PURSUIT_MODE:
                actions.append(("TRY STATIC CALIBRATION", self._switch_to_static, False))
        else:
            if self.calibration_method == config.CALIBRATION_PURSUIT_MODE:
                actions.extend([
                    ("TRY AGAIN", self._retry_pursuit, True),
                    ("TRY STATIC CALIBRATION", self._switch_to_static, False),
                ])
            else:
                actions.append((self._t("recalibrate"), self._retry_all_patient, True))
        for column, (label, command, primary) in enumerate(actions):
            tk.Button(
                self.result_frame,
                text=label,
                command=command,
                bg="#4A90D9" if primary else "#4B5563",
                fg="white",
                activebackground="#357ABD",
                font=(self.PATIENT_FONT, 23 if primary else 18, "bold"),
                padx=62 if primary else 34,
                pady=22 if primary else 17,
            ).grid(row=0, column=column, padx=14)

        if self.on_result_ready is not None:
            self.on_result_ready({
                "text": self._build_readiness_narration(
                    readiness,
                    weak_region=weak_region if weak_label == "WEAK" else None,
                    previous_score=previous_score,
                    improved=improved,
                    previous_kept=previous_kept,
                ),
                "score": score,
                "level": level,
                "weak_region": weak_region if weak_label == "WEAK" else None,
                "previous_score": previous_score,
                "improved": improved,
                "previous_kept": previous_kept,
            })

    def _region_patient_message(self, region: str) -> str:
        language = self.language_manager.get_language()
        if language == "kn":
            messages = {
                "left": "ಪರದೆಯ ಎಡಭಾಗದ ನಿಖರತೆಯನ್ನು ಸುಧಾರಿಸಬಹುದು.",
                "center": "ಪರದೆಯ ಮಧ್ಯಭಾಗದ ನಿಖರತೆಯನ್ನು ಸುಧಾರಿಸಬಹುದು.",
                "right": "ಪರದೆಯ ಬಲಭಾಗದ ನಿಖರತೆಯನ್ನು ಸುಧಾರಿಸಬಹುದು.",
                "top": "ಪರದೆಯ ಮೇಲ್ಭಾಗದ ನಿಖರತೆಯನ್ನು ಸುಧಾರಿಸಬಹುದು.",
                "bottom": "ಪರದೆಯ ಕೆಳಭಾಗದ ನಿಖರತೆಯನ್ನು ಸುಧಾರಿಸಬಹುದು.",
            }
        else:
            messages = {
                "left": "Left-side accuracy can be improved.",
                "center": "Centre accuracy can be improved.",
                "right": "Right-side accuracy can be improved.",
                "top": "Top-side accuracy can be improved.",
                "bottom": "Bottom-side accuracy can be improved.",
            }
        return messages.get(region, "")

    def _build_readiness_narration(
        self,
        readiness: dict[str, object],
        *,
        weak_region: str | None,
        previous_score: int | None,
        improved: bool,
        previous_kept: bool,
    ) -> str:
        """Describe exactly the patient-meaningful readiness result."""
        score = int(readiness.get("score", 0))
        level = str(readiness.get("level", Calibrator.READINESS_POOR))
        language = self.language_manager.get_language()
        if language == "kn":
            lead = f"ನಿಮ್ಮ ದೃಷ್ಟಿ ಸಿದ್ಧತೆ ಅಂಕ ನೂರಕ್ಕೆ {score}."
            level_text = {
                Calibrator.READINESS_EXCELLENT: (
                    "ಇದು ಅತ್ಯುತ್ತಮ ಫಲಿತಾಂಶ. ತುರ್ತು ಸಂವಹನ ಮತ್ತು ಅಕ್ಷರ ಟೈಪಿಂಗ್ ಬಳಸಲು ಸಿದ್ಧವಾಗಿದೆ."
                ),
                Calibrator.READINESS_GOOD: (
                    "ಇದು ಉತ್ತಮ ಫಲಿತಾಂಶ. ತುರ್ತು ಸಂವಹನ ಬಳಸಲು ಸಿದ್ಧವಾಗಿದೆ. "
                    "ಅಕ್ಷರ ಟೈಪಿಂಗ್ ಕೆಲಸ ಮಾಡುತ್ತದೆ, ಆದರೆ ಕೆಲವು ಆಯ್ಕೆಗಳಿಗೆ ಹೆಚ್ಚು ಸಮಯ ಬೇಕಾಗಬಹುದು."
                ),
                Calibrator.READINESS_EMERGENCY: (
                    "ಈ ಫಲಿತಾಂಶ ತುರ್ತು ಸಂವಹನಕ್ಕೆ ಸಿದ್ಧವಾಗಿದೆ. ಅಕ್ಷರ ಟೈಪಿಂಗ್ ಕಷ್ಟವಾಗಬಹುದು."
                ),
                Calibrator.READINESS_POOR: (
                    "ಸಾಮಾನ್ಯ ಬಳಕೆಗೆ ಮೊದಲು ಕ್ಯಾಲಿಬ್ರೇಶನ್ ಸುಧಾರಿಸಬೇಕು."
                ),
            }.get(level, "")
            change = (
                f"ನಿಮ್ಮ ಸಿದ್ಧತೆ {previous_score} ರಿಂದ {score} ಕ್ಕೆ ಸುಧಾರಿಸಿದೆ."
                if improved and previous_score is not None else
                ("ಹಿಂದಿನ ಉತ್ತಮ ಕ್ಯಾಲಿಬ್ರೇಶನ್ ಉಳಿಸಲಾಗಿದೆ." if previous_kept else "")
            )
        else:
            lead = f"Your gaze readiness score is {score} out of 100."
            level_text = {
                Calibrator.READINESS_EXCELLENT: (
                    "This is an excellent result. Emergency communication and "
                    "individual letter typing are ready to use."
                ),
                Calibrator.READINESS_GOOD: (
                    "This is a good result. Emergency communication is ready to use. "
                    "Individual letter typing should work, but some targets may require more time."
                ),
                Calibrator.READINESS_EMERGENCY: (
                    "This result is emergency ready. Emergency communication can be used. "
                    "Individual letter typing may be difficult."
                ),
                Calibrator.READINESS_POOR: (
                    "Calibration should be improved before normal use."
                ),
            }.get(level, "")
            change = (
                f"Your readiness improved from {previous_score} to {score}."
                if improved and previous_score is not None else
                ("The previous, better calibration has been kept." if previous_kept else "")
            )
        region_text = self._region_patient_message(weak_region) if weak_region else ""
        return " ".join(part for part in (lead, level_text, region_text, change) if part)

    def _draw_readiness_debug(self, readiness: dict[str, object]) -> None:
        """Draw compact component and region details for developers."""
        regions = readiness.get("region_labels", {})
        errors = readiness.get("region_errors", {})
        region_text = "  ".join(
            f"{name.upper()}: {regions.get(name, '--')} "
            f"({float(errors.get(name, 0)):.0f}px)"
            for name in ("left", "center", "right", "top", "bottom")
        )
        metrics = self.calibrator.quality_metrics if self.calibrator else {}
        method = self.calibrator.method if self.calibrator else "--"
        lag = self.calibrator.selected_pursuit_lag_ms if self.calibrator else None
        holdout = (
            self.calibrator.pursuit_coverage.get("holdout_samples", 0)
            if self.calibrator else 0
        )
        covered = (
            self.calibrator.pursuit_coverage.get("covered_bins", 0)
            if self.calibrator else 0
        )
        text = (
            f"Method {method}  |  Selected lag {lag if lag is not None else '--'} ms"
            f"  |  Coverage {covered}/{config.PURSUIT_GRID_COLUMNS * config.PURSUIT_GRID_ROWS}"
            f"  |  Hold-out {holdout}\n"
            f"Accuracy {float(readiness.get('accuracy_score', 0)):.1f}  |  "
            f"Stability {float(readiness.get('stability_score', 0)):.1f}  |  "
            f"Coverage {float(readiness.get('coverage_score', 0)):.1f}  |  "
            f"Tracking {float(readiness.get('tracking_score', 0)):.1f}\n"
            f"Emergency {int(readiness.get('emergency_readiness', 0))}  |  "
            f"Typing {int(readiness.get('typing_readiness', 0))}  |  "
            f"Weakest {readiness.get('weakest_region', '--')}\n"
            f"Error mean/median/max: {float(metrics.get('mean_error', 0)):.0f}/"
            f"{float(metrics.get('median_error', 0)):.0f}/"
            f"{float(metrics.get('max_error', 0)):.0f}px\n{region_text}"
        )
        self.canvas.create_text(
            self.screen_width // 2,
            int(self.screen_height * 0.67),
            text=text,
            fill="#8EC5FF",
            font=("Consolas", 11, "bold"),
            justify="center",
            width=int(self.screen_width * 0.94),
        )

    def _improve_accuracy(self) -> None:
        """Recollect only the weakest region once after explicit selection."""
        self._notify_patient_action("improve_accuracy")
        if self.calibrator is None:
            return
        region = self.calibrator.get_weakest_region()
        if region is None:
            return
        if not Calibrator.regional_improvement_available(
            region, self.regional_improvement_attempts
        ):
            self._show_readiness_result(
                notice="The regional improvement limit has been reached."
            )
            return
        points = Calibrator.regional_calibration_points(region)
        if not points:
            return
        self.regional_improvement_attempts[region] += 1
        self.retry_region = region
        self._begin_retry(points, retry_all=False)

    @staticmethod
    def _snapshot_readiness(snapshot: dict[str, object] | None) -> dict[str, object]:
        if snapshot is None:
            return {}
        readiness = snapshot.get("readiness_metrics", {})
        return dict(readiness) if isinstance(readiness, dict) else {}

    def _show_results(self, metrics: dict[str, object]) -> None:
        """Draw the detailed developer calibration result screen."""
        self.canvas.delete("all")
        self._remove_result_controls()
        current_snapshot = self.calibrator.create_snapshot() if self.calibrator else None
        if self.best_session_candidate is None and current_snapshot is not None:
            self.best_session_candidate = current_snapshot
        best_metrics = self._snapshot_metrics(self.best_session_candidate)
        self._draw_metric_column(0.28, "CURRENT CALIBRATION", metrics)
        self._draw_metric_column(0.72, "BEST THIS SESSION", best_metrics)
        current_is_best = not Calibrator.is_preferred_candidate(
            best_metrics, metrics, self.screen_width, self.screen_height
        )
        quality_state = Calibrator.classify_quality(
            metrics,
            screen_width=self.screen_width,
            screen_height=self.screen_height,
        )
        message = self.status_message or (
            "This is the best result from this calibration session."
            if current_is_best
            else (
                "This result is worse than your best calibration. "
                "Your best result is still preserved."
            )
        )
        self.status_message = ""
        self.canvas.create_text(
            self.screen_width // 2, int(self.screen_height * 0.55),
            text=f"Quality: {quality_state}\n{message}",
            fill="#55D187" if self.calibrator and self.calibrator.quality_passes() else "#FFD166",
            font=("Arial", 18, "bold"), width=int(self.screen_width * 0.82)
        )
        self.result_frame = tk.Frame(self, bg="black")
        self.result_frame.place(relx=0.5, rely=0.72, anchor="center")
        actions = []
        if (
            self.calibrator is not None
            and (
                self.calibrator.quality_passes()
                or config.CALIBRATION_DEVELOPER_OVERRIDE
            )
        ):
            actions.append(("ACCEPT", self._accept))
        actions.extend([
            ("RETRY ALL", self._retry_all),
            ("RETRY POOR POINTS", self._retry_poor),
        ])
        if not current_is_best:
            actions.append(("RESTORE BEST", self._restore_best))
        for column, (label, command) in enumerate(actions):
            tk.Button(
                self.result_frame, text=label, command=command,
                bg="#4A90D9", fg="white", activebackground="#357ABD",
                font=("Arial", 17, "bold"), padx=24, pady=16
            ).grid(row=0, column=column, padx=10)

    def _show_retry_comparison(
        self,
        previous_metrics: dict[str, object],
        retry_metrics: dict[str, object],
        improved: bool,
        quality_state: str,
        retry_was_discarded: bool,
    ) -> None:
        """Draw detailed retry comparison controls in developer mode only."""
        self.canvas.delete("all")
        self._remove_result_controls()
        self._draw_metric_column(0.28, "CURRENT CALIBRATION", previous_metrics)
        self._draw_metric_column(0.72, "RETRY RESULT", retry_metrics)
        if retry_was_discarded:
            message = "Retry was not preferred. Best session calibration restored."
        elif improved and quality_state in {
            Calibrator.QUALITY_MODERATE,
            Calibrator.QUALITY_UNUSABLE,
        }:
            message = (
                "Retry improved the balanced score; practical quality is "
                f"{quality_state}."
            )
        else:
            message = "Calibration improved." if improved else "Retry did not improve."
        self.canvas.create_text(
            self.screen_width // 2, int(self.screen_height * 0.49),
            text=(
                f"{message}\nQuality state: {quality_state}  |  "
                f"Improved vs previous: {improved}"
            ),
            fill=(
                "#55D187"
                if quality_state in {
                    Calibrator.QUALITY_GOOD,
                    Calibrator.QUALITY_ACCEPTABLE,
                    Calibrator.QUALITY_MODERATE,
                }
                else "#FFD166"
            ),
            font=("Arial", 20, "bold"), width=int(self.screen_width * 0.85)
        )
        best_metrics = self._snapshot_metrics(self.best_session_candidate)
        self.canvas.create_text(
            self.screen_width // 2, int(self.screen_height * 0.56),
            text=(
                "BEST THIS SESSION  "
                f"Average {float(best_metrics['mean_error']):.0f}px  |  "
                f"Maximum {float(best_metrics['max_error']):.0f}px  |  "
                f"Score {Calibrator.comparison_score(best_metrics):.1f}\n"
                f"Left {float(best_metrics.get('left_mean_error') or 0):.0f}px  |  "
                f"Centre {float(best_metrics.get('center_mean_error') or 0):.0f}px  |  "
                f"Right {float(best_metrics.get('right_mean_error') or 0):.0f}px"
            ),
            fill="#8EC5FF", font=("Arial", 17, "bold")
        )
        if Calibrator.is_better_candidate(best_metrics, retry_metrics):
            self.canvas.create_text(
                self.screen_width // 2, int(self.screen_height * 0.63),
                text=(
                    "This result is worse than your best calibration. "
                    "Your best result is still preserved."
                ),
                fill="#FFD166", font=("Arial", 15, "bold")
            )
        self.result_frame = tk.Frame(self, bg="black")
        self.result_frame.place(relx=0.5, rely=0.75, anchor="center")
        retry_usable = quality_state in {
            Calibrator.QUALITY_GOOD,
            Calibrator.QUALITY_ACCEPTABLE,
        } or (
            quality_state == Calibrator.QUALITY_MODERATE
            and config.ALLOW_MODERATE_CALIBRATION
        )
        actions = []
        if improved and retry_usable and not retry_was_discarded:
            actions.append(("USE IMPROVED CALIBRATION", self._use_improved))
        actions.extend([
            ("KEEP PREVIOUS", self._keep_previous),
            ("RETRY AGAIN", self._retry_again),
            ("RESTORE BEST", self._restore_best),
        ])
        for column, (label, command) in enumerate(actions):
            tk.Button(
                self.result_frame, text=label, command=command,
                bg="#4A90D9", fg="white", activebackground="#357ABD",
                font=("Arial", 16, "bold"), padx=20, pady=15
            ).grid(row=0, column=column, padx=10)

    def _show_patient_result(
        self,
        metrics: dict[str, object],
        quality_state: str,
        notice: str = "",
        *,
        allow_point_retry: bool = True,
    ) -> None:
        """Show one simple, safe next step in normal patient mode."""
        self.canvas.delete("all")
        self._remove_result_controls()
        poor_points = self.calibrator.get_poor_points() if self.calibrator else []
        can_retry_points = Calibrator.should_automatically_retry_points(
            quality_state,
            len(poor_points),
            self.automatic_poor_retry_count,
        ) and allow_point_retry
        actions: list[tuple[str, Callable[[], None], bool]] = []

        if quality_state == Calibrator.QUALITY_GOOD:
            heading = "Calibration complete"
            body = "Accuracy: Good"
            actions = [("CONTINUE", self._accept, True)]
            colour = "#55D187"
        elif can_retry_points:
            heading = "A few calibration points need checking"
            body = (
                "We will repeat only those points once.\n\n"
                "The best result will be kept automatically."
            )
            actions = [("CONTINUE", self._automatic_retry_poor_points, True)]
            colour = "#FFD166"
        elif quality_state == Calibrator.QUALITY_ACCEPTABLE:
            heading = "Calibration complete"
            body = "Accuracy: Acceptable\n\nYou can start using GazeLink."
            actions = [("CONTINUE", self._accept, True)]
            colour = "#55D187"
        elif quality_state == Calibrator.QUALITY_MODERATE:
            heading = "Calibration complete"
            body = (
                "Accuracy may be reduced in some areas.\n\n"
                "You can continue or recalibrate later."
            )
            if config.ALLOW_MODERATE_CALIBRATION:
                actions.append(("CONTINUE", self._accept, True))
            actions.append(("RECALIBRATE", self._retry_all_patient, False))
            colour = "#FFD166"
        else:
            heading = "Calibration failed"
            body = (
                "Calibration could not detect your eye movement reliably.\n\n"
                "Please adjust your position and try again."
            )
            actions = [("RETRY", self._retry_all_patient, True)]
            colour = "#FFD166"

        if notice:
            body = f"{notice}\n\n{body}"
        self.canvas.create_text(
            self.screen_width // 2,
            int(self.screen_height * 0.29),
            text=heading,
            fill=colour,
            font=("Arial", 34, "bold"),
            width=int(self.screen_width * 0.82),
            justify="center",
        )
        self.canvas.create_text(
            self.screen_width // 2,
            int(self.screen_height * 0.47),
            text=body,
            fill="white",
            font=("Arial", 23, "bold"),
            width=int(self.screen_width * 0.78),
            justify="center",
        )
        self.result_frame = tk.Frame(self, bg="black")
        self.result_frame.place(relx=0.5, rely=0.72, anchor="center")
        for column, (label, command, primary) in enumerate(actions):
            tk.Button(
                self.result_frame,
                text=label,
                command=command,
                bg="#4A90D9" if primary else "#4B5563",
                fg="white",
                activebackground="#357ABD",
                font=("Arial", 24 if primary else 19, "bold"),
                padx=70 if primary else 38,
                pady=24 if primary else 18,
            ).grid(row=0, column=column, padx=14)

    def _show_model_failure(self) -> None:
        """Stop after a fit failure and require an explicit full retry."""
        self.canvas.delete("all")
        self._remove_result_controls()
        self.canvas.create_text(
            self.screen_width // 2,
            int(self.screen_height * 0.31),
            text=self._t("calibration_failed"),
            fill="#FFD166",
            font=("Arial", 34, "bold"),
        )
        self.canvas.create_text(
            self.screen_width // 2,
            int(self.screen_height * 0.48),
            text=(
                self._t("adjust_position")
            ),
            fill="white",
            font=("Arial", 23, "bold"),
            justify="center",
        )
        self.result_frame = tk.Frame(self, bg="black")
        self.result_frame.place(relx=0.5, rely=0.72, anchor="center")
        tk.Button(
            self.result_frame,
            text=self._t("retry"),
            command=self._retry_all_patient,
            bg="#4A90D9",
            fg="white",
            font=("Arial", 24, "bold"),
            padx=70,
            pady=24,
        ).pack()

    def _automatic_retry_poor_points(self) -> None:
        if self.calibrator is None:
            return
        if (
            self.automatic_poor_retry_count
            >= config.MAX_AUTOMATIC_POOR_POINT_RETRIES
        ):
            state = Calibrator.classify_quality(
                self.calibrator.quality_metrics,
                screen_width=self.screen_width,
                screen_height=self.screen_height,
            )
            self._show_patient_result(dict(self.calibrator.quality_metrics), state)
            return
        poor = self.calibrator.get_poor_points()
        if not poor:
            self._retry_all_patient()
            return
        self.automatic_poor_retry_count += 1
        self._begin_retry(poor, retry_all=False)

    def _retry_all_patient(self) -> None:
        """Start a full calibration only after this explicit button action."""
        self._notify_patient_action("recalibrate")
        if self.calibration_method == config.CALIBRATION_PURSUIT_MODE:
            self._retry_pursuit()
            return
        self.automatic_poor_retry_count = 0
        if self.fast_calibration:
            self.fast_point_retry_counts = {
                point: 0 for point in Calibrator.REQUIRED_POINTS
            }
        self.full_calibration_attempted = True
        points = list(Calibrator.REQUIRED_POINTS)
        if self.calibrator is not None:
            self._begin_retry(points, retry_all=True)
            return
        if self.on_retry_points is None:
            return
        self._remove_result_controls()
        self.calibrator = self.on_retry_points(points, None, True)
        self.mode = "collection"
        self.active_points = points
        self.current_index = 0
        self.status_message = "Starting a new calibration"
        self._show_target()
        self._resume_updates()

    def _retry_pursuit(self) -> None:
        """Repeat pursuit only after an explicit user/caregiver action."""
        self._notify_patient_action("retry")
        if self.on_switch_method is None:
            return
        self._remove_result_controls()
        self.calibrator = self.on_switch_method(config.CALIBRATION_PURSUIT_MODE)
        self.calibration_method = config.CALIBRATION_PURSUIT_MODE
        self.fast_calibration = False
        self.calibration_run_started_at = time.monotonic()
        self.fresh_collection_performed = True
        self.mode = "pursuit"
        self.pursuit_path = Calibrator.build_pursuit_path()
        self.status_message = ""
        self._show_target()
        self._resume_updates()

    def _switch_to_static(self) -> None:
        """Start the preserved static method only after explicit selection."""
        self._notify_patient_action("try_static")
        if self.on_switch_method is None:
            return
        self._remove_result_controls()
        self.calibrator = self.on_switch_method(config.CALIBRATION_STATIC_MODE)
        self.calibration_method = config.CALIBRATION_STATIC_MODE
        self.fast_calibration = False
        self.calibration_run_started_at = time.monotonic()
        self.fresh_collection_performed = True
        self.mode = "collection"
        self.active_points = list(Calibrator.REQUIRED_POINTS)
        self.current_index = 0
        self.status_message = "Static nine-point comparison"
        self._show_target()
        self._resume_updates()

    def _draw_metric_column(
        self, relative_x: float, heading: str, metrics: dict[str, object]
    ) -> None:
        x = int(self.screen_width * relative_x)
        lines = (
            heading,
            f"Average: {float(metrics['mean_error']):.0f} px",
            f"Median: {float(metrics['median_error']):.0f} px",
            f"Maximum: {float(metrics['max_error']):.0f} px",
            f"Left: {float(metrics.get('left_mean_error') or 0):.0f} px",
            f"Centre: {float(metrics.get('center_mean_error') or 0):.0f} px",
            f"Right: {float(metrics.get('right_mean_error') or 0):.0f} px",
            f"Balanced score: {Calibrator.comparison_score(metrics):.1f}",
        )
        for index, text in enumerate(lines):
            self.canvas.create_text(
                x, 60 + index * 38, text=text,
                fill="#8EC5FF" if index == 0 else "white",
                font=("Arial", 22 if index == 0 else 17, "bold" if index == 0 else "normal")
            )

    @staticmethod
    def _snapshot_metrics(snapshot: dict[str, object] | None) -> dict[str, object]:
        if snapshot is None:
            return {}
        metrics = snapshot.get("quality_metrics", {})
        return dict(metrics) if isinstance(metrics, dict) else {}

    def _accept(self) -> None:
        self._notify_patient_action("continue")
        if self.calibrator is None:
            return
        if (
            not self.calibrator.quality_passes()
            and not config.CALIBRATION_DEVELOPER_OVERRIDE
        ):
            self.status_message = "Calibration is not accurate enough to continue."
            self._show_readiness_result(
                notice="Calibration is not ready to continue."
            )
            return
        if not self.calibrator.save_profile():
            self.status_message = "Calibration profile could not be saved."
            return
        if self.on_verified is not None:
            self.on_verified(True)
        self.close()

    def _retry_all(self) -> None:
        self._begin_retry(list(Calibrator.REQUIRED_POINTS), retry_all=True)

    def _retry_poor(self) -> None:
        if self.calibrator is None:
            return
        poor = self.calibrator.get_poor_points()
        if not poor:
            self.status_message = "No individual verification point exceeds the retry limit."
            self._remove_result_controls()
            self._show_results(self.calibrator.quality_metrics)
            return
        self._begin_retry(poor, retry_all=False)

    def _begin_retry(
        self,
        points: Sequence[str],
        *,
        retry_all: bool,
        base_snapshot: dict[str, object] | None = None,
    ) -> None:
        if self.calibrator is None or self.on_retry_points is None:
            return
        self._remove_result_controls()
        origin = base_snapshot or self.calibrator.create_snapshot()
        self.retry_origin_candidate = Calibrator.from_snapshot(origin).create_snapshot()
        self.retry_origin_metrics = self._snapshot_metrics(self.retry_origin_candidate)
        self.retry_points = list(points)
        self.retry_was_all = retry_all
        if retry_all:
            self.retry_region = None
        self.calibrator = self.on_retry_points(
            points,
            self.retry_origin_candidate,
            retry_all,
        )
        self.mode = "collection"
        self.active_points = list(points)
        self.current_index = 0
        self.status_message = (
            "Collecting all nine points in a protected retry"
            if retry_all
            else "Recollecting only poor points in a protected retry"
        )
        self._show_target()
        self._resume_updates()

    def _notify_patient_action(self, action: str) -> None:
        """Let the controller cancel result narration before acting."""
        if self.on_patient_action is not None:
            self.on_patient_action(action)

    def _keep_previous(self) -> None:
        if self.calibrator is None or self.retry_origin_candidate is None:
            return
        self.calibrator.restore_snapshot(self.retry_origin_candidate)
        metrics = dict(self.calibrator.quality_metrics)
        self._clear_retry_state()
        self._remove_result_controls()
        self._show_results(metrics)

    def _accept_previous(self) -> None:
        if self.calibrator is None or self.retry_origin_candidate is None:
            return
        self.calibrator.restore_snapshot(self.retry_origin_candidate)
        self._accept()

    def _use_improved(self) -> None:
        self._accept()

    def _retry_again(self) -> None:
        if self.retry_origin_candidate is None:
            return
        points = list(self.retry_points)
        retry_all = self.retry_was_all
        origin = self.retry_origin_candidate
        self._begin_retry(points, retry_all=retry_all, base_snapshot=origin)

    def _restore_best(self) -> None:
        if self.calibrator is None or self.best_session_candidate is None:
            return
        self.calibrator.restore_snapshot(self.best_session_candidate)
        self._remove_result_controls()
        self._show_results(dict(self.calibrator.quality_metrics))

    def _clear_retry_state(self) -> None:
        self.retry_origin_candidate = None
        self.retry_origin_metrics = None
        self.retry_points = []
        self.retry_was_all = False
        self.retry_region = None

    def _resume_updates(self) -> None:
        if self.after_id is None and not self.finished:
            self.after_id = self.after(self.frame_delay_ms, self._update)

    def _remove_result_controls(self) -> None:
        if self.result_frame is not None:
            self.result_frame.destroy()
            self.result_frame = None

    def _update_ring(self, progress: float) -> None:
        radius = self.RING_START_RADIUS - (
            self.RING_START_RADIUS - self.DOT_RADIUS
        ) * progress
        if self.ring_id is not None:
            self.canvas.coords(
                self.ring_id,
                self.target_x - radius, self.target_y - radius,
                self.target_x + radius, self.target_y + radius,
            )

    def _draw_sample_status(self, target_count: int, debug: dict[str, Any]) -> None:
        self.canvas.delete("sample_status")
        state = "valid" if debug.get("valid") else "blink / no valid gaze"
        text = (
            f"Valid samples: {len(self.valid_samples)} / {target_count}\n{state}"
            if config.CALIBRATION_DEBUG
            else (
                "Eyes detected"
                if debug.get("valid")
                else self._t("blink_normally")
            )
        )
        self.canvas.create_text(
            self.target_x, self.target_y + 108,
            text=text,
            fill="#C8D1E0", font=(self.PATIENT_FONT, 15), justify="center",
            tags="sample_status"
        )

    def _t(self, key: str) -> str:
        return self.language_manager.translate_ui(key)

    def _draw_verification_result(self, result: dict[str, float | str]) -> None:
        actual_x, actual_y = float(result["actual_x"]), float(result["actual_y"])
        predicted_x, predicted_y = float(result["predicted_x"]), float(result["predicted_y"])
        self.canvas.create_line(
            actual_x, actual_y, predicted_x, predicted_y,
            fill="#FFD166", width=4, tags="verification_result"
        )
        self._draw_dot(predicted_x, predicted_y, "#00A6FB", "verification_result", 14)

    def _draw_dot(self, x: float, y: float, colour: str, tag: str, radius: int | None = None) -> None:
        radius = radius or self.DOT_RADIUS
        self.canvas.create_oval(
            x - radius, y - radius, x + radius, y + radius,
            fill=colour, outline="white", width=3, tags=tag
        )

    def close(self) -> None:
        """Stop capture and close safely; accepted completion is preserved."""
        if self.finished:
            return
        self.finished = True
        if self.after_id is not None:
            try:
                self.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        self.eye_tracker.stop()
        try:
            self.destroy()
        except tk.TclError:
            pass


def run_verification_state_synthetic_tests() -> None:
    """Exercise bounded verification decisions without opening a Tk window."""
    outcomes = [Calibrator.verification_outcome(12, 0.5) for _ in range(5)]
    assert all(item == ("OK", "sample target") for item in outcomes)
    assert Calibrator.verification_outcome(7, 2.0) == ("OK", "timeout")
    assert Calibrator.verification_outcome(0, 2.0) == ("NO_GAZE", "timeout")
    # Invalid/blink frames do not add samples, while monotonic elapsed time wins.
    assert Calibrator.verification_outcome(0, 1.99) is None
    assert Calibrator.verification_outcome(0, 2.0) == ("NO_GAZE", "timeout")
    assert CalibrationScreen._updates_should_continue(False, "verification")
    assert CalibrationScreen._updates_should_continue(False, "profile_validation")
    assert not CalibrationScreen._updates_should_continue(False, "results")
    maximum_point_time = (
        config.VERIFICATION_SETTLE_SECONDS
        + config.VERIFICATION_MAX_SECONDS
        + config.VERIFICATION_ADVANCE_MESSAGE_SECONDS
        + 2.0 / max(1, config.FPS_TARGET)
    )
    assert maximum_point_time < 3.0
    print("Verification state synthetic tests passed.")


if __name__ == "__main__":
    import sys

    if "--synthetic-test" in sys.argv:
        run_verification_state_synthetic_tests()
