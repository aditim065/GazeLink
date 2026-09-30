"""Central offline language strings and persisted GazeLink preference."""

from __future__ import annotations

import json
from pathlib import Path

try:
    from .. import config
    from .runtime import get_data_path, get_logger
except ImportError:
    import config
    from utils.runtime import get_data_path, get_logger


LANG_ENGLISH = "en"
LANG_KANNADA = "kn"
SUPPORTED_LANGUAGES = (LANG_ENGLISH, LANG_KANNADA)


INSTRUCTIONS = {
    LANG_ENGLISH: {
        "welcome": (
            "Welcome to GazeLink. Please choose your language: "
            "English or Kannada."
        ),
        "calibration": (
            "Eye calibration will begin next. First, nine points will appear at "
            "different positions on the screen. Look carefully at each point and "
            "keep your head comfortably steady. After that, five verification "
            "points will appear: left, centre, right, up, and down. Look at each "
            "point until it moves to the next one."
        ),
        "calibration_progress": (
            "Calibration in progress. Look at the highlighted point."
        ),
        "calibration_verification": (
            "Calibration is complete. We will now quickly check the accuracy. "
            "Look at each verification point until it moves to the next one."
        ),
        "calibration_complete": (
            "Calibration check is complete. You can now start using GazeLink."
        ),
        "emergency": "Look at a button and keep looking until the circle completes.",
        "keyboard": (
            "Look at a letter area. Nearby letters will enlarge. Keep looking at "
            "the letter you want until the circle completes."
        ),
        "keyboard_custom": (
            "Look at a character group. Choose the character you want from the "
            "enlarged choices."
        ),
    },
    LANG_KANNADA: {
        "welcome": (
            "ಗೇಜ್‌ಲಿಂಕ್‌ಗೆ ಸ್ವಾಗತ. ದಯವಿಟ್ಟು ನಿಮ್ಮ ಭಾಷೆಯನ್ನು ಆಯ್ಕೆ ಮಾಡಿ: "
            "ಇಂಗ್ಲಿಷ್ ಅಥವಾ ಕನ್ನಡ."
        ),
        "calibration": (
            "ಮುಂದೆ ಕಣ್ಣಿನ ಕ್ಯಾಲಿಬ್ರೇಶನ್ ಆರಂಭವಾಗುತ್ತದೆ. ಮೊದಲು, ಪರದೆಯ ವಿವಿಧ "
            "ಸ್ಥಳಗಳಲ್ಲಿ ಒಂಬತ್ತು ಚುಕ್ಕಿಗಳು ಕಾಣಿಸುತ್ತವೆ. ಪ್ರತಿಯೊಂದು ಚುಕ್ಕಿಯನ್ನು "
            "ಗಮನವಾಗಿ ನೋಡಿ ಮತ್ತು ತಲೆಯನ್ನು ಆರಾಮವಾಗಿ ಸ್ಥಿರವಾಗಿರಿಸಿ. ನಂತರ, ಎಡ, "
            "ಮಧ್ಯ, ಬಲ, ಮೇಲೆ ಮತ್ತು ಕೆಳಗೆ ಐದು ಪರಿಶೀಲನಾ ಚುಕ್ಕಿಗಳು ಕಾಣಿಸುತ್ತವೆ. "
            "ಪ್ರತಿಯೊಂದು ಚುಕ್ಕಿ ಮುಂದಿನದಕ್ಕೆ ಚಲಿಸುವವರೆಗೆ ಅದನ್ನು ನೋಡಿ."
        ),
        "calibration_progress": (
            "ಕ್ಯಾಲಿಬ್ರೇಶನ್ ನಡೆಯುತ್ತಿದೆ. ಗುರುತಿಸಲಾದ ಚುಕ್ಕಿಯನ್ನು ನೋಡಿ."
        ),
        "calibration_verification": (
            "ಕ್ಯಾಲಿಬ್ರೇಶನ್ ಪೂರ್ಣವಾಗಿದೆ. ಈಗ ನಿಖರತೆಯನ್ನು ತ್ವರಿತವಾಗಿ "
            "ಪರಿಶೀಲಿಸುತ್ತೇವೆ. ಪ್ರತಿಯೊಂದು ಪರಿಶೀಲನಾ ಚುಕ್ಕಿ ಮುಂದಿನದಕ್ಕೆ "
            "ಚಲಿಸುವವರೆಗೆ ಅದನ್ನು ನೋಡಿ."
        ),
        "calibration_complete": (
            "ಕ್ಯಾಲಿಬ್ರೇಶನ್ ಪರಿಶೀಲನೆ ಪೂರ್ಣವಾಗಿದೆ. ಈಗ ನೀವು ಗೇಜ್‌ಲಿಂಕ್ ಬಳಸಬಹುದು."
        ),
        "emergency": "ಆಯ್ಕೆಯನ್ನು ನೋಡಿ. ವೃತ್ತ ಪೂರ್ಣವಾಗುವವರೆಗೆ ದೃಷ್ಟಿಯನ್ನು ಅಲ್ಲೇ ಇರಿಸಿ.",
        "keyboard": "ಬೇಕಾದ ವಾಕ್ಯವನ್ನು ನೋಡಿ. ವೃತ್ತ ಪೂರ್ಣವಾಗುವವರೆಗೆ ದೃಷ್ಟಿಯನ್ನು ಅಲ್ಲೇ ಇರಿಸಿ.",
        "keyboard_custom": (
            "ಅಕ್ಷರಗಳ ಗುಂಪನ್ನು ನೋಡಿ. ದೊಡ್ಡದಾಗಿ ಕಾಣುವ ಆಯ್ಕೆಗಳಿಂದ ಬೇಕಾದ "
            "ಅಕ್ಷರವನ್ನು ಆಯ್ಕೆ ಮಾಡಿ."
        ),
    },
}


