"""GazeLink application controller."""

import math
import statistics
import sys
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox
from typing import Any

try:
    from . import config
    from .core.calibration import Calibrator
    from .core.eye_tracker import EyeTracker
    from .core.smoother import GazeIntentTracker, ScreenGazeSmoother
    from .core.word_predictor import WordPredictor
    from .ui.calibration_screen import CalibrationScreen
    from .ui.keyboard import KeyboardScreen
    from .utils.language_manager import (
        LANG_ENGLISH,
        LANG_KANNADA,
        LanguageManager,
        get_kannada_readiness_speech_parts,
    )
    from .utils.tts import TextToSpeech
    from .utils.runtime import get_data_path, get_logger, setup_logging
except ImportError:
    import config
    from core.calibration import Calibrator
    from core.eye_tracker import EyeTracker
    from core.smoother import GazeIntentTracker, ScreenGazeSmoother
    from core.word_predictor import WordPredictor
    from ui.calibration_screen import CalibrationScreen
    from ui.keyboard import KeyboardScreen
    from utils.language_manager import (
        LANG_ENGLISH,
        LANG_KANNADA,
        LanguageManager,
        get_kannada_readiness_speech_parts,
    )
    from utils.tts import TextToSpeech
    from utils.runtime import get_data_path, get_logger, setup_logging


# Testing-only mode: use the mouse for both axes and skip camera calibration.
MOUSE_TEST_MODE = False
FORCE_RECALIBRATION = False
CALIBRATION_MODE_OVERRIDE: str | None = None
SKIP_VERIFICATION = False
DEMO_MODE = False
PRESENTATION_MODE = False


