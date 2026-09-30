import math
import sys
import time

import cv2
import mediapipe as mp

try:
    from .. import config
    from ..utils.runtime import get_logger
except ImportError:
    import config
    from utils.runtime import get_logger


class EyeTracker:
    """Capture webcam frames and estimate normalized two-dimensional gaze."""

    LEFT_IRIS_INDEX = 468
    RIGHT_IRIS_INDEX = 473
    LEFT_EYE_CORNERS = (33, 133)
    RIGHT_EYE_CORNERS = (362, 263)
    LEFT_EYELIDS = (159, 145)
    RIGHT_EYELIDS = (386, 374)
    FACE_REFERENCE_CORNERS = (33, 263)

    def __init__(self):
        self.cap = None
        self.face_mesh = mp.solutions.face_mesh.FaceMesh(
            refine_landmarks=True,
            max_num_faces=1,
        )
        self.debug_info = self._empty_debug_info()
        self._latest_landmarks = None
        self.last_error = ""
        self.consecutive_read_failures = 0

    def start(self):
        """Open the configured webcam."""
        try:
            if self.cap is not None:
                self.cap.release()
            self.cap = cv2.VideoCapture(config.WEBCAM_INDEX)
            if not self.cap.isOpened():
                self.last_error = (
                    f"camera index {config.WEBCAM_INDEX} could not be opened; "
                    "it may be missing or in use"
                )
                self.cap.release()
                self.cap = None
                get_logger().error("camera failure: %s", self.last_error)
                return False
            self.last_error = ""
            self.consecutive_read_failures = 0
            get_logger().info("camera opened index=%s", config.WEBCAM_INDEX)
            return True
        except Exception as exc:
            self.last_error = f"camera open raised {type(exc).__name__}: {exc}"
            get_logger().exception("camera open failed index=%s", config.WEBCAM_INDEX)
            self.stop()
            return False

    def stop(self):
        """Release the webcam if it is open."""
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass

    @staticmethod
    def _to_pixel(landmark, frame_width, frame_height):
        return (
            int(landmark.x * frame_width),
            int(landmark.y * frame_height),
        )

    def get_iris_centers(self, landmarks, frame_width, frame_height):
        """Return the existing left and right iris centers in pixels."""
        left_iris = self._to_pixel(
            landmarks[self.LEFT_IRIS_INDEX], frame_width, frame_height
        )
        right_iris = self._to_pixel(
            landmarks[self.RIGHT_IRIS_INDEX], frame_width, frame_height
        )
        return left_iris, right_iris

    def get_frame(self):
        """Preserve the legacy `(frame, horizontal_ratio)` API."""
        frame, gaze_ratios = self.get_gaze_frame()
        horizontal_ratio = gaze_ratios[0] if gaze_ratios is not None else None
        return frame, horizontal_ratio

    def get_gaze_frame(self):
        """Return `(frame, (horizontal_ratio, vertical_ratio))` when valid."""
        self.debug_info = self._empty_debug_info()
        self._latest_landmarks = None
        if self.cap is None or not self.cap.isOpened():
            self.last_error = "camera is not open"
            self.debug_info["camera_error"] = self.last_error
            return None, None

        success, frame = self.cap.read()
        if not success:
            self.consecutive_read_failures += 1
            self.last_error = (
                f"camera frame read failed {self.consecutive_read_failures} times"
            )
            self.debug_info["camera_error"] = self.last_error
            self.debug_info["read_failure_count"] = self.consecutive_read_failures
            return None, None

        self.consecutive_read_failures = 0
        self.last_error = ""

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb_frame)
        if not results.multi_face_landmarks:
            return frame, None

        landmarks = results.multi_face_landmarks[0].landmark
        self._latest_landmarks = landmarks
        gaze_ratios = self._calculate_gaze_ratios(landmarks)
        return frame, gaze_ratios

    def get_gaze_ratios(self):
        """Capture one frame and return `(horizontal, vertical)` or `None`."""
        _, gaze_ratios = self.get_gaze_frame()
        return gaze_ratios

    def get_debug_info(self):
        """Return a copy of the latest gaze validity and eye-opening values."""
        return dict(self.debug_info)

    def get_feature_vector(self):
        """Return the latest complete nine-value calibration feature vector."""
        features = self.debug_info.get("feature_vector")
        return tuple(features) if features is not None else None

    def _calculate_gaze_ratios(self, landmarks):
        required_index = max(
            self.LEFT_IRIS_INDEX,
            self.RIGHT_IRIS_INDEX,
            *self.LEFT_EYE_CORNERS,
            *self.RIGHT_EYE_CORNERS,
            *self.LEFT_EYELIDS,
            *self.RIGHT_EYELIDS,
        )
        if len(landmarks) <= required_index:
            return None

        left_horizontal = self._horizontal_ratio(
            landmarks,
            self.LEFT_IRIS_INDEX,
            self.LEFT_EYE_CORNERS,
        )
        right_horizontal = self._horizontal_ratio(
            landmarks,
            self.RIGHT_IRIS_INDEX,
            self.RIGHT_EYE_CORNERS,
        )
        left_vertical, left_opening, left_open = self._vertical_ratio(
            landmarks,
            self.LEFT_IRIS_INDEX,
            self.LEFT_EYE_CORNERS,
            self.LEFT_EYELIDS,
        )
        right_vertical, right_opening, right_open = self._vertical_ratio(
            landmarks,
            self.RIGHT_IRIS_INDEX,
            self.RIGHT_EYE_CORNERS,
            self.RIGHT_EYELIDS,
        )

        eyes_open = left_open and right_open
        values = (
            left_horizontal,
            right_horizontal,
            left_vertical,
            right_vertical,
        )
        finite_values = all(
            value is not None and math.isfinite(value) for value in values
        )
        valid = eyes_open and finite_values

        horizontal_ratio = None
        vertical_ratio = None
        if finite_values:
            horizontal_ratio = (left_horizontal + right_horizontal) / 2.0
            vertical_ratio = (left_vertical + right_vertical) / 2.0

        face_center_x = None
        face_center_y = None
        normalized_eye_distance = None
        if len(landmarks) > max(self.FACE_REFERENCE_CORNERS):
            first = landmarks[self.FACE_REFERENCE_CORNERS[0]]
            second = landmarks[self.FACE_REFERENCE_CORNERS[1]]
            face_center_x = (first.x + second.x) / 2.0
            face_center_y = (first.y + second.y) / 2.0
            normalized_eye_distance = self._distance(first, second)

        feature_vector = (
            left_horizontal,
            right_horizontal,
            left_vertical,
            right_vertical,
            horizontal_ratio,
            vertical_ratio,
            face_center_x,
            face_center_y,
            normalized_eye_distance,
        )
        complete_features = all(
            value is not None and math.isfinite(value)
            for value in feature_vector
        )
        valid = valid and complete_features

        self.debug_info = {
            "left_horizontal_ratio": left_horizontal,
            "right_horizontal_ratio": right_horizontal,
            "left_vertical_ratio": left_vertical,
            "right_vertical_ratio": right_vertical,
            "horizontal_ratio": horizontal_ratio,
            "vertical_ratio": vertical_ratio,
            "face_center_x": face_center_x,
            "face_center_y": face_center_y,
            "normalized_eye_distance": normalized_eye_distance,
            "feature_vector": feature_vector if complete_features else None,
            "left_eye_opening": left_opening,
            "right_eye_opening": right_opening,
            "eyes_open": eyes_open,
            "blink": not eyes_open,
            "valid": valid,
        }
        return (horizontal_ratio, vertical_ratio) if valid else None

    @staticmethod
    def _horizontal_ratio(landmarks, iris_index, corner_indices):
        iris_x = landmarks[iris_index].x
        corner_left_x = landmarks[corner_indices[0]].x
        corner_right_x = landmarks[corner_indices[1]].x
        eye_width = corner_right_x - corner_left_x
        if abs(eye_width) < 1e-6:
            return None
        return (iris_x - corner_left_x) / eye_width

    @staticmethod
    def _distance(first, second):
        return math.hypot(first.x - second.x, first.y - second.y)

    def _vertical_ratio(
        self,
        landmarks,
        iris_index,
        corner_indices,
        eyelid_indices,
    ):
        iris = landmarks[iris_index]
        upper = landmarks[eyelid_indices[0]]
        lower = landmarks[eyelid_indices[1]]
        eyelid_gap = lower.y - upper.y
        eye_width = self._distance(
            landmarks[corner_indices[0]],
            landmarks[corner_indices[1]],
        )

        if eyelid_gap <= config.MIN_EYELID_GAP or eye_width < 1e-6:
            return None, 0.0, False

        opening_ratio = eyelid_gap / eye_width
        eye_open = opening_ratio >= config.EYE_OPEN_THRESHOLD
        vertical_ratio = (iris.y - upper.y) / eyelid_gap
        return vertical_ratio, opening_ratio, eye_open

    @staticmethod
    def _empty_debug_info():
        return {
            "horizontal_ratio": None,
            "vertical_ratio": None,
            "left_horizontal_ratio": None,
            "right_horizontal_ratio": None,
            "left_vertical_ratio": None,
            "right_vertical_ratio": None,
            "face_center_x": None,
            "face_center_y": None,
            "normalized_eye_distance": None,
            "feature_vector": None,
            "left_eye_opening": 0.0,
            "right_eye_opening": 0.0,
            "eyes_open": False,
            "blink": False,
            "valid": False,
            "camera_error": "",
            "read_failure_count": 0,
        }


