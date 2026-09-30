import re
import unicodedata
from collections.abc import Iterable

try:
    from ..utils.language_manager import LANG_ENGLISH, LANG_KANNADA
except ImportError:
    from utils.language_manager import LANG_ENGLISH, LANG_KANNADA


BUILT_IN_WORDS = (
    "Help",
    "Hello",
    "Hi",
    "Water",
    "Food",
    "Toilet",
    "Pain",
    "Yes",
    "No",
    "Medicine",
    "Doctor",
    "Nurse",
    "Family",
    "Emergency",
    "Please",
    "Thank",
    "Thanks",
    "Sorry",
    "Need",
    "Want",
    "Feel",
    "Feeling",
    "Home",
    "Hospital",
    "Call",
    "Mother",
    "Father",
    "Brother",
    "Sister",
    "Friend",
    "Hungry",
    "Thirsty",
    "Uncomfortable",
    "Cold",
    "Hot",
    "Good",
    "Bad",
    "Better",
    "Worse",
    "Stop",
    "Start",
    "Sleep",
    "Speak",
    "Breathing",
    "Headache",
    "Chest",
    "Stomach",
    "Washroom",
    "Blanket",
    "Wheelchair",
    "I",
    "You",
    "We",
    "They",
    "He",
    "She",
    "It",
    "Me",
    "My",
    "Your",
    "Our",
    "Their",
    "This",
    "That",
    "These",
    "Those",
    "Here",
    "There",
    "Who",
    "What",
    "Where",
    "When",
    "Why",
    "How",
    "Am",
    "Is",
    "Are",
    "Was",
    "Were",
    "Be",
    "Have",
    "Has",
    "Had",
    "Do",
    "Does",
    "Did",
    "Can",
    "Could",
    "Will",
    "Would",
    "Should",
    "May",
    "Might",
    "The",
    "A",
    "An",
    "And",
    "Or",
    "But",
    "If",
    "Because",
    "With",
    "Without",
    "For",
    "From",
    "To",
    "In",
    "On",
    "At",
    "By",
    "Near",
    "Up",
    "Down",
    "Left",
    "Right",
    "Inside",
    "Outside",
    "Today",
    "Tomorrow",
    "Yesterday",
    "Morning",
    "Afternoon",
    "Evening",
    "Night",
    "Now",
    "Later",
    "Soon",
    "Before",
    "After",
    "Come",
    "Go",
    "Sit",
    "Stand",
    "Wait",
    "Stay",
    "Leave",
    "Bring",
    "Give",
    "Take",
    "Put",
    "Open",
    "Close",
    "Turn",
    "Move",
    "Lift",
    "Lower",
    "Eat",
    "Drink",
    "Rest",
    "Work",
    "Read",
    "Write",
    "Listen",
    "See",
    "Look",
    "Hear",
    "Understand",
    "Know",
    "Remember",
    "Forget",
    "Happy",
    "Sad",
    "Angry",
    "Afraid",
    "Scared",
    "Tired",
    "Sick",
    "Dizzy",
    "Weak",
    "Strong",
    "Comfortable",
    "Safe",
    "Unsafe",
    "Bed",
    "Chair",
    "Room",
    "Bathroom",
    "Door",
    "Window",
    "Light",
    "Fan",
    "Phone",
    "Television",
    "Air",
    "Ambulance",
    "Appointment",
    "Blood",
    "Cough",
    "Fever",
    "Infection",
    "Injury",
    "Oxygen",
    "Allergy",
    "Allergies",
    "Diabetes",
    "Heart",
    "Sugar",
    "Pressure",
    "Nausea",
    "Vomit",
    "Swelling",
    "Bleeding",
    "Burning",
    "Itching",
    "Back",
    "Neck",
    "Head",
    "Arm",
    "Hand",
    "Leg",
    "Foot",
    "Eye",
    "Ear",
    "Mouth",
    "More",
    "Less",
    "Again",
    "Done",
    "Ready",
    "Okay",
    "Maybe",
    "Someone",
    "Something",
    "Name",
    "Number",
    "Address",
    "Child",
    "Son",
    "Daughter",
    "Husband",
    "Wife",
    "Tea",
    "Coffee",
    "Milk",
    "Juice",
    "Soup",
    "Rice",
    "Bread",
    "Breakfast",
    "Lunch",
    "Dinner",
    "Small",
    "Large",
    "Fast",
    "Slow",
    "Quiet",
    "Loud",
    "Clean",
    "Dirty",
    "Dry",
    "Wet",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
)