class GazeLinkApp:
    """Coordinate calibration, gaze targeting, communication actions, and TTS."""

    EMERGENCY_PREFIX = "EMERGENCY:"
    SUGGESTION_PREFIX = "SUGGESTION:"
    GROUP_PREFIX = "GROUP:"

    def __init__(self) -> None:
        self.logger = setup_logging(config.GAZELINK_VERSION, config.LOG_PATH)
        self.calibrator = Calibrator()
        self.calibration_mode = (
            CALIBRATION_MODE_OVERRIDE or config.CALIBRATION_MODE
        )
        self.calibrator.set_method(self.calibration_mode)
        self.language_manager = LanguageManager()
        language = self.language_manager.get_language()
        self.predictor = WordPredictor(language=language, max_suggestions=3)
        self.tts = TextToSpeech(language=language)
        self.spoken_instructions: set[str] = set()
        self.mode_instruction_sequence = 0
        self.speech_context_sequence = 0
        self.speech_owner = "startup"
        self.language_announcement_sequence = 0
        self.smoother = ScreenGazeSmoother()
        self.intent_tracker = GazeIntentTracker()

        self.calibration_screen: CalibrationScreen | None = None
        self.keyboard: KeyboardScreen | None = None
        self.eye_tracker: EyeTracker | None = None
        self.after_id: str | None = None

        self.candidate_key: str | None = None
        self.candidate_started_at: float | None = None
        self.candidate_source = "NONE"
        self.candidate_acquire_time = config.TARGET_ACQUIRE_TIME
        self.current_key: str | None = None
        self.focus_started_at: float | None = None
        self.dwell_progress = 0.0
        self.selection_locked = False
        self.selection_anchor: tuple[float, float] | None = None
        self.last_control_point: tuple[float, float] | None = None
        self.target_loss_started_at: float | None = None
        self.invalid_started_at: float | None = None
        self.invalid_frame_count = 0
        self.last_frame_time = time.monotonic()
        self.maximum_right_reach = 0.0
        self.horizontal_debug: dict[str, Any] = {}
        self.model_debug: dict[str, Any] = {}
        self.dwell_block_reason = "NO_TARGET"
        self.last_intent_trace_time = 0.0
        self.last_intent_trace_signature = None
        self.last_target_resolution: dict[str, object] = {
            "hit_test": "NONE",
            "direct_target": None,
            "final_target": None,
        }

        self.calibration_succeeded = False
        self.calibration_verified = False
        self.shutting_down = False
        self.caregiver_language_dialog_active = False
        self.tracking_lost = False
        self.camera_failure_prompted = False
        self.fast_last_readiness_score: int | None = None
        self.interval_ms = max(1, int(1000 / config.FPS_TARGET))

        # Presentation Mode is an isolated relative-gaze controller.  It never
        # reads or writes normal calibration coefficients or profiles.
        self.presentation_mode = bool(PRESENTATION_MODE)
        self.presentation_state = "OFF"
        self.presentation_neutral: dict[str, float] | None = None
        self.presentation_samples: list[tuple[float, ...]] = []
        self.presentation_started_at: float | None = None
        self.presentation_ready_until: float | None = None
        self.presentation_delta_history = deque(
            maxlen=config.PRESENTATION_SMOOTHING_WINDOW
        )
        self.presentation_direction = "UNCERTAIN"
        self.presentation_last_logged_direction: str | None = None
        self.presentation_drift_started_at: float | None = None
        self.presentation_invalid_started_at: float | None = None
        self.presentation_locked_direction: str | None = None
        self.presentation_leave_started_at: float | None = None
        self.presentation_return_to_normal = False

    def run(self) -> None:
        """Run calibration and communication roots sequentially."""
        try:
            if not self.select_startup_language():
                return
            if self.presentation_mode:
                if self.start_presentation():
                    self.keyboard.mainloop()
                if not self.presentation_return_to_normal or self.shutting_down:
                    return
            if not self.show_calibration_instructions():
                return
            if MOUSE_TEST_MODE:
                if self.start_keyboard():
                    self.keyboard.mainloop()
                return

            if not self.start_calibration():
                return

            self.calibration_screen.mainloop()
            calibration_tracker = self.calibration_screen.eye_tracker
            if not self.shutting_down:
                self._release_tracker(calibration_tracker)
            self.calibration_screen = None

            if self.shutting_down:
                return
            usable_calibration = self._calibration_use_allowed()
            if (
                not self.calibration_succeeded
                or not self.calibration_verified
                or not usable_calibration
            ):
                detail = self.calibrator.get_error() or (
                    "Calibration did not meet the permitted practical quality level."
                )
                print(f"Calibration failed: {detail}")
                return

            if self.start_keyboard():
                self.keyboard.mainloop()
        except Exception:
            self.logger.exception("unexpected application exception")
            self._show_error(
                "GazeLink Error",
                "GazeLink encountered an unexpected error and will close safely.",
                self.keyboard,
            )
        finally:
            self.shutdown()

    def select_startup_language(self) -> bool:
        """Show the patient welcome and large language choices."""
        self._enter_speech_context("welcome")
        selected = {"confirmed": False}
        saved_language = self.language_manager.get_language()
        root = tk.Tk()
        root.title("GazeLink")
        root.configure(bg="#F5F7FA")
        root.attributes("-fullscreen", True)

        tk.Label(
            root,
            text="GazeLink",
            bg="#F5F7FA",
            fg="#1A1A2A",
            font=("Nirmala UI", 48, "bold"),
        ).pack(fill=tk.X, pady=(38, 4))
        tk.Label(
            root,
            text="Assistive Eye-Gaze Communication",
            bg="#F5F7FA", fg="#526072",
            font=("Nirmala UI", 24, "bold"),
        ).pack(fill=tk.X, pady=(0, 22))
        tk.Label(
            root,
            text=self.language_manager.get_instruction("welcome", LANG_ENGLISH),
            bg="#F5F7FA", fg="#1A1A2A",
            font=("Nirmala UI", 25, "bold"),
        ).pack(fill=tk.X, pady=(0, 8))
        frame = tk.Frame(root, bg="#F5F7FA")
        frame.pack(fill=tk.BOTH, expand=True, padx=55, pady=(12, 42))

        choosing = {"active": False}

        def choose(code: str) -> None:
            if choosing["active"]:
                return
            choosing["active"] = True
            self._enter_speech_context("calibration-instructions")
            if self.change_language(
                code, show_warning=True, announce_selection=False
            ):
                selected["confirmed"] = True
                root.after(100, root.destroy)
            else:
                choosing["active"] = False

        for column, (label, code) in enumerate(
            (("ENGLISH", LANG_ENGLISH), ("ಕನ್ನಡ", LANG_KANNADA))
        ):
            saved = code == saved_language
            tk.Button(
                frame,
                text=(
                    f"{label}\n✓ {self.language_manager.get_text('saved_language')}"
                    if saved else label
                ),
                command=lambda value=code: choose(value),
                bg="#315F9B" if saved else "#4A90D9",
                fg="white",
                activebackground="#357ABD",
                font=("Nirmala UI", 36, "bold"),
                bd=0,
            ).grid(row=0, column=column, sticky="nsew", padx=18, pady=18)
            frame.grid_columnconfigure(column, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        root.bind("<e>", lambda _event: choose(LANG_ENGLISH))
        root.bind("<k>", lambda _event: choose(LANG_KANNADA))
        root.bind("<Escape>", lambda _event: root.destroy())
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        self._speak_once(
            "startup-language-prompt",
            self.language_manager.get_instruction("welcome", LANG_ENGLISH),
            LANG_ENGLISH,
            priority=TextToSpeech.PRIORITY_INSTRUCTION,
        )
        root.mainloop()
        return bool(selected["confirmed"])

    def change_language(
        self,
        code: str,
        *,
        show_warning: bool = True,
        announce_selection: bool = False,
    ) -> bool:
        """Change UI, predictor, and TTS without touching camera/calibration."""
        if not self.language_manager.set_language(code):
            return False
        language = self.language_manager.get_language()
        self.logger.info("language selected code=%s", language)
        self.predictor.set_language(language)
        available = self.tts.set_language(language)
        if self.keyboard is not None:
            self.keyboard.set_language_manager(self.language_manager)
            self.update_suggestions()
        if announce_selection:
            self.language_announcement_sequence += 1
            announcement_key = (
                "kannada_selected_speech"
                if language == LANG_KANNADA else "english_selected_speech"
            )
            self._speak_once(
                f"language-selected:{self.language_announcement_sequence}:{language}",
                self.language_manager.get_text(announcement_key),
                language,
            )
        if language == LANG_KANNADA and not available and show_warning:
            parent = self.keyboard if self._keyboard_exists() else None
            try:
                messagebox.showwarning(
                    "Kannada offline voice",
                    (
                        self.language_manager.get_text("voice_unavailable")
                        if config.PATIENT_MODE
                        else "Kannada offline voice is not installed.\n"
                        "Please install eSpeak NG or configure its path."
                    ),
                    parent=parent,
                )
            except tk.TclError:
                print(
                    "Kannada offline voice is not installed. "
                    "Please install eSpeak NG or configure its path."
                )
        return True

    def _enter_speech_context(
        self, name: str, reason: str = "screen_changed"
    ) -> str:
        """Own new-screen speech and synchronously interrupt the old screen."""
        self.speech_context_sequence += 1
        self.speech_owner = f"{name}:{self.speech_context_sequence}"
        self.tts.set_owner(self.speech_owner, reason)
        return self.speech_owner

    def _speak_once(
        self,
        event: str,
        text: str,
        language: str,
        *,
        priority: int = TextToSpeech.PRIORITY_INSTRUCTION,
        cache: bool = True,
    ) -> bool:
        """Queue a patient instruction once per named session event."""
        if event in self.spoken_instructions:
            return False
        accepted = self.tts.speak(
            text,
            language=language,
            priority=priority,
            owner=self.speech_owner,
            cache=cache,
        )
        if accepted:
            self.spoken_instructions.add(event)
        return accepted

    def show_calibration_instructions(self) -> bool:
        """Explain both stages while keeping START immediately available."""
        confirmed = {"value": False}
        language = self.language_manager.get_language()
        if not self.speech_owner.startswith("calibration-instructions:"):
            self._enter_speech_context("calibration-instructions")
        root = tk.Tk()
        root.title(self.language_manager.get_text("calibration_title"))
        root.configure(bg="#F5F7FA")
        root.attributes("-fullscreen", True)

        tk.Label(
            root,
            text=self.language_manager.get_text("calibration_title"),
            bg="#F5F7FA", fg="#1A1A2A",
            font=("Nirmala UI", 34, "bold"),
        ).pack(fill=tk.X, pady=(24, 12))
        instruction = self.language_manager.get_instruction(
            "calibration", language
        )
        instruction_owner = self.speech_owner
        tk.Label(
            root,
            text=instruction,
            bg="#F5F7FA", fg="#344054",
            font=("Nirmala UI", 19, "bold"),
            justify="center", wraplength=int(root.winfo_screenwidth() * 0.78),
        ).pack(fill=tk.X, padx=60, pady=(0, 12))

        diagrams = tk.Frame(root, bg="#F5F7FA")
        diagrams.pack(fill=tk.BOTH, expand=True, padx=55, pady=4)
        diagrams.grid_columnconfigure(0, weight=1)
        diagrams.grid_columnconfigure(1, weight=1)
        diagrams.grid_rowconfigure(0, weight=1)

        calibration_canvas = tk.Canvas(
            diagrams, bg="white", highlightthickness=2,
            highlightbackground="#B8C4D6",
        )
        verification_canvas = tk.Canvas(
            diagrams, bg="white", highlightthickness=2,
            highlightbackground="#B8C4D6",
        )
        calibration_canvas.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        verification_canvas.grid(row=0, column=1, sticky="nsew", padx=(12, 0))

        def draw_diagrams() -> None:
            self._draw_calibration_diagram(calibration_canvas)
            self._draw_verification_diagram(verification_canvas)

        calibration_canvas.bind("<Configure>", lambda _event: draw_diagrams())
        verification_canvas.bind("<Configure>", lambda _event: draw_diagrams())

        status = tk.StringVar(value=self.language_manager.get_text("ready_start"))
        tk.Label(
            root, textvariable=status, bg="#F5F7FA", fg="#526072",
            font=("Nirmala UI", 18, "bold"),
        ).pack(fill=tk.X, pady=(8, 3))

        def start() -> None:
            # Changing speech ownership synchronously stops and discards the
            # instruction before calibration begins.
            self._enter_speech_context("calibration-starting")
            confirmed["value"] = True
            root.destroy()

        start_button = tk.Button(
            root,
            text=self.language_manager.get_text("start"),
            command=lambda: start(),
            bg="#4A90D9", fg="white", activebackground="#357ABD",
            font=("Nirmala UI", 30, "bold"), bd=0,
            padx=100, pady=16,
            state=tk.NORMAL,
        )
        start_button.pack(pady=(3, 22))
        root.bind("<Return>", lambda _event: start())
        root.bind("<Escape>", lambda _event: root.destroy())
        root.protocol("WM_DELETE_WINDOW", root.destroy)

        def begin_narration() -> None:
            if self.speech_owner != instruction_owner:
                return
            self._speak_once(
                f"calibration-instruction:{language}", instruction, language,
            )

        root.after(100, begin_narration)
        root.mainloop()
        return bool(confirmed["value"])

    def _draw_calibration_diagram(self, canvas: tk.Canvas) -> None:
        """Draw the real row-major static target order as a simple path."""
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 10), max(canvas.winfo_height(), 10)
        canvas.create_text(
            width / 2, 24, text=self.language_manager.get_text("calibration_diagram"),
            fill="#1A1A2A", font=("Nirmala UI", 17, "bold"),
        )
        points = []
        for index, (_name, x_fraction, y_fraction) in enumerate(Calibrator.TARGETS, 1):
            x = 42 + x_fraction * (width - 84)
            y = 34 + y_fraction * (height - 62)
            points.append((x, y, index))
        for first, second in zip(points, points[1:]):
            canvas.create_line(
                first[0], first[1], second[0], second[1], fill="#8FA4BF",
                width=3, arrow=tk.LAST,
            )
        for x, y, index in points:
            canvas.create_oval(x - 17, y - 17, x + 17, y + 17, fill="#4A90D9", outline="")
            canvas.create_text(x, y, text=str(index), fill="white", font=("Arial", 13, "bold"))

    def _draw_verification_diagram(self, canvas: tk.Canvas) -> None:
        """Draw the real left, centre, right, up, down verification order."""
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 10), max(canvas.winfo_height(), 10)
        canvas.create_text(
            width / 2, 24, text=self.language_manager.get_text("verification_diagram"),
            fill="#1A1A2A", font=("Nirmala UI", 17, "bold"),
        )
        points = []
        for index, (_name, x_fraction, y_fraction) in enumerate(Calibrator.VERIFICATION_TARGETS, 1):
            x = 42 + x_fraction * (width - 84)
            y = 34 + y_fraction * (height - 62)
            points.append((x, y, index))
        for first, second in zip(points, points[1:]):
            canvas.create_line(
                first[0], first[1], second[0], second[1], fill="#8FA4BF",
                width=3, arrow=tk.LAST,
            )
        for x, y, index in points:
            canvas.create_oval(x - 17, y - 17, x + 17, y + 17, fill="#55A868", outline="")
            canvas.create_text(x, y, text=str(index), fill="white", font=("Arial", 13, "bold"))

    def start_calibration(self) -> bool:
        """Create the calibration root and verify its webcam."""
        self._enter_speech_context("calibration-progress")
        self._speak_once(
            f"calibration-progress:{self.speech_context_sequence}",
            self.language_manager.get_instruction("calibration_progress"),
            self.language_manager.get_language(),
            priority=TextToSpeech.PRIORITY_STATE,
        )
        self.calibrator.set_method(self.calibration_mode)
        self.calibrator.reset()
        self.calibration_succeeded = False
        self.calibration_verified = False
        try:
            screen = CalibrationScreen(
                on_sample=self.on_calibration_sample,
                on_complete=self.on_calibration_complete,
                on_verified=self.on_calibration_verified,
                on_retry_points=self.on_calibration_retry_points,
                language_manager=self.language_manager,
                calibration_mode=self.calibration_mode,
                on_pursuit_frame=self.on_calibration_pursuit_frame,
                on_switch_method=self.on_calibration_switch_method,
                skip_verification=SKIP_VERIFICATION,
                on_stage_change=self.on_calibration_stage_change,
                on_result_ready=self.on_calibration_result_ready,
                on_patient_action=self.on_calibration_patient_action,
            )
        except Exception as exc:
            print(f"Unable to start calibration: {exc}")
            self.logger.exception("unable to start calibration")
            return False

        self.calibration_screen = screen
        screen.protocol("WM_DELETE_WINDOW", self.shutdown)
        screen.bind("<Escape>", self.shutdown)

        if not self._ensure_camera_available(screen.eye_tracker, screen):
            screen.close()
            self._release_tracker(screen.eye_tracker)
            self.calibration_screen = None
            return False
        if not FORCE_RECALIBRATION and self.calibrator.load_profile(
            screen.screen_width,
            screen.screen_height,
        ):
            self.calibration_succeeded = True
            screen.start_profile_validation(self.calibrator)
        return True

    def on_calibration_stage_change(self, stage: str) -> None:
        """Own and announce calibration stages without queuing stale speech."""
        if stage == "verification_checks":
            self._enter_speech_context("calibration-verification-checks")
        elif stage == "complete":
            self._enter_speech_context("calibration-complete")

    def on_calibration_result_ready(self, result: dict[str, object]) -> None:
        """Speak the same patient meaning represented by the readiness screen."""
        if self.calibration_mode == config.CALIBRATION_FAST_MODE:
            score = int(result.get("score", 0))
            if score != self.fast_last_readiness_score:
                print(f"[FAST CAL] readiness score={score}/100")
                self.logger.info("fast calibration readiness score=%s/100", score)
                self.fast_last_readiness_score = score
        text = str(result.get("text", "")).strip()
        if not text:
            return
        owner = self._enter_speech_context("calibration-result")
        language = self.language_manager.get_language()
        if language == LANG_KANNADA:
            parts = get_kannada_readiness_speech_parts(
                score=int(result.get("score", 0)),
                level=str(result.get("level", "POOR")),
                weak_region=result.get("weak_region"),
                previous_score=result.get("previous_score"),
                improved=bool(result.get("improved", False)),
                previous_kept=bool(result.get("previous_kept", False)),
            )
            self.tts.speak_kannada_components(
                parts,
                priority=TextToSpeech.PRIORITY_STATE,
                owner=owner,
            )
            return
        self.tts.speak(
            text,
            language=language,
            priority=TextToSpeech.PRIORITY_STATE,
            owner=owner,
            cache=False,
        )

    def on_calibration_patient_action(self, action: str) -> None:
        """Patient result actions immediately interrupt result narration."""
        self._enter_speech_context(f"calibration-action-{action}", "user_action")

    def on_calibration_sample(
        self,
        point: str,
        features,
    ) -> None:
        self.calibrator.add_feature_sample(point, features)

    def on_calibration_pursuit_frame(
        self,
        timestamp: float,
        target_x: float,
        target_y: float,
        features,
    ) -> None:
        """Record one known pursuit position and its optional valid features."""
        self.calibrator.add_pursuit_target(timestamp, target_x, target_y)
        if features is not None:
            self.calibrator.add_pursuit_sample(timestamp, features)

    def on_calibration_complete(self) -> Calibrator | None:
        screen = self.calibration_screen
        width = screen.screen_width if screen is not None else 0
        height = screen.screen_height if screen is not None else 0
        self.calibrator.set_target_tolerances(
            KeyboardScreen.calibration_target_tolerances(width, height)
        )
        if self.calibration_mode == config.CALIBRATION_PURSUIT_MODE:
            self.calibration_succeeded = bool(
                self.calibrator.finish_pursuit(width, height)
            )
            self.calibrator.duration_seconds = config.PURSUIT_DURATION_SECONDS
        else:
            self.calibration_succeeded = bool(self.calibrator.finish(width, height))
            point_seconds = (
                config.FAST_CALIBRATION_POINT_SECONDS
                if self.calibration_mode == config.CALIBRATION_FAST_MODE
                else config.CALIBRATION_POINT_SECONDS
            )
            self.calibrator.duration_seconds = (
                len(Calibrator.REQUIRED_POINTS) * point_seconds
            )
        if self.calibration_succeeded:
            return self.calibrator

        detail = self.calibrator.get_error()
        print(f"Calibration failed: {detail}")
        return None

    def on_calibration_verified(self, accepted: bool) -> None:
        """Record acceptance only after fresh or saved-profile verification."""
        self.calibration_verified = bool(accepted) and self._calibration_use_allowed()
        if self.calibration_verified:
            self._print_calibration_comparison()

    def _calibration_use_allowed(self) -> bool:
        """Permit the explicit pursuit verification bypass only for developers."""
        return bool(
            self.calibrator.quality_passes()
            or config.CALIBRATION_DEVELOPER_OVERRIDE
            or (
                SKIP_VERIFICATION
                and self.calibration_mode == config.CALIBRATION_PURSUIT_MODE
                and self.calibrator.is_calibrated_2d()
            )
        )

    def on_calibration_switch_method(self, method: str) -> Calibrator:
        """Create a clean candidate for an explicitly selected A/B method."""
        candidate = Calibrator()
        candidate.set_method(method)
        self.calibrator = candidate
        self.calibration_mode = method
        self.calibration_succeeded = False
        self.calibration_verified = False
        return candidate

    @staticmethod
    def _profile_summary(path: str) -> dict[str, object] | None:
        """Read only non-biometric comparison metadata from a profile."""
        import json

        target = get_data_path(path)
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        readiness = dict(payload.get("readiness_metrics", {}))
        pursuit_coverage = dict(payload.get("pursuit_coverage", {}))
        return {
            "duration": float(payload.get("duration_seconds", 0.0)),
            "readiness": int(readiness.get("score", 0)),
            "emergency": int(readiness.get("emergency_readiness", 0)),
            "typing": int(readiness.get("typing_readiness", 0)),
            "lag": payload.get("selected_pursuit_lag_ms"),
            "tracking": float(
                pursuit_coverage.get(
                    "tracking_reliability",
                    readiness.get("valid_frame_ratio", 0.0),
                )
            ),
        }

    def _print_calibration_comparison(self) -> None:
        """Print available calibration results without declaring a winner."""
        for label, filename in (
            ("STATIC 9-POINT", config.CALIBRATION_STATIC_PROFILE_PATH),
            ("FAST 9-POINT", config.CALIBRATION_FAST_PROFILE_PATH),
            ("SMOOTH PURSUIT", config.CALIBRATION_PURSUIT_PROFILE_PATH),
        ):
            summary = self._profile_summary(filename)
            if summary is None:
                continue
            lag = (
                f"\nSelected lag: {summary['lag']} ms"
                if summary.get("lag") is not None else ""
            )
            print(
                f"{label}\nDuration: {summary['duration']:.0f} s\n"
                f"Readiness: {summary['readiness']}\n"
                f"Emergency: {summary['emergency']}\n"
                f"Typing: {summary['typing']}\n"
                f"Tracking reliability: {summary['tracking']:.0%}{lag}"
            )

    def on_calibration_retry_points(
        self,
        points,
        base_snapshot=None,
        retry_all: bool = False,
    ) -> Calibrator:
        """Create or update the isolated working candidate used by retry."""
        self.calibration_verified = False
        self.calibration_succeeded = False
        if base_snapshot is not None:
            candidate = (
                Calibrator()
                if retry_all
                else Calibrator.from_snapshot(base_snapshot)
            )
            if retry_all:
                candidate.set_method(self.calibration_mode)
            candidate.clear_points(points)
            self.calibrator = candidate
        else:
            self.calibrator.clear_points(points)
        return self.calibrator

    def start_keyboard(self) -> bool:
        """Create the emergency home screen and communication tracker."""
        if (
            not MOUSE_TEST_MODE
            and not self.presentation_mode
            and not self._calibration_use_allowed()
        ):
            print("Keyboard not started: calibration is not usable.")
            return False
        try:
            self.keyboard = KeyboardScreen(
                on_action=self.activate_key,
                language_manager=self.language_manager,
            )
            self.keyboard.protocol("WM_DELETE_WINDOW", self.shutdown)
            self.keyboard.bind("<Escape>", self.shutdown)
            self.keyboard.bind("<Control-l>", self.open_caregiver_language_selection)
            self.keyboard.bind("<Control-L>", self.open_caregiver_language_selection)
            if not config.PATIENT_MODE:
                self.keyboard.bind(
                    "<F1>", lambda _event: self.replay_current_instruction()
                )
            if self.presentation_mode:
                self.keyboard.bind("<F9>", self._restart_presentation)
                self.keyboard.bind("<F10>", self._force_presentation_recenter)
                self.keyboard.bind("<F8>", self._return_to_normal_mode)
            self.keyboard.update_idletasks()
            self.keyboard.show_home()
        except Exception as exc:
            print(f"Unable to start GazeLink interface: {exc}")
            self.logger.exception("unable to start communication interface")
            return False

        if not MOUSE_TEST_MODE:
            try:
                tracker = EyeTracker()
                if not self._ensure_camera_available(tracker, self.keyboard):
                    self._release_tracker(tracker)
                    self.shutdown()
                    return False
                self.eye_tracker = tracker
            except Exception as exc:
                print(f"Unable to start gaze tracking: {exc}")
                self.logger.exception("unable to start gaze tracking")
                self._show_error(
                    "GazeLink Camera Error",
                    "Gaze tracking could not be started.",
                    self.keyboard,
                )
                self.shutdown()
                return False

        self.smoother.reset()
        self.intent_tracker.clear()
        self.reset_dwell()
        self.invalid_started_at = None
        self.invalid_frame_count = 0
        self.tracking_lost = False
        self.camera_failure_prompted = False
        self.last_frame_time = time.monotonic()
        self.maximum_right_reach = 0.0
        self.update_suggestions()
        self._schedule_next_frame()
        if self.presentation_mode:
            self._begin_presentation_centering(
                self.language_manager.get_text("presentation_look_center")
            )
        else:
            self._announce_mode_once("emergency", delay_ms=1600)
        return True

    def start_presentation(self) -> bool:
        """Start the experimental relative-direction path without calibration."""
        self.logger.info("presentation mode started")
        print("[PRESENTATION] collecting neutral reference")
        return self.start_keyboard()

    def open_caregiver_language_selection(self, _event=None) -> str:
        """Open a mouse/keyboard-only language chooser without recalibration."""
        if not self._keyboard_exists() or self.caregiver_language_dialog_active:
            return "break"
        self.caregiver_language_dialog_active = True
        self.reset_dwell()
        self.intent_tracker.clear()
        self._enter_speech_context("caregiver-language-selection", "screen_changed")
        dialog = tk.Toplevel(self.keyboard)
        dialog.title("Caregiver language selection")
        dialog.configure(bg="#F5F7FA")
        dialog.attributes("-fullscreen", True)
        tk.Label(
            dialog,
            text="GazeLink",
            bg="#F5F7FA", fg="#1A1A2A",
            font=("Nirmala UI", 46, "bold"),
        ).pack(fill=tk.X, pady=(70, 12))
        tk.Label(
            dialog,
            text="Caregiver language selection",
            bg="#F5F7FA", fg="#526072",
            font=("Nirmala UI", 22, "bold"),
        ).pack(fill=tk.X, pady=(0, 28))
        choices = tk.Frame(dialog, bg="#F5F7FA")
        choices.pack(fill=tk.BOTH, expand=True, padx=70, pady=(0, 70))
        choices.grid_columnconfigure(0, weight=1)
        choices.grid_columnconfigure(1, weight=1)
        choices.grid_rowconfigure(0, weight=1)

        def choose(language: str) -> None:
            self._enter_speech_context("caregiver-language-change", "user_action")
            if not self.change_language(language, announce_selection=False):
                return
            self.caregiver_language_dialog_active = False
            dialog.destroy()
            self._finish_layout_transition(close_zoom=True)
            self.keyboard.show_home()
            self._announce_mode_once("emergency", delay_ms=350)

        for column, (label, language) in enumerate(
            (("ENGLISH", LANG_ENGLISH), ("ಕನ್ನಡ", LANG_KANNADA))
        ):
            tk.Button(
                choices,
                text=label,
                command=lambda value=language: choose(value),
                bg="#4A90D9", fg="white", activebackground="#357ABD",
                font=("Nirmala UI", 40, "bold"), bd=0,
            ).grid(row=0, column=column, sticky="nsew", padx=20, pady=20)
        dialog.bind("<e>", lambda _event: choose(LANG_ENGLISH))
        dialog.bind("<k>", lambda _event: choose(LANG_KANNADA))
        def cancel(_event=None) -> None:
            self.caregiver_language_dialog_active = False
            dialog.destroy()
            self._announce_mode_once(
                "keyboard" if self.keyboard.mode == KeyboardScreen.KEYBOARD else "emergency",
                delay_ms=200,
            )

        dialog.bind("<Escape>", cancel)
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.focus_force()
        return "break"

    def _announce_mode_once(self, mode: str, *, delay_ms: int = 0) -> None:
        """Speak guidance once for this navigation entry, never on redraw."""
        if not self._keyboard_exists():
            return
        language = self.language_manager.get_language()
        owner = self._enter_speech_context(f"communication-{mode}")
        self.mode_instruction_sequence += 1
        event = (
            f"mode-instruction:{mode}:{language}:"
            f"{self.mode_instruction_sequence}"
        )

        def announce() -> None:
            if self._keyboard_exists() and self.speech_owner == owner:
                self._speak_once(
                    event,
                    self.language_manager.get_instruction(mode, language),
                    language,
                )

        if delay_ms > 0:
            self.keyboard.after(delay_ms, announce)
        else:
            announce()

    def process_gaze_frame(self) -> None:
        """Process one non-blocking gaze or mouse-test update."""
        self.after_id = None
        if self.shutting_down or not self._keyboard_exists():
            return

        now = time.monotonic()
        delta = max(0.0, min(0.10, now - self.last_frame_time))
        self.last_frame_time = now
        if self.caregiver_language_dialog_active:
            self._schedule_next_frame()
            return

        try:
            if MOUSE_TEST_MODE:
                self._process_mouse_frame(now, delta)
            elif self.presentation_mode:
                self._process_presentation_frame(now, delta)
            else:
                self._process_eye_frame(now, delta)
        except Exception as exc:
            print(f"Gaze frame processing failed: {exc}")
            self.logger.exception("gaze frame processing failed")
            self.reset_dwell()
        finally:
            self._schedule_next_frame()

    def _process_mouse_frame(self, now: float, delta: float) -> None:
        # Mouse coordinates are permitted only in MOUSE_TEST_MODE.
        x = self.keyboard.winfo_pointerx() - self.keyboard.winfo_rootx()
        y = self.keyboard.winfo_pointery() - self.keyboard.winfo_rooty()
        width = max(1, self.keyboard.winfo_width())
        self.maximum_right_reach = max(
            self.maximum_right_reach,
            max(0.0, min(1.0, x / max(1, width - 1))),
        )

        self.invalid_started_at = None
        if config.HORIZONTAL_ZONE_DEBUG:
            self._suppress_selection_for_zone_debug()
            target = None
        else:
            target = self._process_valid_point(x, y, now, delta)
        self._set_target_reason(target, "READY")
        self.horizontal_debug = self._horizontal_debug_values(
            None,
            width,
            raw_mapped_x=x,
            gain_adjusted_x=x,
            smoothed_x=x,
        )
        self.model_debug = {
            "predicted_x": x,
            "predicted_y": y,
            "correction_x": 0.0,
            "correction_y": 0.0,
            "corrected_x": x,
            "corrected_y": y,
            "confidence": 1.0,
            "face_drift": 0.0,
            "head_drift": False,
            "features": None,
            "warning": "Mouse test mode",
            "blink": False,
            "gaze_valid": True,
            "dwell_reason": self.dwell_block_reason,
            "diagnostic_mode": config.DWELL_DIAGNOSTIC_MODE,
            "smoothed_x": x,
            "smoothed_y": y,
        }
        self.model_debug.update(
            self._intent_debug_fields(self.intent_tracker.last_result)
        )
        self.model_debug.update(self._resolution_debug_fields(now))
        self._update_debug(
            x,
            y,
            x,
            y,
            target,
            valid=True,
            invalid_duration=0.0,
        )

    def _begin_presentation_centering(self, message: str | None = None) -> None:
        """Pause selection and begin a fresh short neutral-reference capture."""
        self.presentation_state = "CENTERING"
        self.presentation_neutral = None
        self.presentation_samples.clear()
        self.presentation_delta_history.clear()
        self.presentation_started_at = time.monotonic()
        self.presentation_ready_until = None
        self.presentation_direction = "UNCERTAIN"
        self.presentation_drift_started_at = None
        self.presentation_invalid_started_at = None
        self.presentation_locked_direction = None
        self.presentation_leave_started_at = None
        self.selection_anchor = None
        self.reset_dwell()
        if self._keyboard_exists():
            self.keyboard.set_presentation_feedback("")
            self.keyboard.set_tracking_status(
                message
                or self.language_manager.get_text("presentation_look_center")
            )
        print("[PRESENTATION] collecting neutral reference")

    def _process_presentation_frame(self, now: float, delta: float) -> None:
        """Run relative eye-direction control without calibrated screen mapping."""
        _, ratios = self.eye_tracker.get_gaze_frame()
        debug = self.eye_tracker.get_debug_info()
        features = debug.get("feature_vector")
        valid = (
            ratios is not None
            and features is not None
            and bool(debug.get("valid"))
            and not bool(debug.get("blink"))
            and len(features) >= 9
            and all(math.isfinite(float(value)) for value in features[:9])
        )
        if not valid:
            self._handle_presentation_invalid(now, debug)
            return

        if self.presentation_invalid_started_at is not None:
            self.presentation_invalid_started_at = None
            self._set_tracking_warning("")

        values = tuple(float(value) for value in features[:9])
        if self.presentation_state == "READY":
            if (
                self.presentation_ready_until is not None
                and now >= self.presentation_ready_until
            ):
                self.presentation_state = "ACTIVE"
                self.keyboard.set_presentation_feedback("")
            return
        if self.presentation_state != "ACTIVE":
            self._collect_presentation_neutral(now, values)
            return

        neutral = self.presentation_neutral
        if neutral is None:
            self._begin_presentation_centering()
            return

        face_shift = math.hypot(
            values[6] - neutral["face_x"],
            values[7] - neutral["face_y"],
        )
        distance_change = abs(values[8] - neutral["eye_distance"]) / max(
            abs(neutral["eye_distance"]), 1e-6
        )
        recenter_needed = (
            face_shift > config.PRESENTATION_RECENTER_FACE_SHIFT
            or distance_change > config.PRESENTATION_RECENTER_DISTANCE_CHANGE
        )
        if recenter_needed:
            if self.presentation_drift_started_at is None:
                self.presentation_drift_started_at = now
            elif (
                now - self.presentation_drift_started_at
                >= config.PRESENTATION_RECENTER_HOLD_SECONDS
            ):
                print("[PRESENTATION] recenter required")
                self._begin_presentation_centering(
                    self.language_manager.get_text("presentation_recenter")
                )
            return
        self.presentation_drift_started_at = None

        dx = (
            (values[4] - neutral["horizontal"])
            * config.PRESENTATION_HORIZONTAL_POLARITY
        )
        dy = (
            (values[5] - neutral["vertical"])
            * config.PRESENTATION_VERTICAL_POLARITY
        )
        self.presentation_delta_history.append((dx, dy))
        smooth_dx = statistics.median(
            item[0] for item in self.presentation_delta_history
        )
        smooth_dy = statistics.median(
            item[1] for item in self.presentation_delta_history
        )
        direction = self._classify_presentation_direction(smooth_dx, smooth_dy)
        self.presentation_direction = direction
        if direction != self.presentation_last_logged_direction:
            print(f"[PRESENTATION] direction={direction}")
            self.presentation_last_logged_direction = direction

        target, virtual_point = self._presentation_target_for_direction(
            direction, smooth_dx, smooth_dy
        )
        feedback = self.language_manager.get_text(
            f"presentation_direction_{direction.lower()}"
        )
        if target:
            feedback += f"  •  {self._presentation_target_label(target)}"
            print_target = target.removeprefix(self.EMERGENCY_PREFIX)
            if self.last_target_resolution.get("final_target") != target:
                print(f"[PRESENTATION] target={print_target}")
        self.keyboard.set_tracking_status("")
        self.keyboard.set_presentation_feedback(feedback)
        self.last_target_resolution = {
            "hit_test": "PRESENTATION",
            "direct_target": None,
            "final_target": target,
        }
        self._process_presentation_dwell(
            target, direction, virtual_point, now, delta
        )

    def _collect_presentation_neutral(
        self, now: float, features: tuple[float, ...]
    ) -> None:
        """Accept a robust neutral reference after a bounded stable capture."""
        if self.presentation_started_at is None:
            self.presentation_started_at = now
        self.presentation_samples.append(features)
        elapsed = now - self.presentation_started_at
        if elapsed < config.PRESENTATION_NEUTRAL_SECONDS:
            return

        samples = self.presentation_samples
        enough = len(samples) >= config.PRESENTATION_NEUTRAL_MIN_SAMPLES
        horizontal_values = [sample[4] for sample in samples]
        vertical_values = [sample[5] for sample in samples]
        h_median = statistics.median(horizontal_values) if samples else 0.0
        v_median = statistics.median(vertical_values) if samples else 0.0
        spread = max(
            statistics.median(abs(value - h_median) for value in horizontal_values)
            if samples else float("inf"),
            statistics.median(abs(value - v_median) for value in vertical_values)
            if samples else float("inf"),
        )
        if not enough or spread > config.PRESENTATION_NEUTRAL_MAX_SPREAD:
            self.presentation_samples.clear()
            self.presentation_started_at = now
            self.keyboard.set_tracking_status(
                self.language_manager.get_text("presentation_face_center")
            )
            return

        self.presentation_neutral = {
            "horizontal": h_median,
            "vertical": v_median,
            "face_x": statistics.median(sample[6] for sample in samples),
            "face_y": statistics.median(sample[7] for sample in samples),
            "eye_distance": statistics.median(sample[8] for sample in samples),
        }
        self.presentation_state = "READY"
        self.presentation_ready_until = now + config.PRESENTATION_READY_SECONDS
        self.presentation_samples.clear()
        self.presentation_delta_history.clear()
        self.keyboard.set_tracking_status("")
        ready = " • ".join(
            (
                self.language_manager.get_text("presentation_face_detected"),
                self.language_manager.get_text("presentation_eyes_detected"),
                self.language_manager.get_text("presentation_ready"),
            )
        )
        self.keyboard.set_presentation_feedback(ready)
        print("[PRESENTATION] face detected")
        print("[PRESENTATION] neutral ready")

    @staticmethod
    def _classify_presentation_direction(dx: float, dy: float) -> str:
        """Classify a smoothed neutral-relative delta, rejecting grey zones."""
        def axis(value: float, threshold: float) -> int | None:
            magnitude = abs(value)
            if magnitude <= config.PRESENTATION_CENTER_DEADZONE:
                return 0
            if magnitude < threshold:
                return None
            return 1 if value > 0 else -1

        horizontal = axis(dx, config.PRESENTATION_HORIZONTAL_THRESHOLD)
        vertical = axis(dy, config.PRESENTATION_VERTICAL_THRESHOLD)
        if horizontal is None or vertical is None:
            return "UNCERTAIN"
        names = {
            (-1, -1): "UP_LEFT",
            (0, -1): "UP",
            (1, -1): "UP_RIGHT",
            (-1, 0): "LEFT",
            (0, 0): "CENTER",
            (1, 0): "RIGHT",
            (-1, 1): "DOWN_LEFT",
            (0, 1): "DOWN",
            (1, 1): "DOWN_RIGHT",
        }
        return names[(horizontal, vertical)]

    def _presentation_target_for_direction(
        self, direction: str, dx: float, dy: float
    ) -> tuple[str | None, tuple[float, float]]:
        """Resolve large logical zones; never use calibrated screen pixels."""
        width = max(1, self.keyboard.winfo_width())
        height = max(1, self.keyboard.winfo_height())
        scale_x = config.PRESENTATION_HORIZONTAL_THRESHOLD * 1.5
        scale_y = config.PRESENTATION_VERTICAL_THRESHOLD * 1.5
        virtual_x = width * (
            0.5 + 0.48 * max(-1.0, min(1.0, dx / scale_x))
        )
        virtual_y = height * (
            0.5 + 0.48 * max(-1.0, min(1.0, dy / scale_y))
        )
        point = (virtual_x, virtual_y)
        if direction == "UNCERTAIN":
            return None, point

        if self.keyboard.mode == KeyboardScreen.EMERGENCY_HOME:
            mapping = {
                "UP_LEFT": "EMERGENCY:HELP",
                "UP_RIGHT": "EMERGENCY:WATER",
                "LEFT": "EMERGENCY:FOOD",
                "RIGHT": "EMERGENCY:TOILET",
                "DOWN_LEFT": "EMERGENCY:PAIN",
                "DOWN_RIGHT": "CLEAR",
                "DOWN": "TYPE",
            }
            return mapping.get(direction), point

        targets = self.keyboard.get_intent_targets()
        # The English overview is intentionally categorical: a normal-sized
        # directional movement chooses a broad QWERTY group, never a tiny key.
        # A deliberately stronger downward movement remains available for the
        # existing SPACE/BACKSPACE/CLEAR/SPEAK/HOME control area.
        region_mapping = {
            "UP_LEFT": "REGION:UPPER_LEFT",
            "UP": "REGION:UPPER_MIDDLE",
            "UP_RIGHT": "REGION:UPPER_RIGHT",
            "LEFT": "REGION:MIDDLE_LEFT",
            "CENTER": "REGION:MIDDLE_MIDDLE",
            "RIGHT": "REGION:MIDDLE_RIGHT",
            "DOWN_LEFT": "REGION:LOWER_LEFT",
            "DOWN_RIGHT": "REGION:LOWER_RIGHT",
        }
        region_target = region_mapping.get(direction)
        if (
            region_target in targets
            and abs(dy) < config.PRESENTATION_VERTICAL_THRESHOLD * 2.0
        ):
            return region_target, point

        scores: list[tuple[float, str]] = []
        for name, target in targets.items():
            x1, y1, x2, y2 = target["bounds"]
            center_x, center_y = target["center"]
            radius_x = max(
                1.0,
                (x2 - x1) * 0.5 * config.PRESENTATION_TARGET_RADIUS_SCALE,
            )
            radius_y = max(
                1.0,
                (y2 - y1) * 0.5 * config.PRESENTATION_TARGET_RADIUS_SCALE,
            )
            normalized = math.hypot(
                (virtual_x - center_x) / radius_x,
                (virtual_y - center_y) / radius_y,
            )
            scores.append((max(0.0, 1.0 - normalized), name))
        scores.sort(reverse=True)
        if not scores or scores[0][0] < config.PRESENTATION_TARGET_MIN_SCORE:
            return None, point
        second = scores[1][0] if len(scores) > 1 else 0.0
        if scores[0][0] - second < config.PRESENTATION_TARGET_WIN_MARGIN:
            if self.current_key in {item[1] for item in scores[:2]}:
                return self.current_key, point
            return None, point
        return scores[0][1], point

    def _process_presentation_dwell(
        self,
        target: str | None,
        direction: str,
        point: tuple[float, float],
        now: float,
        delta: float,
    ) -> None:
        """Apply presentation-only acquisition/dwell and leave-to-rearm lock."""
        self.last_control_point = point
        self.selection_anchor = None
        if self.presentation_locked_direction is not None:
            if direction == self.presentation_locked_direction:
                return
            self.presentation_locked_direction = None
            self.reset_dwell()

        if target is None:
            if self.presentation_leave_started_at is None:
                self.presentation_leave_started_at = now
            elif (
                now - self.presentation_leave_started_at
                >= config.PRESENTATION_DIRECTION_LOSS_SECONDS
            ):
                self.reset_dwell()
            return
        self.presentation_leave_started_at = None

        if self.current_key != target:
            if self.candidate_key != target:
                self.reset_dwell()
                self.candidate_key = target
                self.candidate_started_at = now
                self.candidate_source = "PRESENTATION"
                self.keyboard.highlight_key(target)
                return
            if (
                self.candidate_started_at is None
                or now - self.candidate_started_at
                < config.PRESENTATION_ACQUIRE_TIME
            ):
                return
            self.current_key = target
            self.candidate_key = None
            self.candidate_started_at = None
            self.dwell_progress = 0.0
            self.selection_locked = False
            self.keyboard.start_dwell(target)
            print("[PRESENTATION] dwell started")

        if target != self.current_key or self.selection_locked:
            return
        self.dwell_progress = min(
            1.0,
            self.dwell_progress + delta / config.PRESENTATION_DWELL_TIME,
        )
        self.keyboard.set_dwell_progress(target, self.dwell_progress)
        if self.dwell_progress >= 1.0:
            selected = target.removeprefix(self.EMERGENCY_PREFIX)
            print(f"[PRESENTATION] selected={selected}")
            selected_direction = direction
            self.activate_key(target)
            self.presentation_locked_direction = selected_direction

    def _handle_presentation_invalid(
        self, now: float, debug: dict[str, Any]
    ) -> None:
        """Pause on blink/invalid gaze and clear only after sustained loss."""
        if self.presentation_invalid_started_at is None:
            self.presentation_invalid_started_at = now
        elapsed = now - self.presentation_invalid_started_at
        if elapsed > config.BLINK_GRACE_SECONDS:
            self.reset_dwell()
            self.presentation_delta_history.clear()
        if (
            self.presentation_state == "CENTERING"
            and elapsed >= config.PRESENTATION_NEUTRAL_LOSS_RESET_SECONDS
        ):
            self.presentation_samples.clear()
            self.presentation_started_at = now
        if elapsed >= config.PRESENTATION_INVALID_WARNING_SECONDS:
            self._set_tracking_warning(
                self.language_manager.get_text("presentation_eyes_unclear")
            )
        if (
            self.eye_tracker is not None
            and self.eye_tracker.consecutive_read_failures
            >= config.CAMERA_READ_FAILURE_LIMIT
            and not self.camera_failure_prompted
        ):
            self.camera_failure_prompted = True
            self.keyboard.after_idle(self._prompt_runtime_camera_retry)

    def _presentation_target_label(self, target: str) -> str:
        item = self.keyboard.targets.get(target, {})
        return str(item.get("label") or target.removeprefix(self.EMERGENCY_PREFIX))

    def _restart_presentation(self, _event=None) -> str:
        print("[PRESENTATION] restart requested")
        self.keyboard.show_home()
        self._begin_presentation_centering()
        return "break"

    def _force_presentation_recenter(self, _event=None) -> str:
        print("[PRESENTATION] recenter required")
        self._begin_presentation_centering(
            self.language_manager.get_text("presentation_recenter")
        )
        return "break"

    def _return_to_normal_mode(self, _event=None) -> str:
        """Leave the experimental root so run() can enter normal startup."""
        self._enter_speech_context("normal-mode", "screen_changed")
        self.presentation_return_to_normal = True
        self.presentation_mode = False
        if self.after_id is not None and self._keyboard_exists():
            try:
                self.keyboard.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        self._release_tracker(self.eye_tracker)
        self.eye_tracker = None
        if self._keyboard_exists():
            self.keyboard.destroy()
        self.keyboard = None
        return "break"

    def _process_eye_frame(self, now: float, delta: float) -> None:
        _, raw_ratios = self.eye_tracker.get_gaze_frame()
        debug = self.eye_tracker.get_debug_info()
        features = debug.get("feature_vector")
        if raw_ratios is None or features is None:
            self._handle_invalid_gaze(now, debug)
            return

        self._set_tracking_warning("")
        self.camera_failure_prompted = False

        width = max(1, self.keyboard.winfo_width())
        height = max(1, self.keyboard.winfo_height())
        raw_horizontal, raw_vertical = raw_ratios
        mapping = self.calibrator.predict_with_debug(
            features, width, height, recent_stability=True
        )
        corrected_x = float(mapping["corrected_x"])
        corrected_y = float(mapping["corrected_y"])
        smoothed_x, smoothed_y = self.smoother.update_pair(
            corrected_x, corrected_y
        )
        resolution = self._resolve_live_target(smoothed_x, smoothed_y, now)
        intent_result = resolution["intent_result"]
        self.maximum_right_reach = max(
            self.maximum_right_reach,
            corrected_x / max(1, width - 1),
        )
        confidence = self.calibrator.calculate_confidence(
            features, self.smoother.is_pair_stable()
        )
        mapping.update(confidence)
        mapping["features"] = tuple(features)
        mapping["blink"] = bool(debug.get("blink"))
        mapping["gaze_valid"] = bool(debug.get("valid"))
        mapping["smoothed_x"] = smoothed_x
        mapping["smoothed_y"] = smoothed_y
        mapping["diagnostic_mode"] = config.DWELL_DIAGNOSTIC_MODE
        mapping.update(self._intent_debug_fields(intent_result))
        mapping.update(self._resolution_debug_fields(now, resolution))
        self.model_debug = mapping

        confidence_value = float(confidence["confidence"])
        hard_face_drift = bool(confidence.get("head_drift"))
        if config.DWELL_DIAGNOSTIC_MODE:
            dwell_rate = 1.0
            readiness = "READY"
        elif hard_face_drift:
            self.dwell_block_reason = "FACE_DRIFT"
            self._handle_low_confidence(
                now, debug, mapping, smoothed_x, smoothed_y
            )
            return
        elif (
            confidence_value < config.DIRECT_MIN_CONFIDENCE
            or (
                confidence_value < config.CONFIDENCE_MEDIUM_THRESHOLD
                and resolution["hit_test"] not in {"DIRECT", "LOCK"}
            )
        ):
            self.dwell_block_reason = "LOW_CONFIDENCE"
            self._handle_low_confidence(
                now, debug, mapping, smoothed_x, smoothed_y
            )
            return
        elif confidence_value < config.CONFIDENCE_HIGH_THRESHOLD:
            dwell_rate = (
                config.DIRECT_LOW_CONFIDENCE_DWELL_RATE
                if confidence_value < config.CONFIDENCE_MEDIUM_THRESHOLD
                else config.MEDIUM_CONFIDENCE_DWELL_RATE
            )
            readiness = (
                "FACE_DRIFT"
                if confidence.get("face_drift_warning")
                else "LOW_CONFIDENCE"
            )
            mapping["warning"] = (
                "Minor head movement - dwell slowed"
                if confidence.get("face_drift_warning")
                else "Medium confidence - dwell slowed"
            )
        else:
            dwell_rate = 1.0
            readiness = "READY"
            mapping["warning"] = ""

        self.invalid_started_at = None
        self.invalid_frame_count = 0

        if config.HORIZONTAL_ZONE_DEBUG:
            self._suppress_selection_for_zone_debug()
            target = None
        else:
            target = self._process_intent_target(
                resolution.get("final_target"),
                smoothed_x,
                smoothed_y,
                now,
                delta,
                dwell_rate,
                str(resolution.get("hit_test", "INTENT")),
            )
        self._set_target_reason(target, readiness)
        mapping["dwell_reason"] = self.dwell_block_reason
        mapping.update(self._resolution_debug_fields(now, resolution))

        self.horizontal_debug = self._horizontal_debug_values(
            raw_horizontal,
            width,
            raw_mapped_x=float(mapping["predicted_x"]),
            gain_adjusted_x=corrected_x,
            smoothed_x=smoothed_x,
        )

        self._update_debug(
            float(mapping["predicted_x"]),
            float(mapping["predicted_y"]),
            smoothed_x,
            smoothed_y,
            target,
            valid=bool(debug.get("valid")),
            invalid_duration=0.0,
        )

    def _suppress_selection_for_zone_debug(self) -> None:
        """Keep the five-zone diagnostic read-only."""
        if self.current_key is not None or self.candidate_key is not None:
            self.reset_dwell(preserve_selection_anchor=True)

    def _horizontal_debug_values(
        self,
        ratio: float | None,
        canvas_width: int,
        *,
        raw_mapped_x: float | None,
        gain_adjusted_x: float | None,
        smoothed_x: float | None,
    ) -> dict[str, Any]:
        return {
            "calibrated_left": self.calibrator.calibrated_left,
            "calibrated_center": self.calibrator.center_horizontal,
            "calibrated_right": self.calibrator.calibrated_right,
            "current_ratio": ratio,
            "raw_mapped_x": raw_mapped_x,
            "gain_adjusted_x": gain_adjusted_x,
            "smoothed_x": smoothed_x,
            "canvas_width": canvas_width,
            "coordinate_system": "LOCAL",
        }

    def _set_target_reason(
        self, target: str | None, readiness: str
    ) -> None:
        """Expose the target-identity dwell state for live diagnostics."""
        if self.selection_locked:
            self.dwell_block_reason = "LOCKED"
        elif target is None:
            self.dwell_block_reason = "NO_TARGET"
        elif self.target_loss_started_at is not None:
            self.dwell_block_reason = "UNSTABLE"
        elif self.current_key is None:
            self.dwell_block_reason = "UNSTABLE"
        else:
            self.dwell_block_reason = readiness

    def _update_intent(
        self, x: float, y: float, now: float
    ) -> dict[str, object]:
        """Add one valid point and resolve intent over visible targets."""
        self.intent_tracker.add_point(x, y, now)
        targets = self.keyboard.get_intent_targets()
        minimum_score = (
            config.INTENT_EMERGENCY_MIN_SCORE
            if self.keyboard.mode == self.keyboard.EMERGENCY_HOME
            else config.INTENT_MIN_SCORE
        )
        result = self.intent_tracker.resolve(
            targets, now, minimum_score=minimum_score
        )
        self._trace_intent(result, now)
        return result

    def _resolve_live_target(
        self, x: float, y: float, now: float
    ) -> dict[str, object]:
        """Resolve containment first, hysteresis second, then intent fallback."""
        direct_target = self.keyboard.get_direct_target_at_position(x, y)

        # An acquired target owns its larger exit boundary. This prevents a
        # boundary wobble from switching to a newly contained neighbour.
        if (
            self.current_key is not None
            and self.keyboard.is_within_exit_bounds(self.current_key, x, y)
        ):
            self.intent_tracker.add_point(x, y, now)
            intent_result = self._resolve_intent_scores(now)
            final_target = self.current_key
            hit_test = "LOCK"
        elif direct_target is not None:
            # Direct containment is authoritative. Incompatible old samples
            # are discarded so they cannot delay acquisition of this target.
            self.intent_tracker.prioritize_target(direct_target, x, y, now)
            intent_result = self._resolve_intent_scores(now)
            final_target = direct_target
            hit_test = "DIRECT"
        else:
            intent_result = self._update_intent(x, y, now)
            final_target = intent_result.get("target")
            hit_test = "INTENT" if final_target is not None else "NONE"

        result = {
            "hit_test": hit_test,
            "direct_target": direct_target,
            "final_target": final_target,
            "intent_result": intent_result,
        }
        self.last_target_resolution = result
        return result

    def _resolve_intent_scores(self, now: float) -> dict[str, object]:
        """Score current history without adding another gaze sample."""
        minimum_score = (
            config.INTENT_EMERGENCY_MIN_SCORE
            if self.keyboard.mode == self.keyboard.EMERGENCY_HOME
            else config.INTENT_MIN_SCORE
        )
        result = self.intent_tracker.resolve(
            self.keyboard.get_intent_targets(),
            now,
            minimum_score=minimum_score,
        )
        self._trace_intent(result, now)
        return result

    def _resolution_debug_fields(
        self,
        now: float,
        resolution: dict[str, object] | None = None,
    ) -> dict[str, object]:
        resolution = resolution or self.last_target_resolution
        acquiring_elapsed = 0.0
        if self.candidate_started_at is not None:
            acquiring_elapsed = max(0.0, now - self.candidate_started_at)
        acquiring_progress = (
            min(1.0, acquiring_elapsed / self.candidate_acquire_time)
            if self.candidate_key is not None
            and self.candidate_acquire_time > 0.0
            else 0.0
        )
        final_target = (
            self.current_key
            or self.candidate_key
            or resolution.get("final_target")
        )
        region_name = None
        if final_target and str(final_target).startswith("REGION:"):
            region_name = str(final_target).removeprefix("REGION:")
        elif self.keyboard.zoom_region is not None:
            region_name = self.keyboard.zoom_region
        letters = (
            " ".join(self.keyboard.ZOOM_REGIONS[region_name])
            if region_name in self.keyboard.ZOOM_REGIONS
            else ""
        )
        ui_mode = self.keyboard.mode
        if self.keyboard.mode == self.keyboard.KEYBOARD:
            ui_mode = (
                "LETTER_ZOOM"
                if self.keyboard.is_zoom_active()
                else "KEYBOARD_OVERVIEW"
            )
        return {
            "hit_test": resolution.get("hit_test", "NONE"),
            "direct_target": resolution.get("direct_target"),
            "final_target": final_target,
            "acquiring_elapsed": acquiring_elapsed,
            "acquiring_required": self.candidate_acquire_time,
            "acquiring_progress": acquiring_progress,
            "ui_mode": ui_mode,
            "region_name": region_name,
            "region_letters": letters,
            "region_progress": (
                self.dwell_progress
                if self.current_key is not None
                and self.current_key.startswith("REGION:")
                else 0.0
            ),
        }

    @staticmethod
    def _intent_debug_fields(result: dict[str, object]) -> dict[str, object]:
        return {
            "intent_target": result.get("target"),
            "intent_score": result.get("score", 0.0),
            "intent_second_target": result.get("second_target"),
            "intent_second_score": result.get("second_score", 0.0),
            "intent_margin": result.get("margin", 0.0),
            "intent_history_samples": result.get("history_samples", 0),
            "intent_state": result.get("state", "EMPTY"),
            "intent_top_scores": result.get("top_scores", []),
        }

    def _trace_intent(
        self, result: dict[str, object], now: float
    ) -> None:
        if not config.INTENT_DEBUG:
            return
        signature = (
            result.get("target"),
            result.get("raw_winner"),
            result.get("state"),
        )
        changed = signature != self.last_intent_trace_signature
        if (
            not changed
            and now - self.last_intent_trace_time
            < config.INTENT_TRACE_INTERVAL_SECONDS
        ):
            return
        score_text = " ".join(
            f"{name}={score:.2f}"
            for name, score in result.get("top_scores", [])
        )
        target = result.get("target") or "ambiguous"
        state = result.get("state", "")
        print(f"Intent: {score_text} -> {target} ({state})")
        self.last_intent_trace_signature = signature
        self.last_intent_trace_time = now

    def _process_valid_point(
        self,
        x: float,
        y: float,
        now: float,
        delta: float,
        dwell_rate: float = 1.0,
    ) -> str | None:
        """Resolve a visible point and advance its target-level dwell state."""
        resolution = self._resolve_live_target(x, y, now)
        return self._process_intent_target(
            resolution.get("final_target"),
            x,
            y,
            now,
            delta,
            dwell_rate,
            str(resolution.get("hit_test", "INTENT")),
        )

    def _process_intent_target(
        self,
        target: str | None,
        x: float,
        y: float,
        now: float,
        delta: float,
        dwell_rate: float = 1.0,
        target_source: str = "INTENT",
    ) -> str | None:
        """Acquire and dwell on the containment/hysteresis/intent target."""
        self.last_control_point = (x, y)
        if self.selection_anchor is not None:
            distance = math.hypot(
                x - self.selection_anchor[0],
                y - self.selection_anchor[1],
            )
            if distance <= config.STICKY_LOCK_EXIT_DISTANCE:
                return None
            self.selection_anchor = None

        if self.current_key is not None:
            if target == self.current_key:
                self.target_loss_started_at = None
                if self.selection_locked:
                    return target
                if target.startswith(("REGION:", "KREGION:")):
                    self._advance_region_zoom(target, delta, dwell_rate)
                else:
                    self._advance_dwell(target, delta, dwell_rate)
                return target

            if target is None:
                self._decay_target_progress(now, delta)
                return self.current_key

            # The intent resolver has held a clearly stronger challenger long
            # enough, so it is safe to move the dwell target.
            self.reset_dwell()
            self._start_candidate(target, now, target_source)
            return target

        if target is None:
            self._decay_target_progress(now, delta)
            return self.candidate_key

        self.target_loss_started_at = None
        if target != self.candidate_key:
            self._start_candidate(target, now, target_source)
            return target

        if (
            target_source == "DIRECT"
            and self.candidate_source != "DIRECT"
        ):
            self.candidate_source = "DIRECT"
            self.candidate_acquire_time = config.DIRECT_TARGET_ACQUIRE_TIME

        if (
            self.candidate_started_at is not None
            and now - self.candidate_started_at
            >= self.candidate_acquire_time
        ):
            self._lock_candidate(target)
        return target

    def _advance_region_zoom(
        self, target: str, delta: float, dwell_rate: float
    ) -> None:
        """Open a local letter panel after sustained broad-region focus."""
        self.dwell_progress = min(
            1.0,
            self.dwell_progress
            + delta * dwell_rate / config.ZOOM_ACTIVATION_TIME,
        )
        self.keyboard.set_dwell_progress(target, self.dwell_progress)
        if self.dwell_progress < 1.0:
            return

        if target.startswith("KREGION:"):
            region_name = target.removeprefix("KREGION:")
            opener = self.keyboard.open_kannada_group
        else:
            region_name = target.removeprefix("REGION:")
            opener = self.keyboard.open_zoom_region
        self.reset_dwell(preserve_selection_anchor=True)
        self.intent_tracker.clear()
        opener(region_name)
        self.target_loss_started_at = None

    def _start_candidate(
        self, key: str, now: float, source: str = "INTENT"
    ) -> None:
        """Preview a target without starting selection dwell yet."""
        self.candidate_key = key
        self.candidate_started_at = now
        self.candidate_source = source
        self.candidate_acquire_time = (
            config.DIRECT_TARGET_ACQUIRE_TIME
            if source == "DIRECT"
            else config.TARGET_ACQUIRE_TIME
        )
        self.target_loss_started_at = None
        self.keyboard.highlight_key(key)

    def _lock_candidate(self, key: str) -> None:
        """Convert a steady candidate into a sticky dwell target."""
        focus_started_at = self.candidate_started_at or time.monotonic()
        acquired_for = self.candidate_acquire_time
        self.current_key = key
        self.focus_started_at = focus_started_at
        self.candidate_key = None
        self.candidate_started_at = None
        self.candidate_source = "NONE"
        self.dwell_progress = 0.0
        self.selection_locked = False
        self.keyboard.start_dwell(key)
        if key.startswith(("REGION:", "KREGION:")):
            # Region focus already remained steady during acquisition, so that
            # time counts toward the configured automatic-zoom delay.
            self.dwell_progress = min(
                0.95,
                acquired_for / config.ZOOM_ACTIVATION_TIME,
            )
            self.keyboard.set_dwell_progress(key, self.dwell_progress)

    def _advance_dwell(
        self, target: str, delta: float, dwell_rate: float = 1.0
    ) -> None:
        self.dwell_progress = min(
            1.0,
            self.dwell_progress
            + (delta * dwell_rate / config.DWELL_TIME),
        )
        self.keyboard.set_dwell_progress(target, self.dwell_progress)
        if self.dwell_progress >= 1.0:
            self.activate_key(target)

    def _decay_target_progress(self, now: float, delta: float) -> None:
        """Retain brief losses while decaying progress, then clear the target."""
        if self.target_loss_started_at is None:
            self.target_loss_started_at = now
        loss_duration = now - self.target_loss_started_at

        if (
            self.keyboard.is_zoom_active()
            and loss_duration >= config.ZOOM_CANCEL_SECONDS
        ):
            self.reset_dwell(preserve_selection_anchor=True)
            self.intent_tracker.clear()
            self.keyboard.close_zoom()
            return

        if self.current_key is None:
            if self.candidate_key is None:
                return
            if (
                loss_duration >= config.TARGET_LOSS_RESET_SECONDS
            ):
                self._clear_candidate()
            return
        if self.selection_locked:
            if loss_duration >= config.TARGET_LOSS_RESET_SECONDS:
                self.reset_dwell()
            return

        self.dwell_progress = max(
            0.0,
            self.dwell_progress - config.DWELL_DECAY_RATE * delta,
        )
        self.keyboard.set_dwell_progress(
            self.current_key,
            self.dwell_progress,
        )
        if loss_duration >= config.TARGET_LOSS_RESET_SECONDS:
            self.reset_dwell()

    def _handle_invalid_gaze(
        self,
        now: float,
        debug: dict[str, Any],
    ) -> None:
        """Pause through a short blink, then clear stale gaze and dwell."""
        self.invalid_frame_count += 1
        if self.invalid_started_at is None:
            self.invalid_started_at = now
        invalid_duration = now - self.invalid_started_at
        sustained_loss = invalid_duration > config.BLINK_GRACE_SECONDS
        if sustained_loss:
            self.intent_tracker.clear()
        width = max(1, self.keyboard.winfo_width())
        current_ratio = debug.get("horizontal_ratio")
        raw_mapped_x = None
        gain_adjusted_x = None
        self.model_debug = {
            "features": debug.get("feature_vector"),
            "confidence": 0.0,
            "face_drift": None,
            "head_drift": False,
            "warning": "Gaze unavailable",
            "blink": bool(debug.get("blink")),
            "gaze_valid": False,
            "dwell_reason": (
                "BLINK" if debug.get("blink") else "NO_TARGET"
            ),
            "diagnostic_mode": config.DWELL_DIAGNOSTIC_MODE,
        }
        self.model_debug.update(
            self._intent_debug_fields(self.intent_tracker.last_result)
        )
        self.model_debug["intent_state"] = (
            "BLINK" if debug.get("blink") and not sustained_loss else "LOST"
        )
        self.dwell_block_reason = self.model_debug["dwell_reason"]
        self.horizontal_debug = self._horizontal_debug_values(
            current_ratio,
            width,
            raw_mapped_x=raw_mapped_x,
            gain_adjusted_x=gain_adjusted_x,
            smoothed_x=None,
        )

        if sustained_loss:
            self.reset_dwell(preserve_selection_anchor=True)
            if debug.get("camera_error"):
                self._set_tracking_warning(
                    self.language_manager.get_text("camera_unavailable")
                )
                if (
                    int(debug.get("read_failure_count", 0))
                    >= config.CAMERA_READ_FAILURE_LIMIT
                    and not self.camera_failure_prompted
                ):
                    self.camera_failure_prompted = True
                    self.keyboard.after_idle(self._prompt_runtime_camera_retry)
            elif not debug.get("blink"):
                self._set_tracking_warning(
                    self.language_manager.get_text("face_not_detected")
                )
            if self.invalid_frame_count >= config.GAZE_INVALID_RESET_FRAMES:
                self.smoother.reset()
        if (
            self.keyboard.is_zoom_active()
            and invalid_duration >= config.ZOOM_CANCEL_SECONDS
        ):
            self.intent_tracker.clear()
            self.keyboard.close_zoom()

        self._update_debug(
            None,
            None,
            None,
            None,
            self.current_key or self.candidate_key,
            valid=False,
            invalid_duration=invalid_duration,
        )

    def _handle_low_confidence(
        self,
        now: float,
        debug: dict[str, Any],
        mapping: dict[str, Any],
        smoothed_x: float,
        smoothed_y: float,
    ) -> None:
        """Pause low-confidence dwell without destabilizing the smoother."""
        if self.invalid_started_at is None:
            self.invalid_started_at = now
        invalid_duration = now - self.invalid_started_at
        warning = (
            "Please return to calibration position"
            if mapping.get("head_drift")
            else "Low gaze confidence - hold still"
        )
        mapping["warning"] = warning
        mapping["dwell_reason"] = self.dwell_block_reason
        self.model_debug = mapping
        if invalid_duration > config.TARGET_LOSS_RESET_SECONDS:
            self.reset_dwell(preserve_selection_anchor=True)
        width = max(1, self.keyboard.winfo_width())
        self.horizontal_debug = self._horizontal_debug_values(
            debug.get("horizontal_ratio"), width,
            raw_mapped_x=mapping.get("predicted_x"),
            gain_adjusted_x=mapping.get("corrected_x"),
            smoothed_x=smoothed_x,
        )
        self._update_debug(
            mapping.get("predicted_x"), mapping.get("predicted_y"),
            smoothed_x, smoothed_y,
            self.current_key or self.candidate_key,
            valid=True,
            invalid_duration=invalid_duration,
        )

    def change_focused_key(self, key: str) -> None:
        """Compatibility helper that immediately locks a target."""
        self.reset_dwell()
        self.current_key = key
        self.focus_started_at = time.monotonic()
        self.dwell_progress = 0.0
        self.selection_locked = False
        self.target_loss_started_at = None
        self.keyboard.start_dwell(key)

    def _clear_candidate(self) -> None:
        self.candidate_key = None
        self.candidate_started_at = None
        self.candidate_source = "NONE"
        self.candidate_acquire_time = config.TARGET_ACQUIRE_TIME
        self.target_loss_started_at = None
        if self._keyboard_exists():
            self.keyboard.clear_highlight()

    def reset_dwell(self, preserve_selection_anchor: bool = False) -> None:
        """Clear target, progress, decay state, and selection lock."""
        if self._keyboard_exists():
            try:
                self.keyboard.stop_dwell()
                self.keyboard.clear_highlight()
                self.keyboard.restore_key_sizes()
            except tk.TclError:
                pass
        self.candidate_key = None
        self.candidate_started_at = None
        self.candidate_source = "NONE"
        self.candidate_acquire_time = config.TARGET_ACQUIRE_TIME
        self.current_key = None
        self.focus_started_at = None
        self.dwell_progress = 0.0
        self.selection_locked = False
        self.target_loss_started_at = None
        if not preserve_selection_anchor:
            self.selection_anchor = None

    def activate_key(self, key: str) -> bool:
        """Handle every mode, communication, and editing action."""
        if self.shutting_down:
            return True
        if self.selection_locked and key == self.current_key:
            return True
        # Every deliberate selection outranks guidance and invalidates even a
        # delayed instruction callback that has not reached the TTS worker yet.
        self._enter_speech_context("user-action", "user_action")
        self.selection_locked = True
        self.keyboard.stop_dwell()
        self.dwell_progress = 0.0

        if key.startswith(self.EMERGENCY_PREFIX):
            self._activate_emergency(key.removeprefix(self.EMERGENCY_PREFIX))
        elif key.startswith(self.SUGGESTION_PREFIX):
            self._activate_suggestion(key)
            self._finish_layout_transition()
        elif key.startswith("REGION:"):
            region_name = key.removeprefix("REGION:")
            self.reset_dwell(preserve_selection_anchor=True)
            self.intent_tracker.clear()
            self.keyboard.open_zoom_region(region_name)
        elif key.startswith("KCAT:"):
            self._finish_layout_transition()
            self.keyboard.open_kannada_category(key.removeprefix("KCAT:"))
        elif key.startswith("KREGION:"):
            self._finish_layout_transition()
            self.keyboard.open_kannada_group(key.removeprefix("KREGION:"))
        elif key.startswith("KPHRASE_CATEGORY:"):
            self._finish_layout_transition()
            self.keyboard.open_kannada_phrase_category(
                key.removeprefix("KPHRASE_CATEGORY:")
            )
        elif key.startswith("KPHRASE:"):
            phrase = key.removeprefix("KPHRASE:").strip()
            if phrase:
                self.set_current_text(phrase)
                self.update_suggestions()
                self.tts.speak(
                    phrase,
                    LANG_KANNADA,
                    priority=TextToSpeech.PRIORITY_USER,
                    owner=self.speech_owner,
                    cache=True,
                )
        elif key == "KPHRASE_MORE":
            self._finish_layout_transition()
            self.keyboard.open_kannada_more_phrases()
        elif key == "KPHRASE_BACK":
            self._finish_layout_transition()
            self.keyboard.show_kannada_phrase_home()
        elif key == "KUSTOM":
            self._finish_layout_transition()
            self.keyboard.open_kannada_custom_type()
            self._announce_mode_once("keyboard_custom")
        elif key == "KCUSTOM_BACK":
            self._finish_layout_transition()
            self.keyboard.show_kannada_phrase_home()
            self._announce_mode_once("keyboard")
        elif key == "KBACK":
            self._finish_layout_transition()
            self.keyboard.navigate_kannada_back()
        elif key.startswith("KCHAR:"):
            self._append_kannada_character(key.removeprefix("KCHAR:"))
            self._finish_layout_transition(close_zoom=True)
            self.update_suggestions()
        elif key.startswith("KWORD:"):
            word = key.removeprefix("KWORD:")
            text = self.get_current_text()
            separator = "" if not text or text[-1].isspace() else " "
            self.set_current_text(text + separator + word + " ")
            self._finish_layout_transition(close_zoom=True)
            self.update_suggestions()
        elif key in {"TYPE", "KEYBOARD", "PAGE"}:
            self._remember_selection_anchor()
            self.reset_dwell(preserve_selection_anchor=True)
            self.intent_tracker.clear()
            self.keyboard.show_keyboard()
            self._announce_mode_once("keyboard")
        elif key == "HOME":
            self._remember_selection_anchor()
            self.reset_dwell(preserve_selection_anchor=True)
            self.intent_tracker.clear()
            self.keyboard.show_home()
            self._announce_mode_once("emergency")
        elif key == "BACK":
            self._finish_layout_transition(close_zoom=True)
            self.keyboard.show_home()
            self._announce_mode_once("emergency")
        elif key == "CATEGORIES":
            self._finish_layout_transition()
            self.keyboard.kannada_category = None
            self.keyboard.kannada_zoom_group = None
            self.keyboard.show_keyboard()
        elif key == "SPEAK":
            self.speak_current_text()
        elif key == "SPACE":
            text = self.get_current_text()
            if text and not text[-1].isspace():
                self.keyboard.append_text(" ")
            self.update_suggestions()
        elif key == "BACKSPACE":
            self.keyboard.backspace()
            self.update_suggestions()
        elif key in {"CLEAR", "CLEAR_TOP"}:
            self.keyboard.clear_text()
            self.update_suggestions()
        elif len(key) == 1 and key.isalpha():
            self.keyboard.append_text(key)
            self._finish_layout_transition(close_zoom=True)
            self.update_suggestions()
        return True

    def _append_kannada_character(self, character: str) -> None:
        """Append a base character or a dependent sign in Unicode order."""
        if not character:
            return
        dependent_signs = frozenset("ಾಿೀುೂೃೆೇೈೊೋೌ್")
        text = self.get_current_text()
        if character in dependent_signs and (not text or text[-1].isspace()):
            return
        if character in dependent_signs and text[-1] in dependent_signs:
            self.set_current_text(text[:-1] + character)
            return
        self.keyboard.append_text(character)

    def _remember_selection_anchor(self) -> None:
        if self.last_control_point is not None:
            self.selection_anchor = self.last_control_point

    def _finish_layout_transition(self, close_zoom: bool = False) -> None:
        """Prevent immediate reselection after a target disappears on redraw."""
        self._remember_selection_anchor()
        self.reset_dwell(preserve_selection_anchor=True)
        self.intent_tracker.clear()
        if close_zoom:
            self.keyboard.close_zoom()

    def _activate_emergency(self, phrase: str) -> None:
        emergency = self.language_manager.get_emergency_phrase(phrase)
        displayed = emergency["display"]
        spoken = emergency["speech"]
        current_text = self.get_current_text()
        if current_text and not current_text[-1].isspace():
            current_text += " "
        self.set_current_text(current_text + displayed + " ")
        self.update_suggestions()
        self.tts.speak(
            spoken,
            self.language_manager.get_language(),
            priority=TextToSpeech.PRIORITY_USER,
            owner=self.speech_owner,
            cache=True,
        )

    def _activate_suggestion(self, action: str) -> None:
        try:
            index = int(action.removeprefix(self.SUGGESTION_PREFIX))
            suggestion = self.keyboard.suggestions[index]
        except (ValueError, IndexError):
            return
        if not suggestion:
            return

        updated_text = self.predictor.apply_suggestion(
            self.get_current_text(),
            suggestion,
        )
        self.set_current_text(updated_text)
        self.update_suggestions()

    def update_suggestions(self) -> None:
        if not self._keyboard_exists():
            return
        self.keyboard.set_suggestions(
            self.predictor.predict(self.get_current_text())
        )

    def get_current_text(self) -> str:
        return self.keyboard.get_text() if self.keyboard is not None else ""

    def set_current_text(self, text: str) -> None:
        if self.keyboard is not None:
            self.keyboard.set_text(text)

    def speak_current_text(self) -> None:
        text = self.get_current_text().strip()
        if text:
            self.tts.speak(
                text,
                self.language_manager.get_language(),
                priority=TextToSpeech.PRIORITY_USER,
                owner=self.speech_owner,
                cache=False,
            )

    def replay_current_instruction(self) -> None:
        """Replay guidance on demand without changing navigation or calibration."""
        if not self._keyboard_exists():
            return
        mode = self.keyboard._instruction_name()
        self.tts.speak(
            self.language_manager.get_instruction(
                mode, self.language_manager.get_language()
            ),
            self.language_manager.get_language(),
            priority=TextToSpeech.PRIORITY_INSTRUCTION,
            owner=self.speech_owner,
            cache=True,
        )

    def _update_debug(
        self,
        raw_x: float | None,
        raw_y: float | None,
        smoothed_x: float | None,
        smoothed_y: float | None,
        target: str | None,
        *,
        valid: bool,
        invalid_duration: float,
    ) -> None:
        if not self._keyboard_exists():
            return
        self.keyboard.update_gaze_debug(
            raw_x,
            raw_y,
            smoothed_x,
            smoothed_y,
            target,
            self.dwell_progress,
            invalid_duration,
            self.maximum_right_reach,
            valid,
            self.horizontal_debug,
            self.model_debug,
        )

    def _schedule_next_frame(self) -> None:
        if self.shutting_down or self.after_id is not None:
            return
        if not self._keyboard_exists():
            return
        try:
            self.after_id = self.keyboard.after(
                self.interval_ms,
                self.process_gaze_frame,
            )
        except tk.TclError:
            self.after_id = None

    def _keyboard_exists(self) -> bool:
        if self.keyboard is None:
            return False
        try:
            return bool(self.keyboard.winfo_exists())
        except tk.TclError:
            return False

    def _set_tracking_warning(self, message: str) -> None:
        """Update patient status and log only lost/restored transitions."""
        lost = bool(message)
        if lost != self.tracking_lost:
            self.tracking_lost = lost
            if lost:
                self.logger.warning("tracking lost: %s", message)
            else:
                self.logger.info("tracking restored")
        if self._keyboard_exists():
            self.keyboard.set_tracking_status(message)

    def _ensure_camera_available(self, tracker: EyeTracker, parent: Any) -> bool:
        """Open the webcam with an explicit patient-controlled retry path."""
        capture = tracker.cap
        if capture is not None and capture.isOpened():
            return True
        while not tracker.start():
            detail = tracker.last_error or "unknown camera error"
            self.logger.error("camera unavailable: %s", detail)
            try:
                retry = messagebox.askretrycancel(
                    "GazeLink Camera",
                    self.language_manager.get_text("camera_unavailable"),
                    parent=parent,
                )
            except tk.TclError:
                print(f"Camera unavailable: {detail}")
                return False
            if not retry:
                return False
        return True

    def _prompt_runtime_camera_retry(self) -> None:
        """Offer one non-destructive camera reopen after repeated read failures."""
        if self.shutting_down or self.eye_tracker is None:
            return
        try:
            retry = messagebox.askretrycancel(
                "GazeLink Camera",
                self.language_manager.get_text("camera_unavailable"),
                parent=self.keyboard,
            )
        except tk.TclError:
            retry = False
        if retry and self.eye_tracker.start():
            self.camera_failure_prompted = False
            self.invalid_started_at = None
            self.invalid_frame_count = 0
            self._set_tracking_warning("")
            self.logger.info("camera reopened after runtime failure")
        elif retry:
            self.logger.error("camera retry failed: %s", self.eye_tracker.last_error)

    @staticmethod
    def _release_tracker(tracker: EyeTracker | None) -> None:
        if tracker is None:
            return
        try:
            tracker.stop()
        except Exception as exc:
            print(f"Camera release failed: {exc}")
        face_mesh = getattr(tracker, "face_mesh", None)
        close_mesh = getattr(face_mesh, "close", None)
        if callable(close_mesh):
            try:
                close_mesh()
            except Exception as exc:
                print(f"MediaPipe release failed: {exc}")

    @staticmethod
    def _show_error(title: str, message: str, parent: Any) -> None:
        try:
            messagebox.showerror(title, message, parent=parent)
        except tk.TclError:
            print(f"{title}: {message}")

    def shutdown(self, _event: Any = None) -> None:
        """Cancel callbacks and release camera, face mesh, TTS, and windows."""
        if self.shutting_down:
            return
        self.shutting_down = True

        if self.after_id is not None and self._keyboard_exists():
            try:
                self.keyboard.after_cancel(self.after_id)
            except tk.TclError:
                pass
        self.after_id = None

        self.reset_dwell()
        self._release_tracker(self.eye_tracker)
        self.eye_tracker = None
        shutdown_tts = getattr(self.tts, "shutdown", None)
        try:
            if callable(shutdown_tts):
                shutdown_tts()
            else:
                self.tts.stop()
        except Exception:
            self.logger.exception("TTS shutdown failed")

        if self.calibration_screen is not None:
            calibration_tracker = self.calibration_screen.eye_tracker
            try:
                if self.calibration_screen.winfo_exists():
                    self.calibration_screen.close()
            except (tk.TclError, RuntimeError):
                self.logger.exception("calibration window shutdown failed")
            self._release_tracker(calibration_tracker)

        if self.keyboard is not None:
            try:
                if self.keyboard.winfo_exists():
                    self.keyboard.close()
            except (tk.TclError, RuntimeError):
                self.logger.exception("communication window shutdown failed")
        self.logger.info("application shutdown complete")