TRANSLATIONS = {
    LANG_ENGLISH: {
        "language": "LANGUAGE",
        "back": "BACK",
        "english": "ENGLISH",
        "kannada": "ಕನ್ನಡ",
        "message": "Message",
        "message_placeholder": "Your message will appear here.",
        "camera_unavailable": "Camera unavailable. Please check the webcam and try again.",
        "face_not_detected": "Face not detected. Please look toward the camera.",
        "presentation_look_center": "Look at the centre of the screen.",
        "presentation_face_center": "Face the camera and look at the centre.",
        "presentation_recenter": "Look at the centre to continue.",
        "presentation_eyes_unclear": "Eyes not detected clearly.",
        "presentation_face_detected": "Face detected",
        "presentation_eyes_detected": "Eyes detected",
        "presentation_ready": "Ready",
        "presentation_direction_uncertain": "Uncertain",
        "presentation_direction_center": "Centre",
        "presentation_direction_left": "Left",
        "presentation_direction_right": "Right",
        "presentation_direction_up": "Up",
        "presentation_direction_down": "Down",
        "presentation_direction_up_left": "Upper left",
        "presentation_direction_up_right": "Upper right",
        "presentation_direction_down_left": "Lower left",
        "presentation_direction_down_right": "Lower right",
        "suggestions": "Suggestions",
        "calibration": "Eye Calibration",
        "calibration_failed": "Calibration failed",
        "help": "HELP",
        "water": "WATER",
        "food": "FOOD",
        "toilet": "TOILET",
        "pain": "PAIN",
        "clear": "CLEAR",
        "type": "TYPE",
        "space": "SPACE",
        "backspace": "BACKSPACE",
        "speak": "SPEAK",
        "home": "HOME",
        "categories": "CATEGORIES",
        "vowels": "VOWELS",
        "consonants": "CONSONANTS",
        "signs": "SIGNS / MARKS",
        "common_words": "COMMON WORDS",
        "basic_needs": "BASIC NEEDS",
        "health_pain": "HEALTH / PAIN",
        "responses": "RESPONSES",
        "help_people": "HELP / PEOPLE",
        "custom_type": "CUSTOM TYPE",
        "more": "MORE",
        "look_letter": "Look at the intended character",
        "look_naturally": "Look naturally at the highlighted dot.",
        "blink_normally": "Keep looking at the highlighted point.",
        "checking_control": "Checking your eye control...",
        "continue": "CONTINUE",
        "retry": "RETRY",
        "recalibrate": "RECALIBRATE",
        "improve_accuracy": "IMPROVE ACCURACY",
        "gaze_readiness": "GAZE READINESS",
        "calibration_low": "Calibration accuracy is too low.",
        "adjust_position": "Please adjust your position and try again.",
        "excellent": "Excellent",
        "good": "Good",
        "emergency_ready": "Emergency Ready",
        "emergency_capability": "✓ Emergency communication",
        "typing_capability": "✓ Text typing and word suggestions",
        "typing_warning": "△ Text typing may be less accurate",
        "letters_slow": "Some letters may require more time.",
        "welcome_title": "GazeLink",
        "welcome_subtitle": "Assistive Eye-Gaze Communication",
        "select_language": "Select your language",
        "saved_language": "Previously selected",
        "calibration_title": "Eye Calibration",
        "calibration_instruction_1": "Sit comfortably and look naturally at each highlighted point.",
        "calibration_instruction_2": "",
        "calibration_instruction_3": "Try to keep your head in the same comfortable position.",
        "calibration_instruction_speech": INSTRUCTIONS[LANG_ENGLISH]["calibration"],
        "start": "START",
        "please_listen": "Please listen...",
        "ready_start": "Ready — look at START",
        "calibration_diagram": "Calibration — 9 points",
        "verification_diagram": "Verification — 5 points",
        "emergency_help": "Look at an option and hold your gaze to select.",
        "emergency_help_speech": "Look at a button and keep looking until the circle completes.",
        "keyboard_help": "Look at a letter area. Select from the enlarged letters.",
        "keyboard_help_speech": "Choose a phrase, or select Custom Type to enter your own message.",
        "startup_speech": "Welcome to GazeLink. Choose your language.",
        "english_selected_speech": "English selected.",
        "kannada_selected_speech": "You selected Kannada.",
        "voice_unavailable": "The selected voice is unavailable.",
    },
    LANG_KANNADA: {
        "language": "ಭಾಷೆ",
        "back": "ಹಿಂದೆ",
        "english": "ENGLISH",
        "kannada": "ಕನ್ನಡ",
        "message": "ಸಂದೇಶ",
        "message_placeholder": "ನಿಮ್ಮ ಸಂದೇಶ ಇಲ್ಲಿ ಕಾಣಿಸುತ್ತದೆ.",
        "camera_unavailable": "ಕ್ಯಾಮೆರಾ ಲಭ್ಯವಿಲ್ಲ. ವೆಬ್‌ಕ್ಯಾಮ್ ಪರಿಶೀಲಿಸಿ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
        "presentation_look_center": "ಪರದೆಯ ಮಧ್ಯಭಾಗವನ್ನು ನೋಡಿ.",
        "presentation_face_center": "ಕ್ಯಾಮೆರಾ ಕಡೆ ಮುಖ ಮಾಡಿ ಪರದೆಯ ಮಧ್ಯಭಾಗವನ್ನು ನೋಡಿ.",
        "presentation_recenter": "ಮುಂದುವರಿಸಲು ಪರದೆಯ ಮಧ್ಯಭಾಗವನ್ನು ನೋಡಿ.",
        "presentation_eyes_unclear": "ಕಣ್ಣುಗಳು ಸ್ಪಷ್ಟವಾಗಿ ಕಾಣುತ್ತಿಲ್ಲ.",
        "presentation_face_detected": "ಮುಖ ಪತ್ತೆಯಾಗಿದೆ",
        "presentation_eyes_detected": "ಕಣ್ಣುಗಳು ಪತ್ತೆಯಾಗಿವೆ",
        "presentation_ready": "ಸಿದ್ಧ",
        "presentation_direction_uncertain": "ದೃಷ್ಟಿ ಅಸ್ಪಷ್ಟ",
        "presentation_direction_center": "ಮಧ್ಯ",
        "presentation_direction_left": "ಎಡ",
        "presentation_direction_right": "ಬಲ",
        "presentation_direction_up": "ಮೇಲೆ",
        "presentation_direction_down": "ಕೆಳಗೆ",
        "presentation_direction_up_left": "ಮೇಲಿನ ಎಡ",
        "presentation_direction_up_right": "ಮೇಲಿನ ಬಲ",
        "presentation_direction_down_left": "ಕೆಳಗಿನ ಎಡ",
        "presentation_direction_down_right": "ಕೆಳಗಿನ ಬಲ",
        "face_not_detected": "ಮುಖ ಪತ್ತೆಯಾಗಿಲ್ಲ. ದಯವಿಟ್ಟು ಕ್ಯಾಮೆರಾ ಕಡೆ ನೋಡಿ.",
        "suggestions": "ಸಲಹೆಗಳು",
        "calibration": "ಕಣ್ಣಿನ ಕ್ಯಾಲಿಬ್ರೇಶನ್",
        "calibration_failed": "ಮಾಪನ ವಿಫಲವಾಗಿದೆ",
        "help": "ಸಹಾಯ",
        "water": "ನೀರು",
        "food": "ಆಹಾರ",
        "toilet": "ಶೌಚಾಲಯ",
        "pain": "ನೋವು",
        "clear": "ಅಳಿಸಿ",
        "type": "ಟೈಪ್",
        "space": "ಖಾಲಿ ಜಾಗ",
        "backspace": "ಹಿಂದಕ್ಕೆ",
        "speak": "ಮಾತನಾಡಿ",
        "home": "ಮುಖಪುಟ",
        "categories": "ವರ್ಗಗಳು",
        "vowels": "ಸ್ವರಗಳು",
        "consonants": "ವ್ಯಂಜನಗಳು",
        "signs": "ಮಾತ್ರೆಗಳು",
        "common_words": "ಸಾಮಾನ್ಯ ಪದಗಳು",
        "basic_needs": "ಮೂಲಭೂತ ಅಗತ್ಯಗಳು",
        "health_pain": "ಆರೋಗ್ಯ / ನೋವು",
        "responses": "ಉತ್ತರಗಳು",
        "help_people": "ಸಹಾಯ / ಜನರು",
        "custom_type": "ಅಕ್ಷರ ಟೈಪಿಂಗ್",
        "more": "ಇನ್ನಷ್ಟು",
        "look_letter": "ಬೇಕಾದ ಅಕ್ಷರವನ್ನು ನೋಡಿ",
        "look_naturally": "ಗುರುತಿಸಲಾದ ಚುಕ್ಕಿಯನ್ನು ಸಹಜವಾಗಿ ನೋಡಿ.",
        "blink_normally": "ಗುರುತಿಸಲಾದ ಚುಕ್ಕಿಯನ್ನು ನೋಡುತ್ತಿರಿ.",
        "checking_control": "ನಿಮ್ಮ ಕಣ್ಣಿನ ನಿಯಂತ್ರಣವನ್ನು ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ...",
        "continue": "ಮುಂದುವರಿಸಿ",
        "retry": "ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ",
        "recalibrate": "ಮರು ಮಾಪನ",
        "improve_accuracy": "ನಿಖರತೆ ಸುಧಾರಿಸಿ",
        "gaze_readiness": "ದೃಷ್ಟಿ ಸಿದ್ಧತೆ",
        "calibration_low": "ಮಾಪನದ ನಿಖರತೆ ಕಡಿಮೆಯಾಗಿದೆ.",
        "adjust_position": "ದಯವಿಟ್ಟು ನಿಮ್ಮ ಸ್ಥಾನವನ್ನು ಸರಿಪಡಿಸಿ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
        "excellent": "ಅತ್ಯುತ್ತಮ",
        "good": "ಉತ್ತಮ",
        "emergency_ready": "ತುರ್ತು ಸಂವಹನಕ್ಕೆ ಸಿದ್ಧ",
        "emergency_capability": "✓ ತುರ್ತು ಸಂವಹನ",
        "typing_capability": "✓ ಪಠ್ಯ ಟೈಪಿಂಗ್ ಮತ್ತು ಪದ ಸಲಹೆಗಳು",
        "typing_warning": "△ ಪಠ್ಯ ಟೈಪಿಂಗ್ ಕಡಿಮೆ ನಿಖರವಾಗಿರಬಹುದು",
        "letters_slow": "ಕೆಲವು ಅಕ್ಷರಗಳಿಗೆ ಹೆಚ್ಚು ಸಮಯ ಬೇಕಾಗಬಹುದು.",
        "welcome_title": "GazeLink",
        "welcome_subtitle": "ಸಹಾಯಕ ಕಣ್ಣಿನ ದೃಷ್ಟಿ ಸಂವಹನ",
        "select_language": "ನಿಮ್ಮ ಭಾಷೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ",
        "saved_language": "ಹಿಂದೆ ಆಯ್ಕೆ ಮಾಡಿದ್ದು",
        "calibration_title": "ಕಣ್ಣಿನ ಕ್ಯಾಲಿಬ್ರೇಶನ್",
        "calibration_instruction_1": "ಆರಾಮವಾಗಿ ಕುಳಿತು, ತೋರಿಸುವ ಚುಕ್ಕಿಯನ್ನು ಸಹಜವಾಗಿ ನೋಡಿ.",
        "calibration_instruction_2": "",
        "calibration_instruction_3": "ತಲೆಯನ್ನು ಸಾಧ್ಯವಾದಷ್ಟು ಒಂದೇ ಸ್ಥಿತಿಯಲ್ಲಿ ಇರಿಸಿ.",
        "calibration_instruction_speech": INSTRUCTIONS[LANG_KANNADA]["calibration"],
        "start": "ಪ್ರಾರಂಭಿಸಿ",
        "please_listen": "ದಯವಿಟ್ಟು ಆಲಿಸಿ...",
        "ready_start": "ಸಿದ್ಧ — ಪ್ರಾರಂಭಿಸಿ ಬಟನ್ ನೋಡಿ",
        "calibration_diagram": "ಕ್ಯಾಲಿಬ್ರೇಶನ್ — 9 ಚುಕ್ಕಿಗಳು",
        "verification_diagram": "ಪರಿಶೀಲನೆ — 5 ಚುಕ್ಕಿಗಳು",
        "emergency_help": "ಆಯ್ಕೆಯನ್ನು ನೋಡಿ, ಆಯ್ಕೆ ಮಾಡಲು ದೃಷ್ಟಿಯನ್ನು ಸ್ಥಿರವಾಗಿ ಇರಿಸಿ.",
        "emergency_help_speech": "ಆಯ್ಕೆಯನ್ನು ನೋಡಿ. ವೃತ್ತ ಪೂರ್ಣವಾಗುವವರೆಗೆ ದೃಷ್ಟಿಯನ್ನು ಅಲ್ಲೇ ಇರಿಸಿ.",
        "keyboard_help": "ಅಕ್ಷರದ ಪ್ರದೇಶವನ್ನು ನೋಡಿ. ದೊಡ್ಡದಾದ ಅಕ್ಷರಗಳಲ್ಲಿ ಆಯ್ಕೆ ಮಾಡಿ.",
        "keyboard_help_speech": "ಬೇಕಾದ ವಾಕ್ಯವನ್ನು ಆಯ್ಕೆ ಮಾಡಿ. ನಿಮ್ಮದೇ ಸಂದೇಶ ಬರೆಯಲು ಟೈಪ್ ಮಾಡಿ ಆಯ್ಕೆಮಾಡಿ.",
        "startup_speech": "Welcome to GazeLink. Choose your language.",
        "english_selected_speech": "English selected.",
        "kannada_selected_speech": "ಗೇಜ್‌ಲಿಂಕ್‌ಗೆ ಸ್ವಾಗತ.",
        "voice_unavailable": "ಆಯ್ಕೆ ಮಾಡಿದ ಧ್ವನಿ ಲಭ್ಯವಿಲ್ಲ.",
    },
}