ASSISTIVE_WORDS = frozenset(
    word.casefold()
    for word in (
        "Help",
        "Hello",
        "Hi",
        "Water",
        "Food",
        "Toilet",
        "Pain",
        "Yes",
        "No",
        "Medicine",
        "Doctor",
        "Nurse",
        "Family",
        "Emergency",
        "Please",
        "Thank",
        "Thanks",
        "Sorry",
        "Need",
        "Want",
        "Feel",
        "Feeling",
        "Home",
        "Hospital",
        "Call",
        "Mother",
        "Father",
        "Brother",
        "Sister",
        "Friend",
        "Hungry",
        "Thirsty",
        "Uncomfortable",
        "Cold",
        "Hot",
        "Good",
        "Bad",
        "Better",
        "Worse",
        "Stop",
        "Start",
        "Sleep",
        "Speak",
        "Breathing",
        "Headache",
        "Chest",
        "Stomach",
        "Washroom",
        "Blanket",
        "Wheelchair",
        "Ambulance",
        "Blood",
        "Cough",
        "Fever",
        "Injury",
        "Oxygen",
        "Allergy",
        "Dizzy",
        "Sick",
        "Tired",
    )
)

KANNADA_WORDS = (
    "ನಮಸ್ಕಾರ", "ನೀರು", "ಆಹಾರ", "ಸಹಾಯ", "ನೋವು", "ಬೇಕು", "ಬೇಡ",
    "ಹೌದು", "ಇಲ್ಲ", "ಮನೆ", "ವೈದ್ಯರು", "ಔಷಧಿ", "ಕುಟುಂಬ", "ಅಮ್ಮ", "ಅಪ್ಪ",
    "ತಾಯಿ", "ತಂದೆ", "ಅಣ್ಣ", "ಅಕ್ಕ", "ತಮ್ಮ", "ತಂಗಿ", "ಸ್ನೇಹಿತ", "ನರ್ಸ್",
    "ಆಸ್ಪತ್ರೆ", "ತುರ್ತು", "ದಯವಿಟ್ಟು", "ಧನ್ಯವಾದ", "ಕ್ಷಮಿಸಿ", "ಕರೆ", "ಬನ್ನಿ",
    "ಹೋಗಿ", "ನಿಲ್ಲಿಸಿ", "ಮತ್ತೆ", "ಈಗ", "ನಂತರ", "ಇಲ್ಲಿ", "ಅಲ್ಲಿ", "ಒಳ್ಳೆಯದು",
    "ಕೆಟ್ಟದು", "ಚಳಿ", "ಬಿಸಿ", "ಹಸಿವು", "ಬಾಯಾರಿಕೆ", "ನಿದ್ರೆ", "ಉಸಿರು",
    "ತಲೆನೋವು", "ಹೊಟ್ಟೆ", "ಎದೆ", "ಕೈ", "ಕಾಲು", "ಕುಳಿತು", "ಮಲಗಬೇಕು",
    "ಹೊದಿಕೆ", "ಗಾಳಿ", "ಬೆಳಕು", "ಫೋನ್", "ಶೌಚಾಲಯ", "ಸರಿ", "ಬಹುಶಃ",
    "ನನಗೆ", "ನೀವು", "ನಾನು", "ನಾವು", "ಅವರು", "ಅವನು", "ಅವಳು", "ಇದು",
    "ಅದು", "ಯಾರು", "ಏನು", "ಎಲ್ಲಿ", "ಯಾವಾಗ", "ಏಕೆ", "ಹೇಗೆ", "ಎಷ್ಟು",
    "ಇಂದು", "ನಾಳೆ", "ನಿನ್ನೆ", "ಬೆಳಗ್ಗೆ", "ಮಧ್ಯಾಹ್ನ", "ಸಂಜೆ", "ರಾತ್ರಿ", "ಬೇಗ",
    "ನಿಧಾನ", "ಕಾಯಿರಿ", "ಬನ್ನಿರಿ", "ಹೋಗಬೇಕು", "ಕೊಡಿ", "ತೆಗೆದುಕೊಳ್ಳಿ",
    "ತೆರೆಯಿರಿ", "ಮುಚ್ಚಿರಿ", "ತಿರುಗಿಸಿ", "ಎತ್ತಿ", "ಇಳಿಸಿ", "ತಿನ್ನಬೇಕು",
    "ಕುಡಿಯಬೇಕು", "ವಿಶ್ರಾಂತಿ", "ಮಾತನಾಡಿ", "ಕೇಳಿ", "ನೋಡಿ", "ಅರ್ಥವಾಗಿದೆ",
    "ಗೊತ್ತಿಲ್ಲ", "ಸಂತೋಷ", "ದುಃಖ", "ಭಯ", "ಕೋಪ", "ಆಯಾಸ", "ಅನಾರೋಗ್ಯ",
    "ತಲೆಸುತ್ತು", "ದುರ್ಬಲ", "ಆರಾಮ", "ಅನಾನುಕೂಲ", "ಸುರಕ್ಷಿತ", "ಅಪಾಯ",
    "ಹಾಸಿಗೆ", "ಕುರ್ಚಿ", "ಕೊಠಡಿ", "ಬಾಗಿಲು", "ಕಿಟಕಿ", "ಫ್ಯಾನ್", "ದೂರದರ್ಶನ",
    "ಆಂಬುಲೆನ್ಸ್", "ಆಮ್ಲಜನಕ", "ಜ್ವರ", "ಕೆಮ್ಮು", "ಗಾಯ", "ರಕ್ತ", "ಊತ",
    "ಉರಿ", "ತುರಿಕೆ", "ಅಲರ್ಜಿ", "ಸಕ್ಕರೆ", "ಒತ್ತಡ", "ಹೃದಯ", "ಕತ್ತು",
    "ತಲೆ", "ಬಾಯಿ", "ಕಣ್ಣು", "ಕಿವಿ", "ಬೆನ್ನು", "ಬೆರಳು", "ಹೆಚ್ಚು", "ಕಡಿಮೆ",
    "ಮುಗಿಯಿತು", "ಸಿದ್ಧ", "ಯಾರಾದರೂ", "ಮಗು", "ಮಗ", "ಮಗಳು", "ಗಂಡ", "ಹೆಂಡತಿ",
    "ಅಕ್ಕಿ", "ಹಾಲು", "ಚಹಾ", "ಕಾಫಿ", "ರಸ", "ಸೂಪ್", "ಉಪಹಾರ", "ಊಟ",
    "ಸ್ವಚ್ಛ", "ಕೊಳಕು", "ಒಣ", "ಒದ್ದೆ", "ಶಾಂತ", "ಜೋರಾಗಿ", "ಎಡ", "ಬಲ",
)