def run_target_acquisition_synthetic_tests() -> None:
    """Exercise containment-first acquisition without a camera or Tk root."""

    class FakeKeyboard:
        EMERGENCY_HOME = "EMERGENCY_HOME"
        KEYBOARD = "KEYBOARD"
        ZOOM_REGIONS = {"MIDDLE_MIDDLE": ("F", "G", "H")}

        def __init__(self, targets, mode="EMERGENCY_HOME", zoom=None):
            self.targets = targets
            self.mode = mode
            self.zoom_region = zoom
            self.progress = 0.0

        @staticmethod
        def _inside(bounds, x, y):
            x1, y1, x2, y2 = bounds
            return x1 <= x <= x2 and y1 <= y <= y2

        def get_direct_target_at_position(self, x, y):
            matches = [
                name for name, target in self.targets.items()
                if self._inside(target["bounds"], x, y)
            ]
            return matches[0] if len(matches) == 1 else None

        def is_within_exit_bounds(self, name, x, y):
            target = self.targets.get(name)
            return bool(
                target
                and self._inside(target.get("exit_bounds", target["bounds"]), x, y)
            )

        def get_intent_targets(self):
            return self.targets

        def highlight_key(self, _name):
            pass

        def clear_highlight(self):
            pass

        def start_dwell(self, _name):
            self.progress = 0.0

        def set_dwell_progress(self, _name, progress):
            self.progress = progress

        def stop_dwell(self):
            self.progress = 0.0

        def restore_key_sizes(self):
            pass

        def is_zoom_active(self):
            return self.zoom_region is not None

        def winfo_exists(self):
            return True

    def target(bounds):
        x1, y1, x2, y2 = bounds
        return {
            "bounds": bounds,
            "exit_bounds": (
                x1 - config.TARGET_EXIT_MARGIN,
                y1 - config.TARGET_EXIT_MARGIN,
                x2 + config.TARGET_EXIT_MARGIN,
                y2 + config.TARGET_EXIT_MARGIN,
            ),
            "center": ((x1 + x2) / 2, (y1 + y2) / 2),
            "intent_scale": 1.0,
        }

    def controller(keyboard):
        app = GazeLinkApp.__new__(GazeLinkApp)
        app.keyboard = keyboard
        app.intent_tracker = GazeIntentTracker()
        app.current_key = None
        app.candidate_key = None
        app.candidate_started_at = None
        app.candidate_source = "NONE"
        app.candidate_acquire_time = config.TARGET_ACQUIRE_TIME
        app.focus_started_at = None
        app.dwell_progress = 0.0
        app.selection_locked = False
        app.selection_anchor = None
        app.last_control_point = None
        app.target_loss_started_at = None
        app.last_intent_trace_time = 0.0
        app.last_intent_trace_signature = None
        app.last_target_resolution = {}
        return app

    emergency = FakeKeyboard({
        "EMERGENCY:HELP": target((0, 0, 100, 100)),
        "EMERGENCY:WATER": target((120, 0, 220, 100)),
    })
    app = controller(emergency)
    for index in range(4):
        app.intent_tracker.add_point(50, 50, index * 0.03)
    app.intent_tracker.current_target = "EMERGENCY:HELP"
    resolution = app._resolve_live_target(170, 50, 1.0)
    assert resolution["hit_test"] == "DIRECT"
    assert resolution["final_target"] == "EMERGENCY:WATER"
    app._process_intent_target("EMERGENCY:WATER", 170, 50, 1.0, 0.0, 1.0, "DIRECT")
    app._process_intent_target("EMERGENCY:WATER", 170, 50, 1.16, 0.03, 1.0, "DIRECT")
    app._process_intent_target("EMERGENCY:WATER", 170, 50, 1.19, 0.03, 1.0, "DIRECT")
    assert app.current_key == "EMERGENCY:WATER" and emergency.progress > 0.0

    # The acquired WATER target wins while gaze remains in its exit margin,
    # even where the current point directly enters neighbouring HELP.
    resolution = app._resolve_live_target(95, 50, 1.22)
    assert resolution["hit_test"] == "LOCK"
    assert resolution["final_target"] == "EMERGENCY:WATER"

    # A short invalid interval performs no target update: progress and the
    # acquired identity remain available for the existing blink-grace path.
    paused_progress = emergency.progress
    assert app.current_key == "EMERGENCY:WATER"
    assert emergency.progress == paused_progress

    # A sustained target loss still clears dwell and the acquired identity.
    app._decay_target_progress(1.30, 0.03)
    app._decay_target_progress(
        1.30 + config.TARGET_LOSS_RESET_SECONDS + 0.01,
        0.03,
    )
    assert app.current_key is None and emergency.progress == 0.0

    overview = FakeKeyboard(
        {"REGION:MIDDLE_MIDDLE": target((0, 0, 300, 160))},
        mode="KEYBOARD",
    )
    app = controller(overview)
    resolution = app._resolve_live_target(230, 80, 2.0)
    assert resolution["final_target"] == "REGION:MIDDLE_MIDDLE"
    assert resolution["final_target"] != "H"

    zoom = FakeKeyboard(
        {
            "G": target((0, 0, 90, 120)),
            "H": target((110, 0, 200, 120)),
        },
        mode="KEYBOARD",
        zoom="MIDDLE_MIDDLE",
    )
    app = controller(zoom)
    for index in range(4):
        app.intent_tracker.add_point(45, 60, 2.1 + index * 0.03)
    app.intent_tracker.current_target = "G"
    resolution = app._resolve_live_target(155, 60, 2.3)
    assert resolution["hit_test"] == "DIRECT"
    assert resolution["final_target"] == "H"

    # A shared boundary is ambiguous direct containment and uses intent fallback.
    boundary = FakeKeyboard({
        "G": target((0, 0, 100, 120)),
        "H": target((100, 0, 200, 120)),
    }, mode="KEYBOARD", zoom="MIDDLE_MIDDLE")
    app = controller(boundary)
    assert app._resolve_live_target(100, 60, 3.0)["hit_test"] != "DIRECT"
    assert config.DIRECT_MIN_CONFIDENCE < config.CONFIDENCE_MEDIUM_THRESHOLD
    print("Target acquisition synthetic tests passed.")