EMERGENCY_PHRASES = {
    LANG_ENGLISH: {
        "HELP": {"display": "HELP", "speech": "I need help."},
        "WATER": {"display": "WATER", "speech": "I need water."},
        "FOOD": {"display": "FOOD", "speech": "I need food."},
        "TOILET": {"display": "TOILET", "speech": "I need to use the toilet."},
        "PAIN": {"display": "PAIN", "speech": "I am in pain."},
    },
    LANG_KANNADA: {
        "HELP": {"display": "ಸಹಾಯ", "speech": "ನನಗೆ ಸಹಾಯ ಬೇಕು"},
        "WATER": {"display": "ನೀರು", "speech": "ನನಗೆ ನೀರು ಬೇಕು"},
        "FOOD": {"display": "ಆಹಾರ", "speech": "ನನಗೆ ಆಹಾರ ಬೇಕು"},
        "TOILET": {"display": "ಶೌಚಾಲಯ", "speech": "ನಾನು ಶೌಚಾಲಯಕ್ಕೆ ಹೋಗಬೇಕು"},
        "PAIN": {"display": "ನೋವು", "speech": "ನನಗೆ ನೋವಾಗಿದೆ"},
    },
}


KANNADA_KEY_GROUPS = {
    "vowels": (
        ("vowels_1", ("ಅ", "ಆ", "ಇ", "ಈ")),
        ("vowels_2", ("ಉ", "ಊ", "ಋ", "ಎ")),
        ("vowels_3", ("ಏ", "ಐ", "ಒ", "ಓ", "ಔ")),
    ),
    "consonants": (
        ("consonants_1", ("ಕ", "ಖ", "ಗ", "ಘ", "ಙ")),
        ("consonants_2", ("ಚ", "ಛ", "ಜ", "ಝ", "ಞ")),
        ("consonants_3", ("ಟ", "ಠ", "ಡ", "ಢ", "ಣ")),
        ("consonants_4", ("ತ", "ಥ", "ದ", "ಧ", "ನ")),
        ("consonants_5", ("ಪ", "ಫ", "ಬ", "ಭ", "ಮ")),
        ("consonants_6", ("ಯ", "ರ", "ಲ", "ವ")),
        ("consonants_7", ("ಶ", "ಷ", "ಸ", "ಹ", "ಳ")),
    ),
    "signs": (
        ("signs_1", ("ಾ", "ಿ", "ೀ", "ು")),
        ("signs_2", ("ೂ", "ೃ", "ೆ", "ೇ")),
        ("signs_3", ("ೈ", "ೊ", "ೋ", "ೌ", "್")),
    ),
    "common_words": (
        ("words_1", ("ಹೌದು", "ಇಲ್ಲ", "ಬೇಕು", "ಬೇಡ")),
        ("words_2", ("ನೀರು", "ಆಹಾರ", "ನೋವು", "ಸಹಾಯ")),
        ("words_3", ("ಮನೆ", "ವೈದ್ಯರು", "ಔಷಧಿ", "ಕುಟುಂಬ")),
        ("words_4", ("ಅಮ್ಮ", "ಅಪ್ಪ", "ಧನ್ಯವಾದ", "ನಮಸ್ಕಾರ")),
    ),
}


