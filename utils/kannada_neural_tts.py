"""Generate Kannada WAV audio in the dedicated gazelink_tts environment."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


EXIT_DEPENDENCY_ERROR = 2
EXIT_INPUT_ERROR = 3
EXIT_MODEL_ERROR = 4
EXIT_SYNTHESIS_ERROR = 5


def _dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(name) is not None
        for name in ("torch", "transformers", "soundfile")
    )


def _error(message: str, code: int) -> int:
    print(f"Kannada neural TTS: {message}", file=sys.stderr)
    return code


def load_runtime(model_id: str):
    import torch
    from transformers import AutoConfig, AutoModel, AutoTokenizer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model_config = AutoConfig.from_pretrained(
        model_id, trust_remote_code=True, local_files_only=True
    )
    if getattr(model_config, "pad_token_id", None) is None:
        model_config.pad_token_id = 0
    model = AutoModel.from_pretrained(
        model_id, config=model_config, trust_remote_code=True,
        local_files_only=True,
    ).to(device)
    tokenizer = AutoTokenizer.from_pretrained(
        model_id, trust_remote_code=True, local_files_only=True
    )
    model.eval()
    return torch, model, tokenizer, device


def generate(
    runtime,
    text_path: Path,
    wav_path: Path,
    speaker_id_value: int,
    emotion_id_value: int,
) -> None:
    import soundfile as sf

    torch, model, tokenizer, device = runtime
    text = text_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("input text is empty")
    inputs = tokenizer(text=text, return_tensors="pt")
    input_ids = inputs["input_ids"].to(device)
    speaker_id = torch.tensor([speaker_id_value], device=device)
    emotion_id = torch.tensor([emotion_id_value], device=device)
    with torch.no_grad():
        outputs = model(
            input_ids,
            speaker_id=speaker_id,
            emotion_id=emotion_id,
        )
    audio = outputs.waveform.squeeze().detach().cpu().numpy()
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(wav_path), audio, model.config.sampling_rate)


def synthesize(
    text_path: Path,
    wav_path: Path,
    model_id: str,
    speaker_id_value: int,
    emotion_id_value: int,
) -> int:
    try:
        runtime = load_runtime(model_id)
    except Exception as exc:
        return _error(f"model load failed: {exc}", EXIT_MODEL_ERROR)
    try:
        generate(
            runtime, text_path, wav_path,
            speaker_id_value, emotion_id_value,
        )
    except Exception as exc:
        return _error(f"synthesis failed: {exc}", EXIT_SYNTHESIS_ERROR)
    return 0


def serve(model_id: str, speaker_id: int, emotion_id: int) -> int:
    """Load the model once, then receive file-based requests over stdin."""
    try:
        runtime = load_runtime(model_id)
    except Exception as exc:
        return _error(f"model load failed: {exc}", EXIT_MODEL_ERROR)
    for line in sys.stdin:
        if not line.strip():
            continue
        status_path = None
        try:
            request = json.loads(line)
            status_path = Path(request["status_file"])
            generate(
                runtime,
                Path(request["input_file"]),
                Path(request["output_wav"]),
                speaker_id,
                emotion_id,
            )
            status = {"ok": True}
        except Exception as exc:
            status = {"ok": False, "error": str(exc)}
        if status_path is not None:
            status_path.write_text(
                json.dumps(status, ensure_ascii=False), encoding="utf-8"
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--input-file")
    parser.add_argument("--output-wav")
    parser.add_argument("--model", default="ai4bharat/vits_rasa_13")
    parser.add_argument("--speaker-id", type=int, default=8)
    parser.add_argument("--emotion-id", type=int, default=4)
    args = parser.parse_args()
    if args.check:
        return 0 if _dependencies_available() else EXIT_DEPENDENCY_ERROR
    if args.server:
        return serve(args.model, args.speaker_id, args.emotion_id)
    if not args.input_file or not args.output_wav:
        return _error("input and output paths are required", EXIT_INPUT_ERROR)
    return synthesize(
        Path(args.input_file),
        Path(args.output_wav),
        args.model,
        args.speaker_id,
        args.emotion_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