if __name__ == "__main__":
    if "--target-acquisition-test" in sys.argv:
        run_target_acquisition_synthetic_tests()
        raise SystemExit(0)
    if "--mouse-test" in sys.argv:
        MOUSE_TEST_MODE = True
    if "--presentation-mode" in sys.argv:
        PRESENTATION_MODE = True
        # Presentation Mode must always use real eye features.  It is never a
        # mouse-driven simulation, even if conflicting flags are supplied.
        MOUSE_TEST_MODE = False
    if "--calibration-static" in sys.argv:
        CALIBRATION_MODE_OVERRIDE = config.CALIBRATION_STATIC_MODE
        FORCE_RECALIBRATION = True
    if "--calibration-pursuit" in sys.argv:
        CALIBRATION_MODE_OVERRIDE = config.CALIBRATION_PURSUIT_MODE
        FORCE_RECALIBRATION = True
    if "--fast-calibration" in sys.argv:
        CALIBRATION_MODE_OVERRIDE = config.CALIBRATION_FAST_MODE
        FORCE_RECALIBRATION = True
    if "--skip-verification" in sys.argv:
        SKIP_VERIFICATION = True
        config.CALIBRATION_DEBUG = True
        config.PATIENT_MODE = False
    if "--horizontal-zones" in sys.argv:
        config.HORIZONTAL_ZONE_DEBUG = True
        config.PATIENT_MODE = False
    if "--calibration-diagnostic" in sys.argv:
        FORCE_RECALIBRATION = True
        config.GAZE_DEBUG = True
        config.CALIBRATION_DEBUG = True
        config.PATIENT_MODE = False
    if "--force-calibration" in sys.argv:
        # Ignore the saved profile for this run. It remains untouched until
        # the newly verified model is explicitly accepted on the score screen.
        FORCE_RECALIBRATION = True
    if "--dwell-diagnostic" in sys.argv:
        config.DWELL_DIAGNOSTIC_MODE = True
        config.GAZE_DEBUG = True
        config.PATIENT_MODE = False
    if "--intent-debug" in sys.argv:
        config.INTENT_DEBUG = True
        config.GAZE_DEBUG = True
        config.PATIENT_MODE = False
    if "--tts-debug" in sys.argv:
        config.TTS_DEBUG = True
    if "--demo-mode" in sys.argv:
        DEMO_MODE = True
        config.DEMO_MODE = True
        config.PATIENT_MODE = True
        config.GAZE_DEBUG = False
        config.CALIBRATION_DEBUG = False
        config.HORIZONTAL_ZONE_DEBUG = False
        config.DWELL_DIAGNOSTIC_MODE = False
        config.INTENT_DEBUG = False
    GazeLinkApp().run()