def run_gaze_diagnostic() -> None:
    """Display live gaze ratios and landmarks until Q, Esc, or Ctrl+C."""
    tracker = EyeTracker()
    window_title = "GazeLink Eye Diagnostic"
    consecutive_read_failures = 0
    maximum_read_failures = 20
    last_terminal_update = 0.0

    try:
        if not tracker.start():
            print("Unable to open webcam for gaze diagnostic.")
            return

        print(
            "Gaze diagnostic running. Press Q, Esc, or Ctrl+C to stop."
        )
        while True:
            frame, ratios = tracker.get_gaze_frame()
            debug = tracker.get_debug_info()

            if frame is None:
                consecutive_read_failures += 1
                if consecutive_read_failures >= maximum_read_failures:
                    print(
                        "Webcam read failed repeatedly; stopping diagnostic."
                    )
                    break
                time.sleep(0.03)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), ord("Q"), 27):
                    break
                continue

            consecutive_read_failures = 0
            _draw_diagnostic_landmarks(
                frame,
                tracker._latest_landmarks,
                tracker,
            )
            _draw_diagnostic_text(frame, ratios, debug)
            cv2.imshow(window_title, frame)

            if ratios is None:
                horizontal = "--"
                vertical = "--"
            else:
                horizontal = f"{ratios[0]:.2f}"
                vertical = f"{ratios[1]:.2f}"
            eye_state = _diagnostic_eye_state(debug)

            now = time.monotonic()
            if now - last_terminal_update >= 0.3:
                print(
                    f"H: {horizontal}  V: {vertical}  "
                    f"Eyes: {eye_state}  Valid: {debug['valid']}  "
                    f"Blink: {debug['blink']}"
                )
                last_terminal_update = now

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                break
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"Gaze diagnostic error: {exc}")
    finally:
        tracker.stop()
        close_mesh = getattr(tracker.face_mesh, "close", None)
        if callable(close_mesh):
            close_mesh()
        cv2.destroyAllWindows()
        print("Gaze diagnostic stopped.")