KANNADA_PHRASE_GROUPS = {
    "basic_needs": (
        "ನನಗೆ ನೀರು ಬೇಕು",
        "ನನಗೆ ಆಹಾರ ಬೇಕು",
        "ನಾನು ಶೌಚಾಲಯಕ್ಕೆ ಹೋಗಬೇಕು",
        "ನನಗೆ ವಿಶ್ರಾಂತಿ ಬೇಕು",
        "ನನಗೆ ನಿದ್ರೆ ಬರುತ್ತಿದೆ",
        "ನಾನು ಮನೆಗೆ ಹೋಗಬೇಕು",
    ),
    "health_pain": (
        "ನನಗೆ ನೋವಾಗಿದೆ",
        "ನನಗೆ ತುಂಬಾ ನೋವಾಗಿದೆ",
        "ನನಗೆ ತಲೆ ಸುತ್ತುತ್ತಿದೆ",
        "ನನಗೆ ಉಸಿರಾಟ ಕಷ್ಟವಾಗಿದೆ",
        "ನನಗೆ ಅಸ್ವಸ್ಥವಾಗಿದೆ",
        "ವೈದ್ಯರನ್ನು ಕರೆಸಿ",
        "ನನಗೆ ಔಷಧಿ ಬೇಕು",
    ),
    "responses": (
        "ಹೌದು",
        "ಇಲ್ಲ",
        "ಸರಿ",
        "ಬೇಡ",
        "ಬೇಕು",
        "ಸ್ವಲ್ಪ ಕಾಯಿರಿ",
        "ಮತ್ತೆ ಹೇಳಿ",
        "ನನಗೆ ಅರ್ಥವಾಗಲಿಲ್ಲ",
    ),
    "help_people": (
        "ನನಗೆ ಸಹಾಯ ಬೇಕು",
        "ಅಮ್ಮನನ್ನು ಕರೆಸಿ",
        "ಅಪ್ಪನನ್ನು ಕರೆಸಿ",
        "ಕುಟುಂಬದವರನ್ನು ಕರೆಸಿ",
        "ವೈದ್ಯರನ್ನು ಕರೆಸಿ",
        "ನರ್ಸ್ ಅವರನ್ನು ಕರೆಸಿ",
    ),
}