KANNADA_ASSISTIVE_WORDS = frozenset(
    word.casefold()
    for word in (
        "ನೀರು", "ಆಹಾರ", "ಸಹಾಯ", "ನೋವು", "ಬೇಕು", "ಬೇಡ", "ಹೌದು", "ಇಲ್ಲ",
        "ವೈದ್ಯರು", "ಔಷಧಿ", "ನರ್ಸ್", "ಆಸ್ಪತ್ರೆ", "ತುರ್ತು", "ಶೌಚಾಲಯ",
    )
)


class WordPredictor:
    """Small language-specific offline predictor for assistive text."""

    _VALID_WORD = re.compile(r"^[A-Za-z]+(?:['-][A-Za-z]+)*$")
    _ENDING_WORD = re.compile(r"([A-Za-z]+(?:['-][A-Za-z]*)*)$")

    def __init__(
        self,
        words: list[str] | None = None,
        max_suggestions: int = 5,
        language: str = LANG_ENGLISH,
    ) -> None:
        self.max_suggestions = self._clamp_suggestion_count(max_suggestions)
        self._words: dict[str, str] = {}
        self._custom_words: set[str] = set()
        self.language = (
            language if language in {LANG_ENGLISH, LANG_KANNADA} else LANG_ENGLISH
        )
        source_words = self._built_in_words() if words is None else words
        for word in self._safe_iterable(source_words):
            normalized = self._normalize_word(word)
            if normalized is None:
                continue
            key, display_word = normalized
            self._words.setdefault(key, display_word)

        self._sort_words()

    def predict(self, text: str) -> list[str]:
        """Return prioritized completions for the final incomplete word."""
        prefix = self.get_current_prefix(text)
        if not prefix:
            return []

        normalized_prefix = prefix.casefold()
        assistive_words = (
            KANNADA_ASSISTIVE_WORDS
            if self.language == LANG_KANNADA
            else ASSISTIVE_WORDS
        )
        matches = [
            (key, display_word)
            for key, display_word in self._words.items()
            if key.startswith(normalized_prefix)
        ]
        matches.sort(
            key=lambda item: (
                0 if item[0] == normalized_prefix else 1,
                0 if item[0] in assistive_words else 1,
                len(item[1]),
                item[1].casefold(),
                item[1],
            )
        )
        return [word for _, word in matches[: self.max_suggestions]]

    def add_word(self, word: str) -> bool:
        """Add one valid, case-insensitively unique custom word."""
        normalized = self._normalize_word(word)
        if normalized is None:
            return False

        key, display_word = normalized
        if key in self._words:
            return False

        self._words[key] = display_word
        self._custom_words.add(key)
        self._sort_words()
        return True

    def add_words(self, words: list[str]) -> int:
        """Add multiple custom words and return the number accepted."""
        return sum(self.add_word(word) for word in self._safe_iterable(words))

    def remove_word(self, word: str) -> bool:
        """Remove a stored word using case-insensitive matching."""
        normalized = self._normalize_word(word)
        if normalized is None:
            return False

        key, _ = normalized
        if key not in self._words:
            return False

        del self._words[key]
        self._custom_words.discard(key)
        return True

    def get_current_prefix(self, text: str) -> str:
        """Extract the incomplete word at the end of a message."""
        if text is None or not isinstance(text, str) or not text:
            return ""
        if text[-1].isspace():
            return ""

        end = len(text)
        start = end
        while start > 0 and self._is_word_character(text[start - 1]):
            start -= 1
        prefix = text[start:end].strip("'-")
        return prefix if any(self._is_letter(char) for char in prefix) else ""

    def apply_suggestion(self, text: str, suggestion: str) -> str:
        """Replace the current prefix with a suggestion and add a space."""
        current_text = text if isinstance(text, str) else ""
        normalized = self._normalize_word(suggestion)
        if normalized is None:
            return current_text

        _, display_suggestion = normalized
        prefix = self.get_current_prefix(current_text)
        if prefix:
            return current_text[: -len(prefix)] + display_suggestion + " "
        if not current_text or current_text[-1].isspace():
            return current_text + display_suggestion + " "
        return current_text + " " + display_suggestion + " "

    def clear_custom_words(self) -> None:
        """Remove words added after construction without touching base words."""
        for key in self._custom_words:
            self._words.pop(key, None)
        self._custom_words.clear()
        self._sort_words()

    def word_count(self) -> int:
        """Return the number of unique words currently stored."""
        return len(self._words)

    def set_language(self, language: str) -> bool:
        """Switch built-in dictionaries while preserving the public instance."""
        normalized = str(language).strip().lower()
        if normalized not in {LANG_ENGLISH, LANG_KANNADA}:
            return False
        if normalized == self.language:
            return True
        self.language = normalized
        self._words.clear()
        self._custom_words.clear()
        for word in self._built_in_words():
            item = self._normalize_word(word)
            if item is not None:
                self._words.setdefault(*item)
        self._sort_words()
        return True

    def _normalize_word(self, word: object) -> tuple[str, str] | None:
        if not isinstance(word, str):
            return None
        display_word = word.strip()
        if not display_word:
            return None
        if self.language == LANG_ENGLISH:
            valid = self._VALID_WORD.fullmatch(display_word) is not None
        else:
            valid = all(
                self._is_word_character(char) for char in display_word
            ) and any(self._is_letter(char) for char in display_word)
        if not valid:
            return None
        return display_word.casefold(), display_word

    def _built_in_words(self) -> tuple[str, ...]:
        return KANNADA_WORDS if self.language == LANG_KANNADA else BUILT_IN_WORDS

    @staticmethod
    def _is_letter(character: str) -> bool:
        return unicodedata.category(character).startswith("L")

    @classmethod
    def _is_word_character(cls, character: str) -> bool:
        category = unicodedata.category(character)
        return category.startswith(("L", "M")) or character in "'-"

    @staticmethod
    def _safe_iterable(words: object) -> Iterable[object]:
        if words is None or isinstance(words, (str, bytes)):
            return ()
        try:
            return iter(words)
        except TypeError:
            return ()

    @staticmethod
    def _clamp_suggestion_count(value: int) -> int:
        try:
            count = int(value)
        except (TypeError, ValueError):
            count = 5
        return max(1, min(10, count))

    def _sort_words(self) -> None:
        self._words = dict(
            sorted(
                self._words.items(),
                key=lambda item: (item[1].casefold(), item[1]),
            )
        )


