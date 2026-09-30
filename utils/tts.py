import base64
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from array import array
from pathlib import Path
from typing import Any

try:
    from .. import config
    from .language_manager import (
        LANG_ENGLISH,
        LANG_KANNADA,
        get_fixed_kannada_speech_items,
    )
    from .runtime import get_data_path, get_logger, get_resource_path
except ImportError:
    import config
    from utils.language_manager import (
        LANG_ENGLISH,
        LANG_KANNADA,
        get_fixed_kannada_speech_items,
    )
    from utils.runtime import get_data_path, get_logger, get_resource_path


class TextToSpeech:
    """Single-worker offline TTS with Windows, AI4Bharat, and eSpeak."""

    MIN_RATE = 80
    MAX_RATE = 250
    MIN_VOLUME = 0.0
    MAX_VOLUME = 1.0
    DIAGNOSTIC_TIMEOUT = 8.0
    SPEECH_TIMEOUT = 60.0
    SHUTDOWN_TIMEOUT = 3.0
    PROCESS_STOP_TIMEOUT = 0.20
    PRIORITY_USER = 1
    PRIORITY_STATE = 2
    PRIORITY_INSTRUCTION = 3

    _VOICE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $synth.GetInstalledVoices() | ForEach-Object {
            $name = $_.VoiceInfo.Name
            [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($name))
        }
    }
    finally {
        $synth.Dispose()
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""

    _SPEAK_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {
    Add-Type -AssemblyName System.Speech
    $text = [Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String($env:GAZELINK_TTS_TEXT)
    )
    $voice = [Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String($env:GAZELINK_TTS_VOICE)
    )
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        if (-not [string]::IsNullOrWhiteSpace($voice)) {
            try {
                $synth.SelectVoice($voice)
            }
            catch {
                # Keep the default Windows voice when selection fails.
            }
        }
        $synth.Rate = [Math]::Max(
            -10,
            [Math]::Min(10, [int]$env:GAZELINK_TTS_RATE)
        )
        $synth.Volume = [Math]::Max(
            0,
            [Math]::Min(100, [int]$env:GAZELINK_TTS_VOLUME)
        )
        $synth.Speak($text)
    }
    finally {
        $synth.Dispose()
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""

    _ENGLISH_SERVER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    [Console]::Out.WriteLine('READY')
    [Console]::Out.Flush()
    while (($line = [Console]::In.ReadLine()) -ne $null) {
        if ($line -eq 'SHUTDOWN') { break }
        $parts = $line.Split('|', 5)
        if ($parts.Count -ne 5) { continue }
        $requestId = $parts[0]
        try {
            $rate = [int]$parts[1]
            $volume = [int]$parts[2]
            $voice = [Text.Encoding]::UTF8.GetString(
                [Convert]::FromBase64String($parts[3])
            )
            $text = [Text.Encoding]::UTF8.GetString(
                [Convert]::FromBase64String($parts[4])
            )
            if (-not [string]::IsNullOrWhiteSpace($voice)) {
                try { $synth.SelectVoice($voice) } catch { }
            }
            $synth.Rate = [Math]::Max(-10, [Math]::Min(10, $rate))
            $synth.Volume = [Math]::Max(0, [Math]::Min(100, $volume))
            $synth.Speak($text)
            [Console]::Out.WriteLine("DONE|$requestId")
        }
        catch {
            $detail = [Convert]::ToBase64String(
                [Text.Encoding]::UTF8.GetBytes($_.Exception.Message)
            )
            [Console]::Out.WriteLine("ERROR|$requestId|$detail")
        }
        [Console]::Out.Flush()
    }
}
finally {
    try { $synth.StopSpeaking() } catch { }
    $synth.Dispose()
}
"""

    _PLAY_WAV_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
    $path = [Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String($env:GAZELINK_WAV_PATH)
    )
    $player = New-Object System.Media.SoundPlayer($path)
    try {
        $player.Load()
        $player.PlaySync()
    }
    finally {
        $player.Dispose()
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""

    _WAV_SERVER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Console]::Out.WriteLine('READY')
[Console]::Out.Flush()
while (($line = [Console]::In.ReadLine()) -ne $null) {
    if ($line -eq 'SHUTDOWN') { break }
    $parts = $line.Split('|', 2)
    if ($parts.Count -ne 2) { continue }
    $requestId = $parts[0]
    try {
        $path = [Text.Encoding]::UTF8.GetString(
            [Convert]::FromBase64String($parts[1])
        )
        $player = New-Object System.Media.SoundPlayer($path)
        try { $player.Load(); $player.PlaySync() }
        finally { $player.Dispose() }
        [Console]::Out.WriteLine("DONE|$requestId")
    }
    catch {
        $detail = [Convert]::ToBase64String(
            [Text.Encoding]::UTF8.GetBytes($_.Exception.Message)
        )
        [Console]::Out.WriteLine("ERROR|$requestId|$detail")
    }
    [Console]::Out.Flush()
}
"""

    def __init__(
        self,
        rate: int = 140,
        volume: float = 1.0,
        voice_id: str | None = None,
        language: str = LANG_ENGLISH,
    ) -> None:
        self._rate = self._clamp_rate(rate)
        self._volume = self._clamp_volume(volume)
        self._voice_id = voice_id
        self._language = (
            language if language in {LANG_ENGLISH, LANG_KANNADA} else LANG_ENGLISH
        )

        self._commands: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._command_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._temp_lock = threading.Lock()
        self._initialization_complete = threading.Event()
        self._idle = threading.Event()

        self._available = False
        self._language_available = {
            LANG_ENGLISH: False,
            LANG_KANNADA: False,
        }
        self._shutting_down = False
        self._voices: list[dict[str, Any]] = []
        self._active_process: subprocess.Popen[str] | None = None
        self._active_temp_paths: set[Path] = set()
        self._request_generation = 0
        self._cancelled_generation = 0
        self._speech_owner: str | None = None
        self._active_owner: str | None = None
        self._active_priority: int | None = None
        self._powershell_path = (
            shutil.which("powershell.exe") if os.name == "nt" else None
        )
        self._espeak_path = self._resolve_espeak_path()
        self._natural_kannada_available = False
        self._espeak_kannada_available = False
        self._kannada_python = self._resolve_kannada_python()
        self._kannada_helper = get_resource_path("utils/kannada_neural_tts.py")
        self._last_kannada_backend = "none"
        self._last_kannada_fallback = False
        self._kannada_server: subprocess.Popen[str] | None = None
        self._english_server: subprocess.Popen[str] | None = None
        self._wav_server: subprocess.Popen[str] | None = None
        cache_setting = Path(str(config.TTS_CACHE_DIR))
        self._cache_root = (
            cache_setting
            if cache_setting.is_absolute()
            else get_data_path(cache_setting)
        )
        self._bundled_cache_root = get_resource_path(cache_setting)
        self._fixed_kannada_items = get_fixed_kannada_speech_items()
        self._fixed_kannada_by_text = {
            text: speech_id for speech_id, text in self._fixed_kannada_items
        }
        self._fixed_kannada_cache_ready = self._report_fixed_kannada_cache_status()

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="GazeLinkWindowsTTS",
            daemon=True,
        )
        try:
            self._worker_thread.start()
        except RuntimeError as exc:
            self._print_error("worker startup", exc)
            self._initialization_complete.set()
            self._idle.set()
            return

        self._initialization_complete.wait(self.DIAGNOSTIC_TIMEOUT + 1.0)

    def speak(
        self,
        text: str,
        language: str | None = None,
        *,
        priority: int = PRIORITY_USER,
        owner: str | None = None,
        cache: bool = False,
    ) -> bool:
        """Queue speech with priority and optional screen ownership."""
        if text is None or not isinstance(text, str):
            return False

        message = text.strip()
        requested_language = language or self.get_language()
        if (
            not message
            or requested_language not in {LANG_ENGLISH, LANG_KANNADA}
            or not self.is_language_available(requested_language)
        ):
            return False

        priority = max(self.PRIORITY_USER, min(self.PRIORITY_INSTRUCTION, int(priority)))
        if priority == self.PRIORITY_USER:
            self.interrupt("user_communication")
        elif priority == self.PRIORITY_STATE:
            with self._state_lock:
                active_priority = self._active_priority
            if active_priority is not None and active_priority > priority:
                self.interrupt("important_state")

        with self._command_lock:
            if self._is_shutting_down():
                return False
            self._remove_queued_speech(minimum_priority=priority)
            with self._state_lock:
                if (
                    priority == self.PRIORITY_INSTRUCTION
                    and owner is not None
                    and owner != self._speech_owner
                ):
                    self._log("discarded stale instruction")
                    return False
                self._request_generation += 1
                request_generation = self._request_generation
            self._idle.clear()
            self._commands.put(
                (
                    "speak",
                    (
                        message,
                        requested_language,
                        request_generation,
                        priority,
                        owner,
                        bool(cache),
                    ),
                )
            )
        self._log(
            f"speaking priority={self._priority_name(priority)} "
            f"lang={requested_language} text={message[:60]}"
        )
        return True

    def speak_kannada_components(
        self,
        components,
        *,
        priority: int = PRIORITY_STATE,
        owner: str | None = None,
    ) -> bool:
        """Queue one interruptible readiness narration of cached short pieces."""
        messages = tuple(
            str(component).strip()
            for component in components
            if str(component).strip()
        )
        if not messages or not self.is_language_available(LANG_KANNADA):
            return False
        for message in messages:
            speech_id = self._fixed_kannada_by_text.get(message)
            if speech_id and speech_id.startswith("kn_score_"):
                score_number = int(speech_id.removeprefix("kn_score_"))
                self._fixed_cache_log(f"readiness score number = {score_number}")
                score_path = self._kannada_cache_path(message)
                try:
                    self._validate_wav(score_path)
                    self._fixed_cache_log("score audio found")
                except Exception:
                    self._fixed_cache_log("score audio missing; eSpeak fallback will be used")
                break
        priority = max(self.PRIORITY_USER, min(self.PRIORITY_INSTRUCTION, int(priority)))
        if priority == self.PRIORITY_USER:
            self.interrupt("user_communication")
        elif priority == self.PRIORITY_STATE:
            with self._state_lock:
                active_priority = self._active_priority
            if active_priority is not None and active_priority > priority:
                self.interrupt("important_state")
        with self._command_lock:
            if self._is_shutting_down():
                return False
            self._remove_queued_speech(minimum_priority=priority)
            with self._state_lock:
                self._request_generation += 1
                request_generation = self._request_generation
            self._idle.clear()
            self._commands.put((
                "speak_sequence",
                (messages, LANG_KANNADA, request_generation, priority, owner),
            ))
        self._fixed_cache_log("Kannada readiness narration")
        return True

    def set_owner(self, owner: str, reason: str = "screen_changed") -> None:
        """Cancel old-screen audio and make `owner` current for new speech."""
        normalized = str(owner)
        with self._state_lock:
            changed = normalized != self._speech_owner
            self._speech_owner = normalized
        if changed:
            self.interrupt(reason)

    def interrupt(self, reason: str = "interrupted") -> None:
        """Immediately stop active speech and discard all queued utterances."""
        if self._is_shutting_down():
            return
        with self._state_lock:
            self._cancelled_generation = self._request_generation
        with self._command_lock:
            self._remove_queued_speech()
        self._terminate_active_process()
        self._cleanup_active_temp_files()
        self._log(f"interrupt old speech: {reason}")

    def stop(self) -> None:
        """Stop active speech and discard speech that has not started."""
        if not self.is_available() or self._is_shutting_down():
            return

        self.interrupt("stop")

    def set_rate(self, rate: int) -> None:
        """Store a rate in the existing 80-250 API range."""
        with self._state_lock:
            self._rate = self._clamp_rate(rate)

    def set_volume(self, volume: float) -> None:
        """Store a volume in the existing 0.0-1.0 API range."""
        with self._state_lock:
            self._volume = self._clamp_volume(volume)

    def set_language(self, language: str) -> bool:
        """Select the backend used when speak() receives no language argument."""
        normalized = str(language).strip().lower()
        if normalized not in {LANG_ENGLISH, LANG_KANNADA}:
            return False
        with self._state_lock:
            self._language = normalized
        return self.is_language_available(normalized)

    def get_language(self) -> str:
        with self._state_lock:
            return self._language

    def get_available_voices(self) -> list[dict[str, Any]]:
        """Return cached System.Speech voice information."""
        with self._state_lock:
            return [
                {
                    "id": voice["id"],
                    "name": voice["name"],
                    "languages": list(voice["languages"]),
                    "gender": voice["gender"],
                }
                for voice in self._voices
            ]

    def set_voice(self, voice_id: str) -> bool:
        """Select a cached System.Speech voice by exact name."""
        if not isinstance(voice_id, str) or not voice_id or not self.is_available():
            return False

        with self._state_lock:
            if not any(voice["id"] == voice_id for voice in self._voices):
                return False
            self._voice_id = voice_id
        return True

    def is_available(self) -> bool:
        """Return whether at least one offline speech backend is usable."""
        with self._state_lock:
            return (
                self._available
                and not self._shutting_down
                and self._worker_thread.is_alive()
            )

    def is_language_available(self, language: str) -> bool:
        """Return availability for English (en) or Kannada (kn)."""
        normalized = str(language).strip().lower()
        with self._state_lock:
            return bool(
                self._language_available.get(normalized, False)
                and not self._shutting_down
                and self._worker_thread.is_alive()
            )

    def is_indicf5_available(self) -> bool:
        """Compatibility alias for the configured natural Kannada backend."""
        return self.is_natural_kannada_available()

    def is_natural_kannada_available(self) -> bool:
        """Return whether the dedicated Kannada neural environment is ready."""
        with self._state_lock:
            return bool(
                self._natural_kannada_available and not self._shutting_down
            )

    def get_kannada_backend(self) -> str:
        """Return the Kannada backend that would currently be selected."""
        configured = str(
            getattr(config, "KANNADA_TTS_BACKEND", "auto")
        ).strip().lower()
        if configured == "natural":
            configured = "indicf5"
        with self._state_lock:
            if configured != "espeak" and self._natural_kannada_available:
                return "natural"
            if self._espeak_kannada_available:
                return "espeak"
        return "text-only"

    def get_last_kannada_result(self) -> dict[str, Any]:
        """Return backend details for diagnostics without touching the worker."""
        with self._state_lock:
            return {
                "backend": self._last_kannada_backend,
                "fallback_used": self._last_kannada_fallback,
            }

    def wait_until_idle(self, timeout: float = 10.0) -> bool:
        """Wait until queued and active speech completes, mainly for tests."""
        try:
            wait_time = max(0.0, float(timeout))
        except (TypeError, ValueError):
            wait_time = 10.0
        return self._idle.wait(wait_time)

    def prepare_kannada_cache(self, text: str) -> bool:
        """Generate one fixed Kannada WAV without playing it."""
        message = text.strip() if isinstance(text, str) else ""
        if not message:
            return False
        path = self._writable_kannada_cache_path(message)
        try:
            if path.is_file():
                self._validate_wav(path)
                self._log("Kannada cache hit")
                return True
        except Exception:
            path.unlink(missing_ok=True)
        # One-time compatibility migration from the previous hash-only cache.
        legacy_path = self._legacy_kannada_cache_path(message)
        try:
            if legacy_path != path and legacy_path.is_file():
                self._validate_wav(legacy_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(legacy_path, path)
                self._validate_wav(path)
                self._fixed_cache_log(f"migrated cache to {path.name}")
                return True
        except Exception:
            path.unlink(missing_ok=True)
        if not self.is_natural_kannada_available():
            return False
        with self._command_lock:
            if self._is_shutting_down():
                return False
            with self._state_lock:
                self._request_generation += 1
                generation = self._request_generation
            self._idle.clear()
            self._commands.put(("cache_kannada", (message, generation, path)))
        return True

    def is_kannada_cached(self, text: str) -> bool:
        """Return whether a valid cached neural WAV exists for this text."""
        try:
            self._validate_wav(self._kannada_cache_path(text.strip()))
            return True
        except (AttributeError, RuntimeError, OSError):
            return False

    def shutdown(self) -> None:
        """Permanently stop speech and terminate the worker safely."""
        with self._state_lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            self._available = False
            self._cancelled_generation = self._request_generation

        with self._command_lock:
            self._clear_command_queue()
            self._idle.clear()
            if self._worker_thread.is_alive():
                self._commands.put(("shutdown", None))

        self._terminate_active_process()
        self._terminate_english_server()
        self._terminate_wav_server()
        self._terminate_kannada_server()
        self._cleanup_active_temp_files()
        if (
            self._worker_thread.is_alive()
            and threading.current_thread() is not self._worker_thread
        ):
            self._worker_thread.join(timeout=self.SHUTDOWN_TIMEOUT)
        self._idle.set()

    def _worker_loop(self) -> None:
        voices: list[dict[str, Any]] = []
        english_available = False
        kannada_available = False
        natural_available = False
        espeak_available = False
        try:
            voices = self._detect_voices()
            english_available = True
        except Exception as exc:
            self._print_error("initialization", exc)
        try:
            natural_available = self._detect_natural_kannada()
        except Exception as exc:
            self._print_error("natural Kannada initialization", exc)
        try:
            espeak_available = self._detect_espeak_kannada()
        except Exception as exc:
            self._print_error("Kannada initialization", exc)
        finally:
            kannada_available = (
                self._fixed_kannada_cache_ready
                or natural_available
                or espeak_available
            )
            with self._state_lock:
                self._voices = voices
                voice_ids = {voice["id"] for voice in voices}
                preferred_voice = self._voice_id or getattr(
                    config, "ENGLISH_TTS_VOICE", "Microsoft Zira Desktop"
                )
                self._voice_id = (
                    preferred_voice if preferred_voice in voice_ids else None
                )
                self._natural_kannada_available = natural_available
                self._espeak_kannada_available = espeak_available
                self._language_available[LANG_ENGLISH] = english_available
                self._language_available[LANG_KANNADA] = kannada_available
                self._available = english_available or kannada_available
        if english_available:
            try:
                self._ensure_english_server()
                self._log("English worker ready")
            except Exception as exc:
                self._print_error("persistent English worker", exc)
        if self._powershell_path:
            try:
                self._ensure_wav_server()
            except Exception as exc:
                self._print_error("persistent WAV worker", exc)
        self._initialization_complete.set()
        self._idle.set()

        if not (english_available or kannada_available):
            return

        while True:
            command, value = self._commands.get()
            try:
                if command == "shutdown":
                    break
                if command == "stop":
                    self._terminate_active_process()
                elif command == "speak":
                    (
                        text,
                        language,
                        request_generation,
                        priority,
                        owner,
                        cache_audio,
                    ) = value
                    if self._speech_is_stale(priority, owner):
                        self._log("discarded stale instruction")
                        continue
                    with self._state_lock:
                        self._active_priority = priority
                        self._active_owner = owner
                    if language == LANG_KANNADA:
                        self._speak_kannada(
                            text, request_generation, cache_audio=cache_audio
                        )
                    else:
                        self._speak_with_english_server(text, request_generation)
                elif command == "speak_sequence":
                    (
                        components,
                        language,
                        request_generation,
                        priority,
                        owner,
                    ) = value
                    if self._speech_is_stale(priority, owner):
                        self._log("discarded stale instruction")
                        continue
                    with self._state_lock:
                        self._active_priority = priority
                        self._active_owner = owner
                    for component in components:
                        if self._request_was_cancelled(request_generation):
                            break
                        if language == LANG_KANNADA:
                            self._speak_kannada(
                                component,
                                request_generation,
                                cache_audio=True,
                            )
                elif command == "cache_kannada":
                    text, request_generation, cache_path = value
                    self._speak_with_natural_kannada(
                        text,
                        request_generation,
                        cache_path=cache_path,
                        play_audio=False,
                    )
            except Exception as exc:
                self._print_error(command, exc)
            finally:
                with self._state_lock:
                    self._active_priority = None
                    self._active_owner = None
                self._commands.task_done()
                with self._command_lock:
                    if self._commands.empty() and self._get_active_process() is None:
                        self._idle.set()

        with self._state_lock:
            self._available = False
            self._language_available = {
                LANG_ENGLISH: False,
                LANG_KANNADA: False,
            }
        self._terminate_english_server()
        self._terminate_wav_server()
        self._terminate_kannada_server()
        self._idle.set()

    def _detect_voices(self) -> list[dict[str, Any]]:
        if os.name != "nt":
            raise RuntimeError("System.Speech is available only on Windows")
        if not self._powershell_path:
            raise RuntimeError("powershell.exe was not found")

        result = subprocess.run(
            self._powershell_command(self._VOICE_SCRIPT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.DIAGNOSTIC_TIMEOUT,
            creationflags=self._creation_flags(),
            check=False,
        )
        if result.returncode != 0:
            detail = self._short_error(result.stderr)
            raise RuntimeError(detail or "System.Speech could not be loaded")

        voices = []
        for encoded_name in result.stdout.splitlines():
            encoded_name = encoded_name.strip().lstrip("\ufeff")
            if not encoded_name:
                continue
            try:
                name = base64.b64decode(encoded_name).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
            voices.append(
                {
                    "id": name,
                    "name": name,
                    "languages": [],
                    "gender": "Unknown",
                }
            )
        return voices

    def _speak_with_powershell(self, text: str, request_generation: int) -> None:
        if self._request_was_cancelled(request_generation):
            return

        with self._state_lock:
            system_rate = self._map_rate(self._rate)
            volume_percent = max(0, min(100, int(self._volume * 100)))
            voice_id = self._voice_id or ""

        environment = os.environ.copy()
        environment.update(
            {
                "GAZELINK_TTS_TEXT": self._encode_text(text),
                "GAZELINK_TTS_VOICE": self._encode_text(voice_id),
                "GAZELINK_TTS_RATE": str(system_rate),
                "GAZELINK_TTS_VOLUME": str(volume_percent),
            }
        )

        process = subprocess.Popen(
            self._powershell_command(self._SPEAK_SCRIPT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            creationflags=self._creation_flags(),
        )
        with self._process_lock:
            self._active_process = process

        if self._request_was_cancelled(request_generation):
            self._terminate_process(process)

        try:
            _, stderr = process.communicate(timeout=self.SPEECH_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._terminate_process(process)
            raise RuntimeError("speech process timed out")
        finally:
            with self._process_lock:
                if self._active_process is process:
                    self._active_process = None

        if process.returncode != 0:
            if self._request_was_cancelled(request_generation) or self._is_shutting_down():
                return
            detail = self._short_error(stderr)
            raise RuntimeError(detail or "Windows speech process failed")

    def _speak_with_english_server(
        self, text: str, request_generation: int
    ) -> None:
        """Speak through one reusable System.Speech PowerShell process."""
        if self._request_was_cancelled(request_generation):
            return
        with self._state_lock:
            rate = self._map_rate(self._rate)
            volume = max(0, min(100, int(self._volume * 100)))
            voice = self._voice_id or ""
        try:
            server = self._ensure_english_server()
            if server.stdin is None or server.stdout is None:
                raise RuntimeError("English speech worker pipes are unavailable")
            request_id = str(request_generation)
            request = "|".join(
                (
                    request_id,
                    str(rate),
                    str(volume),
                    self._encode_text(voice),
                    self._encode_text(text),
                )
            )
            with self._process_lock:
                self._active_process = server
            try:
                server.stdin.write(request + "\n")
                server.stdin.flush()
                response = server.stdout.readline().strip()
            finally:
                with self._process_lock:
                    if self._active_process is server:
                        self._active_process = None
            if self._request_was_cancelled(request_generation):
                return
            if response == f"DONE|{request_id}":
                return
            if response.startswith(f"ERROR|{request_id}|"):
                try:
                    detail = base64.b64decode(response.split("|", 2)[2]).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    detail = "System.Speech worker reported an error"
                raise RuntimeError(detail)
            raise RuntimeError("persistent English speech worker stopped unexpectedly")
        except (BrokenPipeError, OSError, RuntimeError):
            if self._request_was_cancelled(request_generation):
                return
            self._terminate_english_server()
            # Retain the proven one-shot path if the persistent worker fails.
            get_logger().warning("TTS fallback used lang=en reason=worker-failure")
            self._speak_with_powershell(text, request_generation)

    def _ensure_english_server(self) -> subprocess.Popen[str]:
        server = self._english_server
        if server is not None and server.poll() is None:
            return server
        if not self._powershell_path:
            raise RuntimeError("powershell.exe was not found")
        server = subprocess.Popen(
            self._powershell_command(self._ENGLISH_SERVER_SCRIPT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=self._creation_flags(),
        )
        self._english_server = server
        if server.stdout is None or server.stdout.readline().strip() != "READY":
            self._terminate_english_server()
            raise RuntimeError("persistent English speech worker did not become ready")
        return server

    def _terminate_english_server(self) -> None:
        server = self._english_server
        self._english_server = None
        if server is None:
            return
        try:
            if server.poll() is None and server.stdin is not None:
                server.stdin.write("SHUTDOWN\n")
                server.stdin.flush()
                server.wait(timeout=self.PROCESS_STOP_TIMEOUT)
                return
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            pass
        self._terminate_process(server)

    def _speak_with_espeak(self, text: str, request_generation: int) -> None:
        """Synthesize Kannada from a UTF-8 file, validate, then play its WAV."""
        if self._request_was_cancelled(request_generation):
            return
        if not self._espeak_path:
            raise RuntimeError("eSpeak NG executable was not found")
        try:
            temp_directory = Path(tempfile.mkdtemp(prefix="gazelink_kannada_"))
            text_path = temp_directory / "speech.txt"
            wav_path = temp_directory / "speech.wav"
            self._register_temp_paths(temp_directory, text_path, wav_path)

            # Explicit UTF-8 file input avoids Windows command-line Unicode
            # conversion that can make eSpeak NG generate a silent WAV.
            with text_path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(text)

            synthesis_command = [
                self._espeak_path,
                "-v", "kn",
                "-b", "1",
                "-s", str(max(80, min(250, int(config.KANNADA_TTS_RATE)))),
                "-p", str(max(0, min(99, int(config.KANNADA_TTS_PITCH)))),
                "-a", str(max(0, min(200, int(config.KANNADA_TTS_VOLUME)))),
                "-f", str(text_path),
                "-w", str(wav_path),
            ]
            self._run_tracked_process(
                synthesis_command,
                request_generation,
                "Kannada synthesis",
            )
            if self._request_was_cancelled(request_generation):
                return
            self._validate_wav(wav_path)
            if not self._powershell_path:
                raise RuntimeError("powershell.exe was not found for WAV playback")

            environment = os.environ.copy()
            environment["GAZELINK_WAV_PATH"] = self._encode_text(str(wav_path))
            self._run_tracked_process(
                self._powershell_command(self._PLAY_WAV_SCRIPT),
                request_generation,
                "Kannada WAV playback",
                environment=environment,
            )
        finally:
            self._cleanup_active_temp_files()

    def _speak_kannada(
        self,
        text: str,
        request_generation: int,
        *,
        cache_audio: bool = False,
    ) -> None:
        """Prefer configured local neural speech and retain eSpeak fallback."""
        backend = str(
            getattr(config, "KANNADA_TTS_BACKEND", "auto")
        ).strip().lower()
        if backend == "natural":
            backend = "indicf5"
        if backend not in {"auto", "indicf5", "espeak"}:
            backend = "auto"
        fixed_id = self._fixed_kannada_by_text.get(text)
        cache_path = self._kannada_cache_path(text)
        if fixed_id is not None and backend != "espeak":
            self._fixed_cache_log(f"request id={fixed_id}")
            try:
                self._validate_wav(cache_path)
                self._fixed_cache_log(f"component cache hit: {fixed_id}")
                self._fixed_cache_log("playback started")
                self._play_wav(cache_path, request_generation, "Kannada cache playback")
                with self._state_lock:
                    self._last_kannada_backend = "natural-cache"
                    self._last_kannada_fallback = False
                return
            except Exception as exc:
                self._fixed_cache_log(f"cache missing/unusable: {fixed_id}")
                try:
                    cache_path.unlink(missing_ok=True)
                except OSError:
                    pass
            # Fixed patient speech must never load the neural model on demand.
            # The fast local fallback speaks now; the preparation utility can
            # generate the named neural WAV later.
            if self._espeak_kannada_available:
                self._fixed_cache_log("using immediate eSpeak fallback")
                get_logger().warning(
                    "TTS fallback used lang=kn reason=fixed-cache-missing id=%s",
                    fixed_id,
                )
                self._speak_with_espeak(text, request_generation)
                with self._state_lock:
                    self._last_kannada_backend = "espeak"
                    self._last_kannada_fallback = True
                return
            raise RuntimeError(
                f"fixed Kannada cache is missing and eSpeak is unavailable: {fixed_id}"
            )
        if backend != "espeak" and cache_audio and cache_path.is_file():
            try:
                self._validate_wav(cache_path)
                self._log("Kannada cache hit")
                self._play_wav(cache_path, request_generation, "Kannada cache playback")
                with self._state_lock:
                    self._last_kannada_backend = "natural-cache"
                    self._last_kannada_fallback = False
                return
            except Exception as exc:
                self._print_error("Kannada cache playback", exc)
                try:
                    cache_path.unlink(missing_ok=True)
                except OSError:
                    pass
        if backend != "espeak" and self._natural_kannada_available:
            try:
                if cache_audio:
                    self._log("Kannada cache miss -> generating")
                self._speak_with_natural_kannada(
                    text,
                    request_generation,
                    cache_path=cache_path if cache_audio else None,
                )
                with self._state_lock:
                    self._last_kannada_backend = "natural"
                    self._last_kannada_fallback = False
                return
            except Exception as exc:
                self._print_error("natural Kannada speech", exc)
                if self._request_was_cancelled(request_generation):
                    return
        if self._espeak_kannada_available:
            if backend != "espeak":
                get_logger().warning("TTS fallback used lang=kn reason=neural-unavailable")
            self._speak_with_espeak(text, request_generation)
            with self._state_lock:
                self._last_kannada_backend = "espeak"
                self._last_kannada_fallback = backend != "espeak"
            return
        raise RuntimeError("no offline Kannada voice is available")

    def _speak_with_natural_kannada(
        self,
        text: str,
        request_generation: int,
        *,
        cache_path: Path | None = None,
        play_audio: bool = True,
    ) -> None:
        """Invoke the tested VITS model through the gazelink_tts Python."""
        if self._request_was_cancelled(request_generation):
            return
        temp_directory = Path(tempfile.mkdtemp(prefix="gazelink_kannada_neural_"))
        text_path = temp_directory / "speech.txt"
        wav_path = temp_directory / "speech.wav"
        status_path = temp_directory / "status.json"
        self._register_temp_paths(
            temp_directory, text_path, wav_path, status_path
        )
        try:
            text_path.write_text(text, encoding="utf-8")
            server = self._ensure_kannada_server()
            request = json.dumps(
                {
                    "input_file": str(text_path),
                    "output_wav": str(wav_path),
                    "status_file": str(status_path),
                },
                ensure_ascii=True,
            )
            with self._process_lock:
                self._active_process = server
            try:
                if server.stdin is None:
                    raise RuntimeError("Kannada model server input is unavailable")
                server.stdin.write(request + "\n")
                server.stdin.flush()
                deadline = time.monotonic() + float(
                    config.KANNADA_NATURAL_TIMEOUT_SECONDS
                )
                while not status_path.is_file():
                    if self._request_was_cancelled(request_generation):
                        self._terminate_kannada_server()
                        return
                    if server.poll() is not None:
                        raise RuntimeError("Kannada model server stopped unexpectedly")
                    if time.monotonic() >= deadline:
                        self._terminate_kannada_server()
                        raise RuntimeError("natural Kannada synthesis timed out")
                    time.sleep(0.05)
            finally:
                with self._process_lock:
                    if self._active_process is server:
                        self._active_process = None
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if not status.get("ok"):
                raise RuntimeError(
                    status.get("error") or "natural Kannada synthesis failed"
                )
            if self._request_was_cancelled(request_generation):
                return
            self._validate_wav(wav_path)
            playback_path = wav_path
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                pending_path = cache_path.with_suffix(".tmp.wav")
                try:
                    shutil.copyfile(wav_path, pending_path)
                    os.replace(pending_path, cache_path)
                finally:
                    pending_path.unlink(missing_ok=True)
                playback_path = cache_path
            if play_audio:
                self._play_wav(
                    playback_path,
                    request_generation,
                    "natural Kannada WAV playback",
                )
        finally:
            self._cleanup_active_temp_files()

    def _play_wav(
        self, path: Path, request_generation: int, action: str
    ) -> None:
        if not self._powershell_path:
            raise RuntimeError("powershell.exe was not found for WAV playback")
        try:
            server = self._ensure_wav_server()
            if server.stdin is None or server.stdout is None:
                raise RuntimeError("WAV playback worker pipes are unavailable")
            request_id = str(request_generation)
            with self._process_lock:
                self._active_process = server
            try:
                server.stdin.write(
                    f"{request_id}|{self._encode_text(str(path))}\n"
                )
                server.stdin.flush()
                response = server.stdout.readline().strip()
            finally:
                with self._process_lock:
                    if self._active_process is server:
                        self._active_process = None
            if self._request_was_cancelled(request_generation):
                return
            if response == f"DONE|{request_id}":
                return
            raise RuntimeError(f"{action} worker failed")
        except (BrokenPipeError, OSError, RuntimeError):
            if self._request_was_cancelled(request_generation):
                return
            self._terminate_wav_server()
            environment = os.environ.copy()
            environment["GAZELINK_WAV_PATH"] = self._encode_text(str(path))
            self._run_tracked_process(
                self._powershell_command(self._PLAY_WAV_SCRIPT),
                request_generation,
                action,
                environment=environment,
            )

    def _ensure_wav_server(self) -> subprocess.Popen[str]:
        server = self._wav_server
        if server is not None and server.poll() is None:
            return server
        server = subprocess.Popen(
            self._powershell_command(self._WAV_SERVER_SCRIPT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=self._creation_flags(),
        )
        self._wav_server = server
        if server.stdout is None or server.stdout.readline().strip() != "READY":
            self._terminate_wav_server()
            raise RuntimeError("persistent WAV worker did not become ready")
        return server

    def _terminate_wav_server(self) -> None:
        server = self._wav_server
        self._wav_server = None
        if server is None:
            return
        try:
            if server.poll() is None and server.stdin is not None:
                server.stdin.write("SHUTDOWN\n")
                server.stdin.flush()
                server.wait(timeout=self.PROCESS_STOP_TIMEOUT)
                return
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            pass
        self._terminate_process(server)

    def _kannada_cache_path(self, text: str) -> Path:
        fixed_id = self._fixed_kannada_by_text.get(text.strip())
        if fixed_id is not None:
            relative = Path(LANG_KANNADA) / f"{fixed_id}.wav"
            writable = self._cache_root / relative
            bundled = self._bundled_cache_root / relative
            if writable.is_file() or not bundled.is_file():
                return writable
            return bundled
        return self._legacy_kannada_cache_path(text)

    def _writable_kannada_cache_path(self, text: str) -> Path:
        fixed_id = self._fixed_kannada_by_text.get(text.strip())
        if fixed_id is not None:
            return self._cache_root / LANG_KANNADA / f"{fixed_id}.wav"
        return self._legacy_kannada_cache_path(text)

    def _legacy_kannada_cache_path(self, text: str) -> Path:
        payload = json.dumps(
            {
                "language": LANG_KANNADA,
                "model": str(config.KANNADA_TTS_MODEL),
                "speaker_id": int(config.KANNADA_TTS_SPEAKER_ID),
                "emotion_id": int(config.KANNADA_TTS_EMOTION_ID),
                "text": text,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        return self._cache_root / LANG_KANNADA / f"{digest}.wav"

    def _report_fixed_kannada_cache_status(self) -> bool:
        """Perform a fast existence check without starting neural synthesis."""
        missing = []
        for speech_id, text in self._fixed_kannada_items:
            path = self._kannada_cache_path(text)
            try:
                ready = path.is_file() and path.stat().st_size > 44
            except OSError:
                ready = False
            if not ready:
                missing.append(speech_id)
        if not missing:
            print("[TTS] Kannada fixed cache ready")
            return True
        print(
            f"[TTS] Kannada fixed cache missing {len(missing)}/"
            f"{len(self._fixed_kannada_items)} files: {', '.join(missing)}"
        )
        return False

    def _run_tracked_process(
        self,
        command: list[str],
        request_generation: int,
        action: str,
        *,
        environment: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> None:
        """Run one cancellable synthesis/playback process on the TTS worker."""
        if self._request_was_cancelled(request_generation):
            return
        process = subprocess.Popen(
            command,
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            creationflags=self._creation_flags(),
        )
        with self._process_lock:
            self._active_process = process
        if self._request_was_cancelled(request_generation):
            self._terminate_process(process)
        try:
            _, stderr = process.communicate(
                timeout=self.SPEECH_TIMEOUT if timeout is None else timeout
            )
        except subprocess.TimeoutExpired:
            self._terminate_process(process)
            raise RuntimeError(f"{action} timed out")
        finally:
            with self._process_lock:
                if self._active_process is process:
                    self._active_process = None
        if process.returncode != 0:
            if self._request_was_cancelled(request_generation) or self._is_shutting_down():
                return
            detail = self._short_error(stderr)
            raise RuntimeError(detail or f"{action} failed")

    @staticmethod
    def _validate_wav(path: Path) -> None:
        """Reject missing, malformed, empty, or completely silent PCM WAVs."""
        if not path.is_file() or path.stat().st_size <= 44:
            raise RuntimeError("Kannada synthesis produced no WAV audio")
        try:
            with wave.open(str(path), "rb") as wav_file:
                frame_count = wav_file.getnframes()
                sample_width = wav_file.getsampwidth()
                compression = wav_file.getcomptype()
                frames = wav_file.readframes(frame_count)
        except (OSError, EOFError, wave.Error) as exc:
            raise RuntimeError(f"Kannada synthesis produced an invalid WAV: {exc}") from exc
        if frame_count <= 0 or not frames:
            raise RuntimeError("Kannada synthesis produced an empty WAV")
        if compression != "NONE":
            raise RuntimeError("Kannada synthesis produced an unsupported compressed WAV")
        if not TextToSpeech._pcm_has_nonzero_amplitude(frames, sample_width):
            raise RuntimeError("Kannada synthesis produced a silent WAV")

    @staticmethod
    def _pcm_has_nonzero_amplitude(frames: bytes, sample_width: int) -> bool:
        if sample_width == 1:
            return any(sample != 128 for sample in frames)
        if sample_width in (2, 4):
            typecode = "h" if sample_width == 2 else "i"
            samples = array(typecode)
            usable = len(frames) - (len(frames) % sample_width)
            samples.frombytes(frames[:usable])
            if sys.byteorder != "little":
                samples.byteswap()
            return any(sample != 0 for sample in samples)
        if sample_width == 3:
            return any(
                int.from_bytes(frames[index:index + 3], "little", signed=True) != 0
                for index in range(0, len(frames) - 2, 3)
            )
        raise RuntimeError(f"Unsupported WAV sample width: {sample_width}")

    def _register_temp_paths(self, *paths: Path) -> None:
        with self._temp_lock:
            self._active_temp_paths.update(paths)

    def _cleanup_active_temp_files(self) -> None:
        """Remove Kannada text/WAV files, retrying brief Windows file locks."""
        with self._temp_lock:
            paths = sorted(
                self._active_temp_paths,
                key=lambda item: len(item.parts),
                reverse=True,
            )
        for path in paths:
            removed = False
            for attempt in range(3):
                try:
                    if path.is_dir():
                        path.rmdir()
                    else:
                        path.unlink(missing_ok=True)
                    removed = True
                    break
                except OSError:
                    if attempt < 2:
                        time.sleep(0.10)
            if removed:
                with self._temp_lock:
                    self._active_temp_paths.discard(path)
            else:
                print(f"Kannada TTS cleanup warning: could not remove {path}")

    def _resolve_espeak_path(self) -> str | None:
        configured = getattr(config, "ESPEAK_NG_PATH", None)
        if configured:
            candidate = Path(str(configured)).expanduser()
            if candidate.is_file():
                return str(candidate)
        return shutil.which("espeak-ng")

    @staticmethod
    def _resolve_kannada_python() -> str:
        """Find the separate local Conda Python without a user-specific path."""
        configured = getattr(config, "KANNADA_TTS_PYTHON", None)
        environment_override = os.environ.get("GAZELINK_TTS_PYTHON")
        environment_name = str(
            getattr(config, "KANNADA_TTS_ENVIRONMENT", "gazelink_tts")
        )
        candidates: list[Path] = []
        for value in (environment_override, configured):
            if value:
                candidates.append(Path(str(value)).expanduser())

        roots: list[Path] = []
        conda_prefix = os.environ.get("CONDA_PREFIX")
        if conda_prefix:
            prefix = Path(conda_prefix)
            roots.extend((prefix, prefix.parent))
        conda_executable = os.environ.get("CONDA_EXE") or shutil.which("conda.exe")
        if conda_executable:
            conda_path = Path(conda_executable)
            roots.extend((conda_path.parent, conda_path.parent.parent))
        current_python = Path(sys.executable)
        roots.extend((current_python.parent, current_python.parent.parent))
        user_home = Path.home()
        program_data = Path(
            os.environ.get(
                "PROGRAMDATA",
                str(Path(os.environ.get("SystemDrive", "C:")) / "ProgramData"),
            )
        )
        roots.extend((
            user_home / "anaconda3",
            user_home / "miniconda3",
            program_data / "Anaconda3",
            Path(os.environ.get("LOCALAPPDATA", user_home)) / "miniconda3",
        ))

        executable_name = "python.exe" if os.name == "nt" else "python"
        for root in roots:
            candidates.extend((
                root / "envs" / environment_name / executable_name,
                root / environment_name / executable_name,
            ))
        seen: set[str] = set()
        for candidate in candidates:
            normalized = str(candidate.resolve())
            if normalized in seen:
                continue
            seen.add(normalized)
            if candidate.is_file():
                return str(candidate)
        return ""

    def _detect_espeak_kannada(self) -> bool:
        if not self._espeak_path:
            return False
        result = subprocess.run(
            [self._espeak_path, "--voices=kn"],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.DIAGNOSTIC_TIMEOUT,
            creationflags=self._creation_flags(),
            check=False,
        )
        return result.returncode == 0 and "kn" in result.stdout.casefold()

    def _detect_natural_kannada(self) -> bool:
        backend = str(
            getattr(config, "KANNADA_TTS_BACKEND", "auto")
        ).strip().lower()
        if backend == "natural":
            backend = "indicf5"
        if backend == "espeak":
            return False
        python_path = Path(self._kannada_python)
        if not python_path.is_file() or not self._kannada_helper.is_file():
            return False
        result = subprocess.run(
            [self._kannada_python, str(self._kannada_helper), "--check"],
            shell=False,
            capture_output=True,
            timeout=self.DIAGNOSTIC_TIMEOUT,
            creationflags=self._creation_flags(),
            check=False,
        )
        return result.returncode == 0

    def _ensure_kannada_server(self) -> subprocess.Popen[str]:
        server = self._kannada_server
        if server is not None and server.poll() is None:
            return server
        command = [
            self._kannada_python,
            str(self._kannada_helper),
            "--server",
            "--model", str(config.KANNADA_TTS_MODEL),
            "--speaker-id", str(config.KANNADA_TTS_SPEAKER_ID),
            "--emotion-id", str(config.KANNADA_TTS_EMOTION_ID),
        ]
        server = subprocess.Popen(
            command,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            creationflags=self._creation_flags(),
        )
        self._kannada_server = server
        return server

    def _terminate_kannada_server(self) -> None:
        server = self._kannada_server
        self._kannada_server = None
        if server is None:
            return
        try:
            if server.stdin is not None:
                server.stdin.close()
        except OSError:
            pass
        self._terminate_process(server)

    def _terminate_active_process(self) -> None:
        process = self._get_active_process()
        if process is not None:
            self._terminate_process(process)

    def _get_active_process(self) -> subprocess.Popen[str] | None:
        with self._process_lock:
            process = self._active_process
        if process is None or process.poll() is not None:
            return None
        return process

    def _terminate_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=self.PROCESS_STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=self.PROCESS_STOP_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass

    def _remove_queued_speech(self, minimum_priority: int | None = None) -> None:
        """Drop queued speech at or below the new request's importance."""
        retained_commands = []
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                break
            speech_command = command[0] in {"speak", "speak_sequence"}
            retain = not speech_command
            if speech_command and minimum_priority is not None:
                queued_priority = int(command[1][3])
                # Numerically lower means more important.
                retain = queued_priority < minimum_priority
            if retain:
                retained_commands.append(command)
            self._commands.task_done()
        for command in retained_commands:
            self._commands.put(command)

    def _clear_command_queue(self) -> None:
        while True:
            try:
                self._commands.get_nowait()
            except queue.Empty:
                return
            self._commands.task_done()

    def _is_shutting_down(self) -> bool:
        with self._state_lock:
            return self._shutting_down

    def _request_was_cancelled(self, request_generation: int) -> bool:
        with self._state_lock:
            return request_generation <= self._cancelled_generation

    def _speech_is_stale(self, priority: int, owner: str | None) -> bool:
        if priority != self.PRIORITY_INSTRUCTION or owner is None:
            return False
        with self._state_lock:
            return owner != self._speech_owner

    @classmethod
    def _priority_name(cls, priority: int) -> str:
        return {
            cls.PRIORITY_USER: "USER",
            cls.PRIORITY_STATE: "STATE",
            cls.PRIORITY_INSTRUCTION: "INSTRUCTION",
        }.get(priority, str(priority))

    @staticmethod
    def _log(message: str) -> None:
        if bool(getattr(config, "TTS_DEBUG", False)):
            output = f"[TTS] {message}"
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(output.encode(encoding, errors="replace").decode(encoding))

    @staticmethod
    def _fixed_cache_log(message: str) -> None:
        """Always expose fixed-cache routing without requiring debug UI."""
        output = f"[TTS] {message}"
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(output.encode(encoding, errors="replace").decode(encoding))

    def _powershell_command(self, script: str) -> list[str]:
        return [
            self._powershell_path,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ]

    @staticmethod
    def _encode_text(text: str) -> str:
        return base64.b64encode(text.encode("utf-8")).decode("ascii")

    @classmethod
    def _map_rate(cls, rate: int) -> int:
        rate = cls._clamp_rate(rate)
        points = ((80, -5), (150, 0), (200, 3), (250, 5))
        for (low_rate, low_value), (high_rate, high_value) in zip(
            points,
            points[1:],
        ):
            if rate <= high_rate:
                fraction = (rate - low_rate) / (high_rate - low_rate)
                mapped = low_value + fraction * (high_value - low_value)
                return max(-10, min(10, round(mapped)))
        return 5

    @classmethod
    def _clamp_rate(cls, rate: int) -> int:
        try:
            numeric_rate = int(rate)
        except (TypeError, ValueError):
            numeric_rate = 140
        return max(cls.MIN_RATE, min(cls.MAX_RATE, numeric_rate))

    @classmethod
    def _clamp_volume(cls, volume: float) -> float:
        try:
            numeric_volume = float(volume)
        except (TypeError, ValueError):
            numeric_volume = 1.0
        return max(cls.MIN_VOLUME, min(cls.MAX_VOLUME, numeric_volume))

    @staticmethod
    def _creation_flags() -> int:
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

    @staticmethod
    def _short_error(stderr: str | None) -> str:
        if not stderr:
            return ""
        lines = [line.strip() for line in stderr.splitlines() if line.strip()]
        return lines[-1] if lines else ""

    @staticmethod
    def _print_error(action: str, error: Exception) -> None:
        print(f"Offline TTS {action} failed: {error}")
        get_logger().warning("offline TTS %s failed: %s", action, error)


if __name__ == "__main__":
    natural_test = "--test-kannada-natural" in sys.argv
    kannada_test = "--test-kannada" in sys.argv
    prepare_cache = "--prepare-kannada-cache" in sys.argv
    if natural_test or prepare_cache:
        config.KANNADA_TTS_BACKEND = "indicf5"
    elif kannada_test:
        config.KANNADA_TTS_BACKEND = "espeak"
    tts = TextToSpeech()
    if prepare_cache:
        items = get_fixed_kannada_speech_items()
        print(f"Preparing {len(items)} fixed Kannada speech files...")
        if not tts.is_natural_kannada_available():
            print(
                "Natural Kannada TTS is unavailable; existing legacy WAVs "
                "will still be migrated to stable names."
            )
        completed = 0
        for index, (speech_id, message) in enumerate(items, start=1):
            accepted = tts.prepare_kannada_cache(message)
            if (
                accepted
                and tts.wait_until_idle(timeout=240.0)
                and tts.is_kannada_cached(message)
            ):
                completed += 1
            print(f"Kannada cache: {index}/{len(items)} {speech_id}")
        print(f"Kannada cache prepared: {completed}/{len(items)} files.")
        tts.shutdown()
    elif natural_test:
        print(
            f"Natural Kannada available: "
            f"{tts.is_natural_kannada_available()}"
        )
        print(f"Model: {config.KANNADA_TTS_MODEL}")
        print(f"Selected Kannada backend: {tts.get_kannada_backend()}")
        print(f"Speaker: KAN_F / ID {config.KANNADA_TTS_SPEAKER_ID}")
        print(f"Emotion/style ID: {config.KANNADA_TTS_EMOTION_ID}")
        for message in ("ನಮಸ್ಕಾರ", "ನನಗೆ ಸಹಾಯ ಬೇಕು", "ನನಗೆ ನೀರು ಬೇಕು"):
            started = time.monotonic()
            accepted = tts.speak(message, LANG_KANNADA)
            completed = accepted and tts.wait_until_idle(timeout=240.0)
            print(
                f"Synthesis time: {time.monotonic() - started:.2f}s "
                f"({'completed' if completed else 'unavailable/timeout'})"
            )
        result = tts.get_last_kannada_result()
        print(f"Fallback used: {result['fallback_used']}")
        tts.shutdown()
        print("Natural Kannada TTS test completed.")
    elif kannada_test:
        print(f"Kannada TTS available: {tts.is_language_available(LANG_KANNADA)}")
        print(f"Selected Kannada backend: {tts.get_kannada_backend()}")
        for message in ("ನಮಸ್ಕಾರ", "ನನಗೆ ಸಹಾಯ ಬೇಕು", "ನನಗೆ ನೀರು ಬೇಕು"):
            if tts.speak(message, LANG_KANNADA):
                tts.wait_until_idle(timeout=15.0)
            else:
                print("Kannada offline voice is not installed. Configure eSpeak NG.")
                break
        tts.shutdown()
        print("Kannada offline TTS test completed.")
    else:
        print(f"English TTS available: {tts.is_language_available(LANG_ENGLISH)}")
        print("Installed voices:", [
            voice["name"] for voice in tts.get_available_voices()
        ])
        for message in (
            "GazeLink first speech test.",
            "GazeLink second speech test.",
            "GazeLink third speech test successful.",
            "Help. I need water.",
        ):
            if tts.speak(message, LANG_ENGLISH):
                tts.wait_until_idle(timeout=15.0)
        tts.shutdown()
        print("Windows repeated TTS test completed.")