KANNADA_PRIMARY_PHRASES = (
    "ನನಗೆ ಚಳಿ ಆಗುತ್ತಿದೆ",
    "ನನಗೆ ಬಿಸಿ ಆಗುತ್ತಿದೆ",
    "ಸ್ವಲ್ಪ ನಿಧಾನವಾಗಿ ಮಾತನಾಡಿ",
    "ಮತ್ತೆ ಹೇಳಿ",
    "ನಾನು ದಣಿದಿದ್ದೇನೆ",
    "ನನಗೆ ವಿಶ್ರಾಂತಿ ಬೇಕು",
    "ನನ್ನನ್ನು ಸ್ವಲ್ಪ ತಿರುಗಿಸಿ",
    "ನನಗೆ ಕುಳಿತುಕೊಳ್ಳಲು ಸಹಾಯ ಮಾಡಿ",
    "ನನಗೆ ಅರ್ಥವಾಗಲಿಲ್ಲ",
    "ಒಂದು ಕ್ಷಣ ಕಾಯಿರಿ",
    "ಹೌದು",
    "ಇಲ್ಲ",
)


KANNADA_MORE_PHRASES = (
    "ಟಿವಿ ಆನ್ ಮಾಡಿ",
    "ಟಿವಿ ಆಫ್ ಮಾಡಿ",
    "ಫ್ಯಾನ್ ಆನ್ ಮಾಡಿ",
    "ಫ್ಯಾನ್ ಆಫ್ ಮಾಡಿ",
    "ಲೈಟ್ ಆನ್ ಮಾಡಿ",
    "ಲೈಟ್ ಆಫ್ ಮಾಡಿ",
    "ನನ್ನ ಕುಟುಂಬವನ್ನು ಕರೆಸಿ",
    "ನರ್ಸ್ ಅವರನ್ನು ಕರೆಸಿ",
    "ನಾನು ಮಾತನಾಡಲು ಬಯಸುತ್ತೇನೆ",
    "ಧನ್ಯವಾದಗಳು",
)