def _diagnostic_eye_state(debug_info) -> str:
    """Return a clear eye state from the current debug flags."""
    if debug_info.get("eyes_open"):
        return "OPEN"
    if debug_info.get("blink"):
        return "CLOSED"
    return "UNKNOWN"


def _draw_diagnostic_text(frame, gaze_ratios, debug_info) -> None:
    """Draw readable, separately labelled diagnostic values on a frame."""
    if gaze_ratios is None:
        horizontal = "--"
        vertical = "--"
    else:
        horizontal = f"{gaze_ratios[0]:.2f}"
        vertical = f"{gaze_ratios[1]:.2f}"

    lines = (
        f"Horizontal: {horizontal}",
        f"Vertical: {vertical}",
        f"Eyes: {_diagnostic_eye_state(debug_info)}",
        f"Gaze valid: {debug_info.get('valid', False)}",
        f"Blink: {debug_info.get('blink', False)}",
    )

    cv2.rectangle(frame, (10, 10), (310, 170), (0, 0, 0), -1)
    for line_number, text in enumerate(lines):
        cv2.putText(
            frame,
            text,
            (22, 40 + line_number * 29),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.68,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )


def _draw_diagnostic_landmarks(frame, landmarks, tracker) -> None:
    """Draw iris, corner, and eyelid reference landmarks when available."""
    if landmarks is None:
        return

    height, width = frame.shape[:2]
    groups = (
        ((tracker.LEFT_IRIS_INDEX, tracker.RIGHT_IRIS_INDEX), (0, 255, 255), 5),
        (
            (*tracker.LEFT_EYE_CORNERS, *tracker.RIGHT_EYE_CORNERS),
            (0, 255, 0),
            4,
        ),
        (
            (*tracker.LEFT_EYELIDS, *tracker.RIGHT_EYELIDS),
            (255, 128, 0),
            4,
        ),
    )

    for indices, color, radius in groups:
        for index in indices:
            if index >= len(landmarks):
                continue
            x, y = tracker._to_pixel(landmarks[index], width, height)
            cv2.circle(frame, (x, y), radius, color, -1, cv2.LINE_AA)


if __name__ == "__main__":
    if "--diagnostic" in sys.argv:
        run_gaze_diagnostic()
    else:
        print("Run with --diagnostic to print live horizontal and vertical gaze.")