if __name__ == "__main__":
    predictor = WordPredictor()
    test_inputs = (
        "hel",
        "I need wat",
        "Please call doc",
        "I feel pai",
        "xyz",
        "",
        "I need water ",
    )

    for test_input in test_inputs:
        print(f"Input: {test_input!r}")
        print(f"Suggestions: {predictor.predict(test_input)}")

    replacement = predictor.apply_suggestion("I need wat", "Water")
    print(f"Applied suggestion: {replacement!r}")

    custom_word = "Gazelink"
    print(f"Added {custom_word!r}: {predictor.add_word(custom_word)}")
    print(f"Custom prediction: {predictor.predict('gaze')}")
    print(f"Removed {custom_word!r}: {predictor.remove_word(custom_word)}")
    print(f"Total word count: {predictor.word_count()}")

    assert len(predictor.predict("a")) <= 5
    assert "Water" in predictor.predict("I need wat")
    assert "Doctor" in predictor.predict("doc")
    assert predictor.predict("xyz") == []
    assert replacement == "I need Water "

    kannada = WordPredictor(language=LANG_KANNADA, max_suggestions=3)
    assert kannada.word_count() >= 100
    assert "ನೀರು" in kannada.predict("ನೀ")
    assert "ಸಹಾಯ" in kannada.predict("ಸಹಾ")
    assert "ನೋವು" in kannada.predict("ನೋ")
    assert kannada.apply_suggestion("ನನಗೆ ನೀ", "ನೀರು") == "ನನಗೆ ನೀರು "
    assert kannada.get_current_prefix("ನನಗೆ ನೀ") == "ನೀ"
    assert kannada.set_language(LANG_ENGLISH)
    assert "Water" in kannada.predict("wat")

    print("WordPredictor tests passed.")