KANNADA_READINESS_COMPONENTS = {
    "score_intro": "ನಿಮ್ಮ ದೃಷ್ಟಿ ಸಿದ್ಧತೆ ಅಂಕ",
    "out_of_100": "ನೂರರಲ್ಲಿ.",
    "category_excellent": "ಇದು ಅತ್ಯುತ್ತಮ ಫಲಿತಾಂಶ.",
    "category_good": "ಇದು ಉತ್ತಮ ಫಲಿತಾಂಶ.",
    "category_emergency": "ಈ ಫಲಿತಾಂಶ ತುರ್ತು ಸಂವಹನಕ್ಕೆ ಸಿದ್ಧವಾಗಿದೆ.",
    "category_poor": "ಸಾಮಾನ್ಯ ಬಳಕೆಗೆ ಮೊದಲು ಕ್ಯಾಲಿಬ್ರೇಶನ್ ಸುಧಾರಿಸಬೇಕು.",
    "emergency_ready": "ತುರ್ತು ಸಂವಹನ ಬಳಸಲು ಸಿದ್ಧವಾಗಿದೆ.",
    "typing_ready": "ಅಕ್ಷರ ಟೈಪಿಂಗ್ ಬಳಸಲು ಸಿದ್ಧವಾಗಿದೆ.",
    "typing_slower": "ಅಕ್ಷರ ಟೈಪಿಂಗ್ ಕೆಲಸ ಮಾಡುತ್ತದೆ, ಆದರೆ ಕೆಲವು ಆಯ್ಕೆಗಳಿಗೆ ಹೆಚ್ಚು ಸಮಯ ಬೇಕಾಗಬಹುದು.",
    "typing_difficult": "ಅಕ್ಷರ ಟೈಪಿಂಗ್ ಕಷ್ಟವಾಗಬಹುದು.",
    "region_left": "ಪರದೆಯ ಎಡಭಾಗದ ನಿಖರತೆಯನ್ನು ಸುಧಾರಿಸಬಹುದು.",
    "region_center": "ಪರದೆಯ ಮಧ್ಯಭಾಗದ ನಿಖರತೆಯನ್ನು ಸುಧಾರಿಸಬಹುದು.",
    "region_right": "ಪರದೆಯ ಬಲಭಾಗದ ನಿಖರತೆಯನ್ನು ಸುಧಾರಿಸಬಹುದು.",
    "region_top": "ಪರದೆಯ ಮೇಲ್ಭಾಗದ ನಿಖರತೆಯನ್ನು ಸುಧಾರಿಸಬಹುದು.",
    "region_bottom": "ಪರದೆಯ ಕೆಳಭಾಗದ ನಿಖರತೆಯನ್ನು ಸುಧಾರಿಸಬಹುದು.",
    "improved_intro": "ನಿಮ್ಮ ಸಿದ್ಧತೆ",
    "improved_from": "ರಿಂದ",
    "improved_end": "ಕ್ಕೆ ಸುಧಾರಿಸಿದೆ.",
    "previous_kept": "ಹಿಂದಿನ ಉತ್ತಮ ಕ್ಯಾಲಿಬ್ರೇಶನ್ ಉಳಿಸಲಾಗಿದೆ.",
}


