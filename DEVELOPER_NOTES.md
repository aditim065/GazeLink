# GazeLink developer notes

The main application uses Python 3.11 and the dependencies in `requirements.txt`.

Natural Kannada speech is intentionally isolated from the main environment. It
runs in the separate Conda environment named `gazelink_tts`, which contains the
local AI4Bharat model dependencies (including PyTorch, Transformers, and
SoundFile). Set `GAZELINK_TTS_PYTHON` to that environment's Python executable
when automatic Conda discovery is not available. Normal startup never downloads
models or contacts an online service; cached WAV files and local fallbacks are
used.
