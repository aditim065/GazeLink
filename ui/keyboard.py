"""Two-mode gaze interface with automatic local QWERTY zoom."""

import math
import tkinter as tk
import tkinter.font as tkfont
import unicodedata

try:
    from .. import config
    from ..utils.language_manager import (
        KANNADA_KEY_GROUPS,
        KANNADA_PHRASE_GROUPS,
        KANNADA_MORE_PHRASES,
        KANNADA_PRIMARY_PHRASES,
        LANG_KANNADA,
        get_default_language_manager,
    )
except ImportError:
    import config
    from utils.language_manager import (
        KANNADA_KEY_GROUPS,
        KANNADA_PHRASE_GROUPS,
        KANNADA_MORE_PHRASES,
        KANNADA_PRIMARY_PHRASES,
        LANG_KANNADA,
        get_default_language_manager,
    )


class KeyboardScreen(tk.Tk):
    """Fullscreen emergency home and zoom-assisted QWERTY keyboard."""

    EMERGENCY_HOME = "EMERGENCY_HOME"
    KEYBOARD = "KEYBOARD"
    LANGUAGE_PICKER = "LANGUAGE_PICKER"

    # Compatibility aliases; they all resolve to the one keyboard page.
    KEYBOARD_GROUPS = KEYBOARD
    LETTER_GROUP = KEYBOARD
    SUGGESTIONS = KEYBOARD

    MAIN_BG = "#F5F7FA"
    KEY_BG = "#FFFFFF"
    KEY_BORDER = "#CDD6E3"
    KEY_TEXT = "#1A1A2A"
    KEY_HOVER = "#E8F0FE"
    BLUE = "#4A90D9"
    GREEN = "#22C55E"
    WHITE = "#FFFFFF"
    OUTPUT_BG = "#EAF0FF"
    EMERGENCY_BG = "#FF4757"
    EMERGENCY_HOVER = "#FF6B77"
    CONTROL_BG = "#315F9B"
    SUGGESTION_BG = "#EEF2FF"
    DISABLED_BG = "#E6EAF0"
    DISABLED_TEXT = "#9AA5B4"
    SHADOW = "#DDE3ED"

    OUTER_PADDING = 20
    GAP = 14
    CORNER_RADIUS = 16

    EMERGENCY_ACTIONS = ("HELP", "WATER", "FOOD", "TOILET", "PAIN")
    QWERTY_ROWS = (
        tuple("QWERTYUIOP"),
        tuple("ASDFGHJKL"),
        tuple("ZXCVBNM"),
    )
    ZOOM_REGIONS = {
        "UPPER_LEFT": ("Q", "W", "E"),
        "UPPER_MIDDLE": ("R", "T", "Y", "U"),
        "UPPER_RIGHT": ("I", "O", "P"),
        "MIDDLE_LEFT": ("A", "S", "D"),
        "MIDDLE_MIDDLE": ("F", "G", "H"),
        "MIDDLE_RIGHT": ("J", "K", "L"),
        "LOWER_LEFT": ("Z", "X", "C"),
        "LOWER_RIGHT": ("V", "B", "N", "M"),
    }
    REGION_ROWS = (
        ("UPPER_LEFT", "UPPER_MIDDLE", "UPPER_RIGHT"),
        ("MIDDLE_LEFT", "MIDDLE_MIDDLE", "MIDDLE_RIGHT"),
        ("LOWER_LEFT", "LOWER_RIGHT"),
    )

    @classmethod
    def calibration_target_tolerances(
        cls, screen_width: int, screen_height: int
    ) -> dict[str, float]:
        """Estimate safe centre-error tolerances from the actual layouts."""
        width, height = float(screen_width), float(screen_height)
        header = max(128.0, height * 0.16)
        usable_width = width - 2.0 * cls.OUTER_PADDING
        usable_height = height - cls.OUTER_PADDING - (header + cls.GAP)
        emergency_cell_width = (usable_width - cls.GAP) / 2.0
        emergency_cell_height = (usable_height - cls.GAP * 3.0) / 4.0
        emergency = 0.72 * min(emergency_cell_width, emergency_cell_height) / 2.0

        controls_height = max(205.0, usable_height * 0.34)
        zoom_height = usable_height - controls_height - cls.GAP - 48.0
        three_column_width = (usable_width - 2.0 * cls.GAP) / 3.0
        four_letter_width = (usable_width - cls.GAP) / 2.0
        four_letter_height = (zoom_height - cls.GAP) / 2.0
        typing = 0.68 * min(
            three_column_width,
            zoom_height,
            four_letter_width,
            four_letter_height,
        ) / 2.0
        return {
            "emergency_pixels": max(40.0, emergency),
            "typing_pixels": max(30.0, typing),
        }

    def __init__(self, on_action=None, language_manager=None):
        super().__init__()
        self.title("GazeLink")
        self.configure(bg=self.MAIN_BG)
        self.attributes("-fullscreen", True)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.on_action = on_action
        self.language_manager = language_manager or get_default_language_manager()
        self.mode = self.EMERGENCY_HOME
        self.zoom_region = None
        self.kannada_category = None
        self.kannada_zoom_group = None
        self.kannada_phrase_category = None
        self.kannada_custom_mode = False
        self.output_text = ""
        self.suggestions = ["", "", ""]

        self.targets = {}
        self.keys = self.targets
        self.output_text_id = None
        self.active_dwell_key = None
        self.highlighted_key = None
        self.dwell_progress = 0.0
        self.detected_horizontal_zone = None
        self._interactive_bounds = (0, 0, 0, 0)
        self._redraw_after_id = None
        self._standalone_tts = None
        self.tracking_status = ""
        self.tracking_status_id = None
        self.presentation_feedback = ""
        self.presentation_feedback_id = None

        # Compatibility attributes retained for older callers.
        self.emergency_bounds = []
        self.suggestion_bounds = []
        self.suggestion_items = []
        self.speak_bounds = None

        self.canvas = tk.Canvas(
            self,
            bg=self.MAIN_BG,
            highlightthickness=0,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self._handle_click)
        self.bind("<Configure>", self._schedule_redraw)
        self.font_family = self._select_font_family()
        self.after_idle(self._draw_screen)

    def _select_font_family(self):
        """Prefer a Windows font known to contain Kannada glyphs."""
        try:
            installed = set(tkfont.families(self))
        except tk.TclError:
            installed = set()
        for family in ("Nirmala UI", "Tunga", "Noto Sans Kannada", "Arial Unicode MS"):
            if family in installed:
                return family
        print("Kannada-capable font not confirmed; using Tkinter default font.")
        return "TkDefaultFont"

    def _font(self, size, weight="normal"):
        return (self.font_family, size, weight)

    def _t(self, key):
        return self.language_manager.translate_ui(key)

    def _schedule_redraw(self, _event=None):
        if self._redraw_after_id is not None:
            try:
                self.after_cancel(self._redraw_after_id)
            except tk.TclError:
                pass
        self._redraw_after_id = self.after(50, self._draw_screen)

    def _draw_screen(self):
        """Redraw the active mode while retaining visual dwell state."""
        self._redraw_after_id = None
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1:
            width = self.winfo_screenwidth()
        if height <= 1:
            height = self.winfo_screenheight()
        if width <= 1 or height <= 1:
            return

        active = self.active_dwell_key
        highlighted = self.highlighted_key
        progress = self.dwell_progress

        self.canvas.delete("interface")
        self.targets.clear()
        self.emergency_bounds.clear()
        self.suggestion_bounds.clear()
        self.suggestion_items.clear()
        self.speak_bounds = None

        header_bottom = max(128, int(height * 0.16))
        self._draw_output_header(width, header_bottom)
        left = self.OUTER_PADDING
        top = header_bottom + self.GAP
        right = width - self.OUTER_PADDING
        bottom = height - self.OUTER_PADDING
        self._interactive_bounds = (left, top, right, bottom)

        if self.mode == self.EMERGENCY_HOME:
            self._draw_emergency_home(left, top, right, bottom)
        elif self.mode == self.LANGUAGE_PICKER:
            self._draw_language_picker(left, top, right, bottom)
        else:
            self._draw_keyboard_page(left, top, right, bottom)

        self.active_dwell_key = active if active in self.targets else None
        self.highlighted_key = highlighted if highlighted in self.targets else None
        self.dwell_progress = progress if self.active_dwell_key else 0.0
        if self.highlighted_key is not None:
            self._apply_highlight(self.highlighted_key)
        if self.active_dwell_key is not None:
            self.set_dwell_progress(
                self.active_dwell_key,
                self.dwell_progress,
            )

    def _draw_output_header(self, width, bottom):
        self.canvas.create_rectangle(
            0,
            0,
            width,
            bottom,
            fill=self.OUTPUT_BG,
            outline=self.KEY_BORDER,
            width=2,
            tags="interface",
        )
        header_label = (
            self._t("language")
            if self.mode == self.LANGUAGE_PICKER
            else self._t("message")
        )
        self.canvas.create_text(
            22,
            14,
            anchor="nw",
            text=header_label,
            fill="#667085",
            font=self._font(14, "bold"),
            tags="interface",
        )
        shown_text = self.output_text or self._t("message_placeholder")
        message_right = width - 20
        message_width = max(180, message_right - 44)
        self.output_text_id = self.canvas.create_text(
            22,
            45,
            anchor="nw",
            text=shown_text,
            fill=self.KEY_TEXT if self.output_text else "#7A8494",
            font=self._font(27, "bold"),
            width=message_width,
            tags="interface",
        )
        self.tracking_status_id = None
        self.presentation_feedback_id = None
        if self.tracking_status:
            self.tracking_status_id = self.canvas.create_text(
                width - 22,
                14,
                anchor="ne",
                text=self.tracking_status,
                fill="#B42318",
                font=self._font(13, "bold"),
                width=max(260, int(width * 0.48)),
                tags="interface",
            )
        elif self.presentation_feedback:
            self.presentation_feedback_id = self.canvas.create_text(
                width - 22,
                14,
                anchor="ne",
                text=self.presentation_feedback,
                fill=self.BLUE,
                font=self._font(14, "bold"),
                width=max(260, int(width * 0.48)),
                tags="interface",
            )
        if self.mode in {self.EMERGENCY_HOME, self.KEYBOARD}:
            help_width = width - 44
            self.canvas.create_text(
                22,
                bottom - 8,
                anchor="sw",
                text=self.language_manager.get_instruction(
                    self._instruction_name()
                ),
                fill="#526072",
                font=self._font(14, "bold"),
                width=help_width,
                tags="interface",
            )

    def _instruction_name(self):
        """Return the one shared visual/spoken instruction for this UI state."""
        if self.mode == self.EMERGENCY_HOME:
            return "emergency"
        if (
            self.mode == self.KEYBOARD
            and self.language_manager.get_language() == LANG_KANNADA
            and self.kannada_custom_mode
        ):
            return "keyboard_custom"
        return "keyboard"

    def set_tracking_status(self, message: str) -> None:
        """Show a small non-selectable warning without changing gaze targets."""
        normalized = str(message or "").strip()
        if normalized == self.tracking_status:
            return
        self.tracking_status = normalized
        if self.tracking_status_id is not None:
            try:
                self.canvas.itemconfigure(
                    self.tracking_status_id,
                    text=normalized,
                    state=tk.NORMAL if normalized else tk.HIDDEN,
                )
                return
            except tk.TclError:
                pass
        self._schedule_redraw()

    def set_presentation_feedback(self, message: str) -> None:
        """Show non-selectable relative-direction feedback in presentation mode."""
        normalized = str(message or "").strip()
        if normalized == self.presentation_feedback:
            return
        self.presentation_feedback = normalized
        if self.presentation_feedback_id is not None:
            try:
                self.canvas.itemconfigure(
                    self.presentation_feedback_id,
                    text=normalized,
                    state=tk.NORMAL if normalized else tk.HIDDEN,
                )
                return
            except tk.TclError:
                pass
        self._schedule_redraw()

    def _draw_emergency_home(self, left, top, right, bottom):
        row_height = (bottom - top - self.GAP * 3) / 4
        column_width = (right - left - self.GAP) / 2

        emergency_items = [
            (
                self.language_manager.get_emergency_phrase(key)["display"],
                f"EMERGENCY:{key}",
            )
            for key in self.EMERGENCY_ACTIONS
        ]
        emergency_items.append((self._t("clear"), "CLEAR"))
        for index, (label, action) in enumerate(emergency_items):
            row, column = divmod(index, 2)
            x1 = left + column * (column_width + self.GAP)
            y1 = top + row * (row_height + self.GAP)
            bounds = (x1, y1, x1 + column_width, y1 + row_height)
            emergency = action.startswith("EMERGENCY:")
            self._draw_target(
                label,
                action,
                bounds,
                fill=self.EMERGENCY_BG if emergency else self.CONTROL_BG,
                text_color=self.WHITE,
                border=self.EMERGENCY_BG if emergency else self.CONTROL_BG,
                hover=self.EMERGENCY_HOVER if emergency else self.KEY_HOVER,
                font_size=34 if self.language_manager.get_language() == LANG_KANNADA else 38,
            )
            if emergency:
                self.emergency_bounds.append((*bounds, label))

        type_top = top + 3 * (row_height + self.GAP)
        self._draw_target(
            self._t("type"),
            "TYPE",
            (left, type_top, right, bottom),
            fill=self.BLUE,
            text_color=self.WHITE,
            border=self.BLUE,
            font_size=38,
            type_hitbox=True,
        )

    def _draw_language_picker(self, left, top, right, bottom):
        gap = self.GAP
        width = (right - left - gap) / 2
        back_height = max(92, (bottom - top) * 0.20)
        choice_bottom = bottom - back_height - gap
        for index, (label, code) in enumerate(
            ((self._t("english"), "en"), (self._t("kannada"), "kn"))
        ):
            x1 = left + index * (width + gap)
            self._draw_target(
                label,
                f"LANGUAGE:{code}",
                (x1, top, x1 + width, choice_bottom),
                fill=self.BLUE,
                text_color=self.WHITE,
                border=self.BLUE,
                font_size=42,
            )
        self._draw_target(
            self._t("back"),
            "BACK",
            (left, choice_bottom + gap, right, bottom),
            fill=self.CONTROL_BG,
            text_color=self.WHITE,
            border=self.CONTROL_BG,
            font_size=30,
        )

    def _draw_keyboard_page(self, left, top, right, bottom):
        if (
            self.language_manager.get_language() == LANG_KANNADA
            and not self.kannada_custom_mode
        ):
            if self.kannada_phrase_category is None:
                self._draw_kannada_phrase_home(left, top, right, bottom)
            elif self.kannada_phrase_category == "__more__":
                self._draw_kannada_more_phrases(left, top, right, bottom)
            else:
                self._draw_kannada_phrase_list(left, top, right, bottom)
            return

        suggestions_visible = any(self.suggestions) and not self.is_zoom_active()
        suggestion_height = (
            max(76, (bottom - top) * 0.13) if suggestions_visible else 0
        )
        controls_height = max(205, (bottom - top) * 0.34)

        alphabet_top = top
        if suggestions_visible:
            suggestion_bottom = top + suggestion_height
            self._draw_inline_suggestions(
                left,
                top,
                right,
                suggestion_bottom,
                enabled=True,
            )
            alphabet_top = suggestion_bottom + self.GAP

        controls_top = bottom - controls_height
        alphabet_bottom = controls_top - self.GAP
        zoom_active = self.is_zoom_active()
        if not zoom_active:
            if self.language_manager.get_language() == LANG_KANNADA:
                if self.kannada_category is None:
                    self._draw_kannada_categories(
                        left, alphabet_top, right, alphabet_bottom
                    )
                else:
                    self._draw_kannada_groups(
                        left, alphabet_top, right, alphabet_bottom
                    )
            else:
                self._draw_qwerty_overview(
                    left, alphabet_top, right, alphabet_bottom
                )
            self._draw_controls(left, controls_top, right, bottom, enabled=True)
        else:
            if self.kannada_zoom_group is not None:
                self._draw_kannada_zoom_panel(left, alphabet_top, right, alphabet_bottom)
            else:
                self._draw_zoom_panel(left, alphabet_top, right, alphabet_bottom)
            self._draw_controls(left, controls_top, right, bottom, enabled=False)

    def _draw_kannada_phrase_home(self, left, top, right, bottom):
        controls_height = max(120, (bottom - top) * 0.20)
        phrase_bottom = bottom - controls_height - self.GAP
        phrase_items = [
            (phrase, f"KPHRASE:{phrase}")
            for phrase in KANNADA_PRIMARY_PHRASES
        ]
        self._draw_grid_targets(
            phrase_items, left, top, right, phrase_bottom,
            columns=2, font_size=23,
        )
        control_items = (
            (self._t("more"), "KPHRASE_MORE"),
            (self._t("custom_type"), "KUSTOM"),
            (self._t("home"), "HOME"),
        )
        self._draw_grid_targets(
            control_items, left, phrase_bottom + self.GAP, right, bottom,
            columns=3, font_size=22,
        )

    def _draw_kannada_more_phrases(self, left, top, right, bottom):
        controls_height = max(108, (bottom - top) * 0.17)
        phrase_bottom = bottom - controls_height - self.GAP
        items = [
            (phrase, f"KPHRASE:{phrase}")
            for phrase in KANNADA_MORE_PHRASES
        ]
        self._draw_grid_targets(
            items, left, top, right, phrase_bottom, columns=2, font_size=23,
        )
        controls = (
            (self._t("back"), "KPHRASE_BACK"),
            (self._t("home"), "HOME"),
        )
        self._draw_grid_targets(
            controls, left, phrase_bottom + self.GAP, right, bottom,
            columns=2, font_size=22,
        )

    def _draw_kannada_phrase_categories(self, left, top, right, bottom):
        items = (
            (self._t("basic_needs"), "KPHRASE_CATEGORY:basic_needs"),
            (self._t("health_pain"), "KPHRASE_CATEGORY:health_pain"),
            (self._t("responses"), "KPHRASE_CATEGORY:responses"),
            (self._t("help_people"), "KPHRASE_CATEGORY:help_people"),
            (self._t("back"), "KPHRASE_BACK"),
            (self._t("home"), "HOME"),
        )
        self._draw_grid_targets(
            items, left, top, right, bottom, columns=2, font_size=27,
        )

    def _draw_kannada_phrase_list(self, left, top, right, bottom):
        phrases = KANNADA_PHRASE_GROUPS.get(
            self.kannada_phrase_category, ()
        )
        items = [(phrase, f"KPHRASE:{phrase}") for phrase in phrases]
        items.append((self._t("back"), "KPHRASE_BACK"))
        self._draw_grid_targets(
            items, left, top, right, bottom, columns=2, font_size=24,
        )

    def _draw_kannada_categories(self, left, top, right, bottom):
        items = (
            (self._t("vowels"), "vowels"),
            (self._t("consonants"), "consonants"),
            (self._t("signs"), "signs"),
            (self._t("common_words"), "common_words"),
        )
        targets = [(label, f"KCAT:{category}") for label, category in items]
        targets.append((self._t("back"), "KBACK"))
        self._draw_grid_targets(
            targets,
            left, top, right, bottom, columns=2, font_size=30,
        )

    def _draw_kannada_groups(self, left, top, right, bottom):
        groups = KANNADA_KEY_GROUPS.get(self.kannada_category, ())
        items = [
            (
                "  ".join(self._kannada_display_value(value) for value in values),
                f"KREGION:{name}",
            )
            for name, values in groups
        ]
        items.append((self._t("back"), "KBACK"))
        columns = 3 if len(items) > 4 else 2
        self._draw_grid_targets(items, left, top, right, bottom, columns, 25)

    def _draw_grid_targets(self, items, left, top, right, bottom, columns, font_size):
        rows = max(1, math.ceil(len(items) / columns))
        width = (right - left - self.GAP * (columns - 1)) / columns
        height = (bottom - top - self.GAP * (rows - 1)) / rows
        for index, (label, action) in enumerate(items):
            row, column = divmod(index, columns)
            x1 = left + column * (width + self.GAP)
            y1 = top + row * (height + self.GAP)
            self._draw_target(
                label, action, (x1, y1, x1 + width, y1 + height),
                fill="#DCEAFF", border=self.BLUE, font_size=font_size,
            )

    def _kannada_group_values(self):
        for groups in KANNADA_KEY_GROUPS.values():
            for name, values in groups:
                if name == self.kannada_zoom_group:
                    return values
        return ()

    @staticmethod
    def _kannada_display_value(value):
        """Give dependent signs a visible base without changing typed Unicode."""
        if value and unicodedata.category(value[0]).startswith("M"):
            return "\u25cc" + value
        return value

    def _draw_kannada_zoom_panel(self, left, top, right, bottom):
        values = self._kannada_group_values()
        self.canvas.create_text(
            (left + right) / 2, top + 21,
            text=self._t("look_letter"), fill=self.BLUE,
            font=self._font(20, "bold"), tags="interface",
        )
        panel_top = top + 42
        columns = 3 if len(values) >= 5 else 2
        items = []
        for value in values:
            prefix = "KWORD:" if self.kannada_category == "common_words" else "KCHAR:"
            items.append((self._kannada_display_value(value), prefix + value))
        self._draw_grid_targets(items, left, panel_top, right, bottom, columns, 38)

    def _draw_inline_suggestions(self, left, top, right, bottom, enabled):
        visible = [word for word in self.suggestions if word][:3]
        if not visible:
            return
        gap = self.GAP
        width = (right - left - gap * (len(visible) - 1)) / len(visible)
        for index, suggestion in enumerate(visible):
            x1 = left + index * (width + gap)
            bounds = (x1, top, x1 + width, bottom)
            self.suggestion_bounds.append(bounds)
            action = f"SUGGESTION:{index}"
            if enabled:
                self._draw_target(
                    suggestion,
                    action,
                    bounds,
                    fill=self.SUGGESTION_BG,
                    text_color=self.BLUE,
                    border="#C7D2FE",
                    font_size=28,
                )
                self.suggestion_items.append(self.targets[action]["text"])
            else:
                self._draw_disabled_card(suggestion, bounds)
                self.suggestion_items.append(None)

    def _draw_qwerty_overview(self, left, top, right, bottom):
        row_gap = self.GAP
        row_height = (bottom - top - row_gap * 2) / 3
        row_bounds = []
        for row_index, row in enumerate(self.QWERTY_ROWS):
            y1 = top + row_index * (row_height + row_gap)
            bounds = (left, y1, right, y1 + row_height)
            row_bounds.append(bounds)
            self._draw_visual_qwerty_row(row, *bounds)

        for row_index, region_names in enumerate(self.REGION_ROWS):
            row_left, row_top, row_right, row_bottom = row_bounds[row_index]
            row_letter_count = len(self.QWERTY_ROWS[row_index])
            x1 = row_left
            for region_name in region_names:
                fraction = len(self.ZOOM_REGIONS[region_name]) / row_letter_count
                x2 = x1 + (row_right - row_left) * fraction
                self._register_region(
                    region_name,
                    (x1, row_top, x2, row_bottom),
                )
                x1 = x2

    def _draw_visual_qwerty_row(self, row, left, top, right, bottom):
        gap = 7
        key_width = (right - left - gap * (len(row) - 1)) / len(row)
        x = left
        for letter in row:
            self._draw_visual_key(letter, (x, top, x + key_width, bottom))
            x += key_width + gap

    def _draw_visual_key(self, label, bounds):
        x1, y1, x2, y2 = bounds
        self._rounded_rectangle(
            x1,
            y1,
            x2,
            y2,
            10,
            fill=self.KEY_BG,
            outline=self.KEY_BORDER,
            width=2,
            tags="interface",
        )
        self.canvas.create_text(
            (x1 + x2) / 2,
            (y1 + y2) / 2,
            text=label,
            fill=self.KEY_TEXT,
            font=self._font(27, "bold"),
            tags="interface",
        )

    def _register_region(self, region_name, bounds):
        x1, y1, x2, y2 = bounds
        action = f"REGION:{region_name}"
        outline = self._rounded_rectangle(
            x1 + 3,
            y1 + 3,
            x2 - 3,
            y2 - 3,
            12,
            fill="",
            outline="#A9C5E8",
            width=2,
            tags="interface",
        )
        progress_bounds = (x1 + 14, y2 - 14, x2 - 14, y2 - 7)
        self.canvas.create_rectangle(
            *progress_bounds,
            fill="#D6E3F5",
            outline="",
            tags="interface",
        )
        progress = self.canvas.create_rectangle(
            progress_bounds[0],
            progress_bounds[1],
            progress_bounds[0],
            progress_bounds[3],
            fill=self.BLUE,
            outline="",
            tags="interface",
        )
        self._store_target(
            action,
            region_name.replace("_", " "),
            bounds,
            outline,
            None,
            progress,
            fill="",
            border="#A9C5E8",
            hover="",
            region=True,
            dwell_kind="bar",
            progress_bounds=progress_bounds,
        )

    def _draw_zoom_panel(self, left, top, right, bottom):
        letters = self.ZOOM_REGIONS[self.zoom_region]
        self.canvas.create_text(
            (left + right) / 2,
            top + 21,
            text=self._t("look_letter"),
            fill=self.BLUE,
            font=self._font(20, "bold"),
            tags="interface",
        )
        panel_top = top + 48
        if len(letters) <= 3:
            rows, columns = 1, len(letters)
        else:
            rows, columns = 2, 2

        # The logical cells fully tile the zoom panel. Buttons retain a small
        # visual gap, while their non-overlapping entry zones cover that gap.
        gap = float(config.ZOOM_LETTER_VISUAL_GAP)
        cell_width = (right - left) / columns
        cell_height = (bottom - panel_top) / rows
        inset = gap / 2.0
        for index, letter in enumerate(letters):
            row, column = divmod(index, columns)
            logical_x1 = left + column * cell_width
            logical_y1 = panel_top + row * cell_height
            logical_x2 = left + (column + 1) * cell_width
            logical_y2 = panel_top + (row + 1) * cell_height
            entry_bounds = (
                logical_x1,
                logical_y1,
                logical_x2 - (0.01 if column < columns - 1 else 0.0),
                logical_y2 - (0.01 if row < rows - 1 else 0.0),
            )
            self._draw_target(
                letter,
                letter,
                (
                    logical_x1 + inset,
                    logical_y1 + inset,
                    logical_x2 - inset,
                    logical_y2 - inset,
                ),
                fill="#DCEAFF",
                border=self.BLUE,
                font_size=54,
                entry_bounds=entry_bounds,
                exit_margin=config.ZOOM_LETTER_EXIT_MARGIN,
                intent_scale=config.ZOOM_LETTER_INTENT_SCALE,
            )

    def _draw_controls(self, left, top, right, bottom, enabled):
        row_gap = self.GAP
        row_height = (bottom - top - row_gap * 2) / 3
        column_gap = self.GAP
        column_width = (right - left - column_gap) / 2
        controls = (
            (self._t("space"), "SPACE", 0, 0, 1),
            (self._t("backspace"), "BACKSPACE", 0, 1, 1),
            (self._t("clear"), "CLEAR", 1, 0, 1),
            (self._t("speak"), "SPEAK", 1, 1, 1),
            (self._t("home"), "HOME", 2, 0, 2),
        )
        for label, action, row, column, span in controls:
            x1 = left + column * (column_width + column_gap)
            y1 = top + row * (row_height + row_gap)
            width = column_width * span + column_gap * (span - 1)
            bounds = (x1, y1, x1 + width, y1 + row_height)
            if enabled:
                self._draw_target(
                    label,
                    action,
                    bounds,
                    fill=self.BLUE if action == "SPEAK" else self.CONTROL_BG,
                    text_color=self.WHITE,
                    border=self.BLUE if action == "SPEAK" else self.CONTROL_BG,
                    font_size=28,
                )
                if action == "SPEAK":
                    self.speak_bounds = bounds
            else:
                self._draw_disabled_card(label, bounds)

    def _draw_target(
        self,
        label,
        action,
        bounds,
        *,
        fill=None,
        text_color=None,
        border=None,
        hover=None,
        font_size=32,
        type_hitbox=False,
        entry_bounds=None,
        exit_margin=None,
        intent_scale=None,
    ):
        x1, y1, x2, y2 = bounds
        fill = fill or self.KEY_BG
        text_color = text_color or self.KEY_TEXT
        border = border or self.KEY_BORDER
        hover = hover or self.KEY_HOVER
        self._rounded_rectangle(
            x1,
            y1 + 3,
            x2,
            y2 + 3,
            self.CORNER_RADIUS,
            fill=self.SHADOW,
            outline="",
            tags="interface",
        )
        background = self._rounded_rectangle(
            x1,
            y1,
            x2,
            y2,
            self.CORNER_RADIUS,
            fill=fill,
            outline=border,
            width=2,
            tags="interface",
        )
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        ring_radius = min(48, max(24, min(x2 - x1, y2 - y1) * 0.34))
        ring = self.canvas.create_arc(
            center_x - ring_radius,
            center_y - ring_radius,
            center_x + ring_radius,
            center_y + ring_radius,
            start=90,
            extent=0,
            style=tk.ARC,
            outline=self.WHITE if text_color == self.WHITE else self.BLUE,
            width=8,
            tags="interface",
        )
        text_id = self.canvas.create_text(
            center_x,
            center_y,
            text=label,
            fill=text_color,
            font=self._font(font_size, "bold"),
            width=max(70, x2 - x1 - 20),
            tags="interface",
        )
        self._store_target(
            action,
            label,
            bounds,
            background,
            text_id,
            ring,
            fill,
            border,
            hover,
            type_hitbox=type_hitbox,
            entry_bounds=entry_bounds,
            exit_margin=exit_margin,
            intent_scale=intent_scale,
        )

    def _store_target(
        self,
        action,
        label,
        bounds,
        background,
        text_id,
        ring,
        fill,
        border,
        hover,
        *,
        region=False,
        type_hitbox=False,
        dwell_kind="arc",
        progress_bounds=None,
        entry_bounds=None,
        exit_margin=None,
        intent_scale=None,
    ):
        x1, y1, x2, y2 = bounds
        canvas_width = max(1, self.winfo_width())
        canvas_height = max(1, self.winfo_height())
        if entry_bounds is None:
            margin = (
                0
                if region
                else min(
                    config.TARGET_ENTRY_MARGIN,
                    max(0, self.GAP / 2 - 1),
                )
            )
            gaze_bounds = (
                max(0, x1 - margin),
                max(0, y1 - margin),
                min(canvas_width, x2 + margin),
                min(canvas_height, y2 + margin),
            )
        else:
            gaze_bounds = tuple(entry_bounds)
        effective_exit_margin = (
            config.TARGET_EXIT_MARGIN if exit_margin is None else float(exit_margin)
        )
        if intent_scale is None:
            if action == "TYPE":
                intent_scale = 1.25
            elif self.mode == self.EMERGENCY_HOME:
                intent_scale = 1.15
            elif region:
                intent_scale = 1.10
            elif action.startswith("SUGGESTION:"):
                intent_scale = 1.08
            elif action in {
                "SPACE", "BACKSPACE", "CLEAR", "SPEAK", "HOME",
                "LANGUAGE", "CATEGORIES",
            }:
                intent_scale = 1.06
            else:
                intent_scale = 1.0
        self.targets[action] = {
            "label": label,
            "bounds": bounds,
            "gaze_bounds": gaze_bounds,
            "entry_bounds": gaze_bounds,
            "exit_bounds": (
                max(0, x1 - effective_exit_margin),
                max(0, y1 - effective_exit_margin),
                min(canvas_width, x2 + effective_exit_margin),
                min(canvas_height, y2 + effective_exit_margin),
            ),
            "center": ((x1 + x2) / 2, (y1 + y2) / 2),
            "background": background,
            "text": text_id,
            "dwell": ring,
            "fill": fill,
            "border": border,
            "hover": hover,
            "region": region,
            "intent_scale": intent_scale,
            "dwell_kind": dwell_kind,
            "progress_bounds": progress_bounds,
        }

    def _draw_disabled_card(self, label, bounds):
        x1, y1, x2, y2 = bounds
        self._rounded_rectangle(
            x1,
            y1,
            x2,
            y2,
            self.CORNER_RADIUS,
            fill=self.DISABLED_BG,
            outline=self.KEY_BORDER,
            width=2,
            tags="interface",
        )
        self.canvas.create_text(
            (x1 + x2) / 2,
            (y1 + y2) / 2,
            text=label,
            fill=self.DISABLED_TEXT,
            font=self._font(23, "bold"),
            tags="interface",
        )

    def _rounded_rectangle(self, x1, y1, x2, y2, radius, **options):
        points = (
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1,
        )
        return self.canvas.create_polygon(
            points,
            smooth=True,
            splinesteps=24,
            **options,
        )

    @staticmethod
    def _inside(bounds, x, y):
        x1, y1, x2, y2 = bounds
        return x1 <= x <= x2 and y1 <= y <= y2

    @staticmethod
    def _distance_to_bounds(bounds, x, y):
        x1, y1, x2, y2 = bounds
        dx = max(x1 - x, 0, x - x2)
        dy = max(y1 - y, 0, y - y2)
        return math.hypot(dx, dy)

    def is_within_lock_distance(self, action, x, y):
        target = self.targets.get(action)
        if target is None:
            return False
        return (
            self._distance_to_bounds(target["bounds"], x, y)
            <= config.STICKY_LOCK_EXIT_DISTANCE
        )

    def get_target_at_position(
        self,
        x,
        y,
        current_target=None,
        sticky=False,
    ):
        if current_target in self.targets:
            if sticky and self.is_within_lock_distance(
                current_target,
                x,
                y,
            ):
                return current_target
            if not sticky and self._inside(
                self.targets[current_target]["exit_bounds"],
                x,
                y,
            ):
                return current_target

        for action, target in self.targets.items():
            if self._inside(target["bounds"], x, y):
                return action
        for action, target in self.targets.items():
            if self._inside(target["gaze_bounds"], x, y):
                return action

        if not self._inside(self._interactive_bounds, x, y):
            return None
        nearest = sorted(
            (
                (self._distance_to_bounds(target["bounds"], x, y), action)
                for action, target in self.targets.items()
            ),
            key=lambda item: (item[0], item[1]),
        )
        if nearest and nearest[0][0] <= config.TARGET_SNAP_DISTANCE:
            return nearest[0][1]
        return None

    def get_key_at_position(self, x, y):
        return self.get_target_at_position(x, y)

    def get_intent_targets(self):
        """Return geometry for every currently selectable target."""
        return {
            action: {
                "center": target["center"],
                "bounds": target["bounds"],
                "intent_scale": target.get("intent_scale", 1.0),
            }
            for action, target in self.targets.items()
        }

    def get_direct_target_at_position(self, x, y):
        """Return the sole target whose non-overlapping entry zone contains a point."""
        matches = [
            action
            for action, target in self.targets.items()
            if self._inside(target.get("entry_bounds", target["bounds"]), x, y)
        ]
        return matches[0] if len(matches) == 1 else None

    def is_within_exit_bounds(self, action, x, y):
        """Return whether an acquired target still contains the gaze with margin."""
        target = self.targets.get(action)
        return bool(
            target is not None
            and self._inside(target["exit_bounds"], x, y)
        )

    def get_target_center(self, action):
        target = self.targets.get(action)
        return target["center"] if target is not None else None

    def highlight_key(self, key_name):
        self.highlighted_key = key_name if key_name in self.targets else None
        self.clear_highlight(keep_name=True)
        if self.highlighted_key is not None:
            self._apply_highlight(self.highlighted_key)

    def _apply_highlight(self, key_name):
        target = self.targets.get(key_name)
        if target is None:
            return
        options = {"outline": self.BLUE, "width": 5}
        if target["region"]:
            options["fill"] = "#DCEAFF"
            options["stipple"] = "gray25"
            letters = " ".join(
                self.ZOOM_REGIONS[key_name.removeprefix("REGION:")]
            )
            centre_x, centre_y = target["center"]
            self.canvas.delete("region_feedback")
            self.canvas.create_text(
                centre_x,
                centre_y,
                text=f"Region: {letters}\nHold gaze to enlarge",
                fill=self.BLUE,
                font=self._font(12, "bold"),
                justify=tk.CENTER,
                state=tk.DISABLED,
                tags=("interface", "region_feedback"),
            )
        else:
            options["fill"] = target["hover"]
        self.canvas.itemconfigure(target["background"], **options)

    def clear_highlight(self, keep_name=False):
        self.canvas.delete("region_feedback")
        for target in self.targets.values():
            self.canvas.itemconfigure(
                target["background"],
                fill=target["fill"],
                outline=target["border"],
                width=2,
                stipple="",
            )
        if not keep_name:
            self.highlighted_key = None

    def start_dwell(self, key_name):
        self.stop_dwell()
        if key_name not in self.targets:
            return
        self.active_dwell_key = key_name
        self.dwell_progress = 0.0
        self.highlight_key(key_name)
        self.set_dwell_progress(key_name, 0.0)

    def set_dwell_progress(self, key_name, progress):
        target = self.targets.get(key_name)
        if target is None:
            return
        progress = max(0.0, min(1.0, float(progress)))
        if key_name == self.active_dwell_key:
            self.dwell_progress = progress
        if target.get("dwell_kind") == "bar":
            x1, y1, x2, y2 = target["progress_bounds"]
            self.canvas.coords(
                target["dwell"], x1, y1, x1 + (x2 - x1) * progress, y2
            )
        else:
            self.canvas.itemconfigure(target["dwell"], extent=-360 * progress)

    def stop_dwell(self):
        if self.active_dwell_key in self.targets:
            target = self.targets[self.active_dwell_key]
            if target.get("dwell_kind") == "bar":
                x1, y1, _x2, y2 = target["progress_bounds"]
                self.canvas.coords(target["dwell"], x1, y1, x1, y2)
            else:
                self.canvas.itemconfigure(target["dwell"], extent=0)
        self.active_dwell_key = None
        self.dwell_progress = 0.0

    def open_zoom_region(self, region_name):
        if region_name not in self.ZOOM_REGIONS or self.mode != self.KEYBOARD:
            return False
        self.zoom_region = region_name
        self._draw_screen()
        return True

    def open_kannada_category(self, category):
        if category not in KANNADA_KEY_GROUPS or self.mode != self.KEYBOARD:
            return False
        self.kannada_category = category
        self.kannada_zoom_group = None
        self._draw_screen()
        return True

    def open_kannada_phrase_category(self, category):
        if category not in KANNADA_PHRASE_GROUPS or self.mode != self.KEYBOARD:
            return False
        self.kannada_phrase_category = category
        self._draw_screen()
        return True

    def open_kannada_more_phrases(self):
        if self.mode != self.KEYBOARD:
            return False
        self.kannada_phrase_category = "__more__"
        self._draw_screen()
        return True

    def open_kannada_custom_type(self):
        if self.mode != self.KEYBOARD:
            return False
        self.kannada_custom_mode = True
        self.kannada_phrase_category = None
        self.kannada_category = None
        self.kannada_zoom_group = None
        self._draw_screen()
        return True

    def show_kannada_phrase_home(self):
        self.kannada_custom_mode = False
        self.kannada_phrase_category = None
        self.kannada_category = None
        self.kannada_zoom_group = None
        if self.mode == self.KEYBOARD:
            self._draw_screen()

    def navigate_kannada_back(self):
        """Move exactly one level up in the optional character keyboard."""
        if self.mode != self.KEYBOARD or not self.kannada_custom_mode:
            return False
        if self.kannada_zoom_group is not None:
            self.kannada_zoom_group = None
        elif self.kannada_category is not None:
            self.kannada_category = None
        else:
            self.show_kannada_phrase_home()
            return True
        self._draw_screen()
        return True

    def open_kannada_group(self, group_name):
        valid_names = {
            name for groups in KANNADA_KEY_GROUPS.values() for name, _ in groups
        }
        if group_name not in valid_names or self.mode != self.KEYBOARD:
            return False
        self.kannada_zoom_group = group_name
        self._draw_screen()
        return True

    def close_zoom(self):
        if self.zoom_region is None and self.kannada_zoom_group is None:
            return
        self.zoom_region = None
        self.kannada_zoom_group = None
        self._draw_screen()

    def is_zoom_active(self):
        return self.mode == self.KEYBOARD and (
            self.zoom_region is not None or self.kannada_zoom_group is not None
        )

    def complete_dwell(self, key_name):
        self.stop_dwell()
        if self.on_action is not None and self.on_action(key_name):
            return
        self._perform_standalone_action(key_name)

    def _perform_standalone_action(self, action):
        if action.startswith("EMERGENCY:"):
            phrase = self.language_manager.get_emergency_phrase(
                action.removeprefix("EMERGENCY:")
            )
            self.append_text(phrase["display"] + " ")
        elif action == "TYPE":
            self.show_keyboard()
        elif action.startswith("REGION:"):
            self.open_zoom_region(action.removeprefix("REGION:"))
        elif action == "HOME":
            self.show_home()
        elif action == "BACK":
            self.show_home()
        elif action == "LANGUAGE":
            self.show_language_picker()
        elif action == "CATEGORIES":
            self.kannada_category = None
            self.kannada_zoom_group = None
            self._draw_screen()
        elif action.startswith("LANGUAGE:"):
            self.language_manager.set_language(action.removeprefix("LANGUAGE:"))
            self.set_language_manager(self.language_manager)
            self.show_home()
        elif action.startswith("KCAT:"):
            self.open_kannada_category(action.removeprefix("KCAT:"))
        elif action == "KCUSTOM_BACK":
            self.show_kannada_phrase_home()
        elif action == "KBACK":
            self.navigate_kannada_back()
        elif action.startswith("KREGION:"):
            self.open_kannada_group(action.removeprefix("KREGION:"))
        elif action.startswith("KCHAR:"):
            self.append_text(action.removeprefix("KCHAR:"))
            self.close_zoom()
        elif action.startswith("KWORD:"):
            word = action.removeprefix("KWORD:")
            if self.output_text and not self.output_text[-1].isspace():
                self.append_text(" ")
            self.append_text(word + " ")
            self.close_zoom()
        elif action == "CLEAR":
            self.clear_text()
        elif action == "SPACE":
            if self.output_text and not self.output_text[-1].isspace():
                self.append_text(" ")
        elif action == "BACKSPACE":
            self.backspace()
        elif action == "SPEAK":
            self.speak_text()
        elif action.startswith("SUGGESTION:"):
            index = int(action.removeprefix("SUGGESTION:"))
            visible = [word for word in self.suggestions if word][:3]
            if index < len(visible):
                self._apply_suggestion(visible[index])
        elif len(action) == 1 and action.isalpha():
            self.append_text(action)
            self.close_zoom()

    def show_home(self):
        self.zoom_region = None
        self.kannada_zoom_group = None
        self.kannada_phrase_category = None
        self.kannada_custom_mode = False
        self.mode = self.EMERGENCY_HOME
        self._draw_screen()

    def show_keyboard(self):
        self.zoom_region = None
        self.kannada_zoom_group = None
        self.kannada_category = None
        self.kannada_phrase_category = None
        self.kannada_custom_mode = False
        self.mode = self.KEYBOARD
        self._draw_screen()

    def show_language_picker(self):
        self.zoom_region = None
        self.kannada_zoom_group = None
        self.mode = self.LANGUAGE_PICKER
        self._draw_screen()

    def set_language_manager(self, language_manager):
        """Update labels without changing output text or calibration state."""
        self.language_manager = language_manager
        self.kannada_category = None
        self.kannada_zoom_group = None
        self.zoom_region = None
        self.kannada_phrase_category = None
        self.kannada_custom_mode = False
        self._draw_screen()

    def show_keyboard_groups(self):
        self.show_keyboard()

    def show_letter_group(self, _group_name):
        self.show_keyboard()
        return True

    def show_suggestions(self):
        self.show_keyboard()

    def enlarge_target(self, _key_name):
        """Compatibility no-op; local zoom replaces fisheye key enlargement."""

    def restore_key_sizes(self):
        """Compatibility no-op; local zoom is closed explicitly."""

    def set_suggestions(self, word_list):
        words = list(word_list or [])[:3]
        words.extend([""] * (3 - len(words)))
        self.suggestions = words
        if self.mode == self.KEYBOARD:
            self._draw_screen()

    def append_text(self, text):
        self.output_text += text
        self._refresh_output()

    def backspace(self):
        # Python strings are Unicode code points: a Kannada vowel sign is
        # removed first, leaving its consonant intact (for example, ಕಾ -> ಕ).
        self.output_text = self.output_text[:-1]
        self._refresh_output()

    def clear_text(self):
        self.output_text = ""
        self._refresh_output()

    def get_text(self):
        return self.output_text

    def set_text(self, text):
        self.output_text = text if isinstance(text, str) else ""
        self._refresh_output()

    def _refresh_output(self):
        if self.output_text_id is None:
            return
        shown_text = self.output_text or self._t("message_placeholder")
        self.canvas.itemconfigure(
            self.output_text_id,
            text=shown_text,
            fill=self.KEY_TEXT if self.output_text else "#7A8494",
        )

    def speak_text(self):
        if not self.output_text.strip():
            return
        if self.on_action is not None:
            self.on_action("SPEAK")
            return
        if self._standalone_tts is None:
            try:
                try:
                    from ..utils.tts import TextToSpeech
                except ImportError:
                    from utils.tts import TextToSpeech
                self._standalone_tts = TextToSpeech()
            except Exception as exc:
                print(f"Speech unavailable: {exc}")
                return
        self._standalone_tts.speak(
            self.output_text,
            self.language_manager.get_language(),
        )

    def _apply_suggestion(self, word):
        if " " in self.output_text:
            prefix = self.output_text.rsplit(" ", 1)[0] + " "
        else:
            prefix = ""
        self.set_text(prefix + word + " ")

    def _handle_click(self, event):
        action = self.get_target_at_position(event.x, event.y)
        if action is not None:
            self.complete_dwell(action)

    def update_gaze_debug(
        self,
        raw_x,
        raw_y,
        smoothed_x,
        smoothed_y,
        target,
        dwell_progress,
        invalid_duration,
        right_edge_reach,
        valid=True,
        horizontal_debug=None,
        model_debug=None,
    ):
        """Draw cursor markers and a header-contained debug panel."""
        self.canvas.delete("gaze_debug")
        self.detected_horizontal_zone = None
        if not config.GAZE_DEBUG:
            return

        if raw_x is not None and raw_y is not None:
            radius = 8
            self.canvas.create_oval(
                raw_x - radius,
                raw_y - radius,
                raw_x + radius,
                raw_y + radius,
                fill=self.GREEN,
                outline=self.WHITE,
                width=2,
                state=tk.DISABLED,
                tags="gaze_debug",
            )
        centre = self.get_target_center(target)
        if centre is not None:
            snap_x, snap_y = centre
            radius = 16
            self.canvas.create_oval(
                snap_x - radius,
                snap_y - radius,
                snap_x + radius,
                snap_y + radius,
                outline="#00A6FB",
                width=5,
                state=tk.DISABLED,
                tags="gaze_debug",
            )

        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        for fraction in (0.25, 0.50, 0.75):
            line_x = width * fraction
            self.canvas.create_line(
                line_x,
                0,
                line_x,
                height,
                fill="#D946EF",
                width=2 if fraction == 0.50 else 1,
                dash=(6, 6),
                state=tk.DISABLED,
                tags="gaze_debug",
            )
        if config.HORIZONTAL_ZONE_DEBUG:
            self._draw_horizontal_zone_overlay(
                smoothed_x if smoothed_x is not None else raw_x,
                width,
                height,
            )

        def number(value):
            return "--" if value is None else f"{value:.0f}"

        def ratio(value):
            return "--" if value is None else f"{value:.4f}"

        reach = "--"
        if right_edge_reach is not None:
            status = (
                "OK"
                if right_edge_reach >= config.RIGHT_EDGE_DIAGNOSTIC_THRESHOLD
                else "NOT YET"
            )
            reach = f"{right_edge_reach * 100:.0f}% {status}"
        horizontal_debug = horizontal_debug or {}
        model_debug = model_debug or {}
        zoom = self.zoom_region or "--"
        region = model_debug.get("region_name") or "--"
        confidence_value = model_debug.get("confidence")
        confidence_text = (
            "--" if confidence_value is None else f"{float(confidence_value):.2f}"
        )
        reason = str(model_debug.get("dwell_reason") or "NO_TARGET")
        diagnostic = bool(model_debug.get("diagnostic_mode"))
        intent_target = model_debug.get("intent_target") or "--"
        second_target = model_debug.get("intent_second_target") or "--"
        top_scores = " ".join(
            f"{name}:{float(score):.2f}"
            for name, score in model_debug.get("intent_top_scores", [])
        ) or "--"
        hit_test = model_debug.get("hit_test", "NONE")
        direct_target = model_debug.get("direct_target") or "--"
        final_target = model_debug.get("final_target") or "--"
        acquiring_elapsed = float(model_debug.get("acquiring_elapsed", 0.0))
        acquiring_required = float(model_debug.get("acquiring_required", 0.0))
        acquiring_progress = float(model_debug.get("acquiring_progress", 0.0))
        region_letters = model_debug.get("region_letters") or "--"
        region_progress = float(model_debug.get("region_progress", 0.0))
        ui_mode = model_debug.get("ui_mode") or self.mode
        lines = (
            (
                "DWELL DIAGNOSTIC MODE"
                if diagnostic else f"Dwell state: {reason}"
            ),
            f"Hit method {hit_test}  Direct {direct_target}  Final {final_target}",
            f"Intent {intent_target}={float(model_debug.get('intent_score', 0.0)):.2f}  "
            f"Second {second_target}={float(model_debug.get('intent_second_score', 0.0)):.2f}  "
            f"Margin {float(model_debug.get('intent_margin', 0.0)):.2f}",
            f"Samples {int(model_debug.get('intent_history_samples', 0))}  "
            f"State {model_debug.get('intent_state', 'EMPTY')}  Top {top_scores}",
            f"Mode {ui_mode}  Region {region}  Letters {region_letters}",
            f"Acquiring {acquiring_elapsed:.2f}/{acquiring_required:.2f}s  "
            f"Acquire {acquiring_progress * 100:.0f}%  "
            f"Region activation {region_progress * 100:.0f}%",
            f"Pred {number(model_debug.get('predicted_x'))}/"
            f"{number(model_debug.get('predicted_y'))}  Corrected "
            f"{number(model_debug.get('corrected_x'))}/"
            f"{number(model_debug.get('corrected_y'))}",
            f"Smooth {number(smoothed_x)}/{number(smoothed_y)}  Correction "
            f"{number(model_debug.get('correction_x'))}/"
            f"{number(model_debug.get('correction_y'))}",
            f"Target {target or '--'}  Zoom {zoom}  "
            f"Dwell {dwell_progress * 100:.0f}%  Reason {reason}",
            f"Confidence {confidence_text}  Face drift "
            f"{ratio(model_debug.get('face_drift'))}  "
            f"Valid {bool(model_debug.get('gaze_valid', valid))}  "
            f"Blink {bool(model_debug.get('blink', False))}",
            f"Invalid {invalid_duration:.2f}s  Right {reach}  LOCAL  "
            f"{str(model_debug.get('warning') or '')}",
        )
        x1 = max(width * 0.56, 420)
        x2 = width - 10
        header_bottom = max(128, int(height * 0.16))
        self.canvas.create_rectangle(
            x1,
            7,
            x2,
            header_bottom - 7,
            fill="#16213E",
            outline=self.WHITE,
            width=1,
            state=tk.DISABLED,
            tags="gaze_debug",
        )
        line_spacing = max(8.5, (header_bottom - 22) / len(lines))
        for index, text in enumerate(lines):
            self.canvas.create_text(
                x1 + 10,
                9 + index * line_spacing,
                text=text,
                anchor="nw",
                fill=self.WHITE,
                font=("Consolas", 6, "bold"),
                state=tk.DISABLED,
                tags="gaze_debug",
            )

    def _draw_horizontal_zone_overlay(self, gaze_x, width, height):
        names = (
            "FAR LEFT",
            "LEFT-MIDDLE",
            "CENTRE",
            "RIGHT-MIDDLE",
            "FAR RIGHT",
        )
        zone_width = width / len(names)
        active_index = None
        if gaze_x is not None:
            active_index = max(
                0,
                min(len(names) - 1, int(gaze_x / zone_width)),
            )
            self.detected_horizontal_zone = names[active_index]

        for index, name in enumerate(names):
            x1 = index * zone_width
            x2 = (index + 1) * zone_width
            self.canvas.create_rectangle(
                x1,
                0,
                x2,
                height,
                fill="",
                outline=self.BLUE if index == active_index else "#94A3B8",
                width=6 if index == active_index else 2,
                state=tk.DISABLED,
                tags="gaze_debug",
            )
            self.canvas.create_text(
                (x1 + x2) / 2,
                height - 12,
                text=name,
                fill=self.BLUE if index == active_index else "#475569",
                font=("Arial", 10, "bold"),
                state=tk.DISABLED,
                tags="gaze_debug",
            )

    def clear_gaze_debug(self):
        self.canvas.delete("gaze_debug")

    def close(self):
        self.stop_dwell()
        if self._standalone_tts is not None:
            self._standalone_tts.shutdown()
            self._standalone_tts = None
        if self._redraw_after_id is not None:
            try:
                self.after_cancel(self._redraw_after_id)
            except tk.TclError:
                pass
            self._redraw_after_id = None
        self.destroy()