def get_kannada_number_word(value: int) -> str:
    """Return a patient-friendly Kannada word for an integer from 0 to 100."""
    number = max(0, min(100, int(value)))
    direct = {
        0: "ಸೊನ್ನೆ", 1: "ಒಂದು", 2: "ಎರಡು", 3: "ಮೂರು", 4: "ನಾಲ್ಕು",
        5: "ಐದು", 6: "ಆರು", 7: "ಏಳು", 8: "ಎಂಟು", 9: "ಒಂಬತ್ತು",
        10: "ಹತ್ತು", 11: "ಹನ್ನೊಂದು", 12: "ಹನ್ನೆರಡು", 13: "ಹದಿಮೂರು",
        14: "ಹದಿನಾಲ್ಕು", 15: "ಹದಿನೈದು", 16: "ಹದಿನಾರು",
        17: "ಹದಿನೇಳು", 18: "ಹದಿನೆಂಟು", 19: "ಹತ್ತೊಂಬತ್ತು",
        20: "ಇಪ್ಪತ್ತು", 30: "ಮೂವತ್ತು", 40: "ನಲವತ್ತು", 50: "ಐವತ್ತು",
        60: "ಅರವತ್ತು", 70: "ಎಪ್ಪತ್ತು", 80: "ಎಂಬತ್ತು", 90: "ತೊಂಬತ್ತು",
        100: "ನೂರು",
    }
    if number in direct:
        return direct[number]
    tens_stem = {
        2: "ಇಪ್ಪತ್ತ", 3: "ಮೂವತ್ತ", 4: "ನಲವತ್ತ", 5: "ಐವತ್ತ",
        6: "ಅರವತ್ತ", 7: "ಎಪ್ಪತ್ತ", 8: "ಎಂಬತ್ತ", 9: "ತೊಂಬತ್ತ",
    }[number // 10]
    unit_suffix = {
        1: "ೊಂದು", 2: "ೆರಡು", 3: "ಮೂರು", 4: "ನಾಲ್ಕು", 5: "ೈದು",
        6: "ಾರು", 7: "ೇಳು", 8: "ೆಂಟು", 9: "ೊಂಬತ್ತು",
    }[number % 10]
    return tens_stem + unit_suffix


def get_kannada_readiness_speech_parts(
    *,
    score: int,
    level: str,
    weak_region: str | None = None,
    previous_score: int | None = None,
    improved: bool = False,
    previous_kept: bool = False,
) -> tuple[str, ...]:
    """Build a readiness narration from short, cacheable Kannada pieces."""
    numeric_score = max(0, min(100, int(score)))
    parts = [
        KANNADA_READINESS_COMPONENTS["score_intro"],
        get_kannada_number_word(numeric_score),
        KANNADA_READINESS_COMPONENTS["out_of_100"],
    ]
    normalized_level = str(level).upper()
    if normalized_level == "EXCELLENT":
        parts.extend((
            KANNADA_READINESS_COMPONENTS["category_excellent"],
            KANNADA_READINESS_COMPONENTS["emergency_ready"],
            KANNADA_READINESS_COMPONENTS["typing_ready"],
        ))
    elif normalized_level == "GOOD":
        parts.extend((
            KANNADA_READINESS_COMPONENTS["category_good"],
            KANNADA_READINESS_COMPONENTS["emergency_ready"],
            KANNADA_READINESS_COMPONENTS["typing_slower"],
        ))
    elif normalized_level == "EMERGENCY READY":
        parts.extend((
            KANNADA_READINESS_COMPONENTS["category_emergency"],
            KANNADA_READINESS_COMPONENTS["emergency_ready"],
            KANNADA_READINESS_COMPONENTS["typing_difficult"],
        ))
    else:
        parts.append(KANNADA_READINESS_COMPONENTS["category_poor"])
    region_key = f"region_{weak_region}" if weak_region else ""
    if region_key in KANNADA_READINESS_COMPONENTS:
        parts.append(KANNADA_READINESS_COMPONENTS[region_key])
    if improved and previous_score is not None:
        parts.extend((
            KANNADA_READINESS_COMPONENTS["improved_intro"],
            get_kannada_number_word(previous_score),
            KANNADA_READINESS_COMPONENTS["improved_from"],
            get_kannada_number_word(numeric_score),
            KANNADA_READINESS_COMPONENTS["improved_end"],
        ))
    elif previous_kept:
        parts.append(KANNADA_READINESS_COMPONENTS["previous_kept"])
    return tuple(parts)


class LanguageManager:
    """Own the selected language and all patient-facing translations."""

    def __init__(self, settings_path: str | Path | None = None) -> None:
        self.settings_path = (
            Path(settings_path)
            if settings_path is not None
            else get_data_path(config.USER_SETTINGS_PATH)
        )
        self._language = LANG_ENGLISH
        self.load()

    def set_language(self, code: str, *, persist: bool = True) -> bool:
        normalized = str(code).strip().lower()
        if normalized not in SUPPORTED_LANGUAGES:
            return False
        self._language = normalized
        if persist:
            self.save()
        return True

    def get_language(self) -> str:
        return self._language

    def translate_ui(self, key: str) -> str:
        normalized = str(key).strip().lower()
        return TRANSLATIONS[self._language].get(
            normalized,
            TRANSLATIONS[LANG_ENGLISH].get(normalized, str(key)),
        )

    def get_text(self, key: str) -> str:
        """Return one translated patient-facing UI string."""
        return self.translate_ui(key)

    def get_instruction(
        self, screen_name: str, language: str | None = None
    ) -> str:
        """Return the exact string shared by the visual and spoken guidance."""
        code = language or self._language
        if code not in SUPPORTED_LANGUAGES:
            code = LANG_ENGLISH
        return INSTRUCTIONS[code].get(str(screen_name).strip().lower(), "")

    def get_emergency_phrase(self, key: str) -> dict[str, str]:
        normalized = str(key).strip().upper()
        phrase = EMERGENCY_PHRASES[self._language].get(normalized)
        if phrase is None:
            phrase = EMERGENCY_PHRASES[LANG_ENGLISH].get(
                normalized, {"display": normalized, "speech": normalized.title()}
            )
        return dict(phrase)

    def format_verification_status(self, index: int, total: int) -> str:
        """Return a short patient-facing verification progress message."""
        if self._language == LANG_KANNADA:
            return f"ಕ್ಯಾಲಿಬ್ರೇಶನ್ ನಿಖರತೆ ಪರಿಶೀಲನೆ — {index} / {total}"
        return f"Checking calibration accuracy — {index} of {total}"

    def get_emergency_display(self, key: str) -> str:
        return self.get_emergency_phrase(key)["display"]

    def get_emergency_speech(self, key: str) -> str:
        return self.get_emergency_phrase(key)["speech"]

    def save(self) -> bool:
        try:
            self.settings_path.write_text(
                json.dumps({"language": self._language}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except OSError as exc:
            print(f"Could not save language preference: {exc}")
            get_logger().warning("language preference save failed: %s", exc)
            return False

    def save_preference(self) -> bool:
        return self.save()

    def load(self) -> bool:
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
            language = str(payload.get("language", LANG_ENGLISH)).lower()
            if language not in SUPPORTED_LANGUAGES:
                return False
            self._language = language
            return True
        except (OSError, ValueError, TypeError) as exc:
            if self.settings_path.exists():
                get_logger().warning("language preference load failed: %s", exc)
            return False

    def load_preference(self) -> bool:
        return self.load()


_DEFAULT_MANAGER: LanguageManager | None = None


def get_default_language_manager() -> LanguageManager:
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = LanguageManager()
    return _DEFAULT_MANAGER


def get_fixed_kannada_speech_texts() -> tuple[str, ...]:
    """Return fixed Kannada content suitable for persistent WAV caching."""
    return tuple(text for _speech_id, text in get_fixed_kannada_speech_items())


def get_fixed_kannada_speech_items() -> tuple[tuple[str, str], ...]:
    """Return stable cache IDs and their exact predefined Kannada text."""
    items: list[tuple[str, str]] = []
    seen_text: set[str] = set()

    def add(speech_id: str, text: str) -> None:
        normalized = text.strip()
        if not normalized or normalized in seen_text:
            return
        seen_text.add(normalized)
        items.append((speech_id, normalized))

    instruction_ids = {
        "welcome": "kn_welcome",
        "calibration": "kn_calibration_intro",
        "calibration_progress": "kn_calibration_progress",
        "calibration_verification": "kn_verification_intro",
        "calibration_complete": "kn_calibration_complete",
        "emergency": "kn_emergency_instruction",
        "keyboard": "kn_phrase_instruction",
        "keyboard_custom": "kn_custom_type_instruction",
    }
    for key, speech_id in instruction_ids.items():
        add(speech_id, INSTRUCTIONS[LANG_KANNADA][key])
    for key, speech_id in (
        ("kannada_selected_speech", "kn_language_selected"),
        ("emergency_help_speech", "kn_emergency_help_legacy"),
        ("keyboard_help_speech", "kn_phrase_help_legacy"),
    ):
        add(speech_id, TRANSLATIONS[LANG_KANNADA][key])

    emergency_ids = {
        "HELP": "kn_help",
        "WATER": "kn_water",
        "FOOD": "kn_food",
        "TOILET": "kn_toilet",
        "PAIN": "kn_pain",
    }
    for key, speech_id in emergency_ids.items():
        add(speech_id, EMERGENCY_PHRASES[LANG_KANNADA][key]["speech"])

    for index, phrase in enumerate(KANNADA_PRIMARY_PHRASES, start=1):
        add(f"kn_phrase_primary_{index:02d}", phrase)
    for index, phrase in enumerate(KANNADA_MORE_PHRASES, start=1):
        add(f"kn_phrase_more_{index:02d}", phrase)
    for category, phrases in KANNADA_PHRASE_GROUPS.items():
        for index, phrase in enumerate(phrases, start=1):
            add(f"kn_phrase_{category}_{index:02d}", phrase)
    for component, text in KANNADA_READINESS_COMPONENTS.items():
        add(f"kn_readiness_{component}", text)
    for score in range(101):
        add(f"kn_score_{score:03d}", get_kannada_number_word(score))
    return tuple(items)
