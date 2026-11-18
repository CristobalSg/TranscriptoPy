"""Extrae audio con FFmpeg y lo fragmenta con la biblioteca estándar wave."""

from __future__ import annotations

import math
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16 bits


def _default_log(text: str) -> None:
    print(text, flush=True)


@dataclass(frozen=True)
class AudioPart:
    index: int
    path: Path
    offset_seconds: float
    duration_seconds: float


def check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "No se encontró FFmpeg en el PATH. Instálalo y comprueba 'ffmpeg -version'."
        )


def _valid_wav(path: Path, expected_frames: int | None = None) -> bool:
    if not path.is_file():
        return False
    try:
        with wave.open(str(path), "rb") as source:
            return (
                source.getnchannels() == CHANNELS
                and source.getsampwidth() == SAMPLE_WIDTH
                and source.getframerate() == SAMPLE_RATE
                and source.getnframes() > 0
                and (expected_frames is None or source.getnframes() == expected_frames)
            )
    except (OSError, EOFError, wave.Error):
        return False


def extract_audio(
    source: Path, audio_file: Path, log: Callable[[str], None] | None = None
) -> None:
    """Produce WAV mono 16 kHz PCM, reusando solamente un archivo WAV válido."""
    log = log or _default_log
    if _valid_wav(audio_file):
        log(f"[Audio] Se reutiliza: {audio_file}")
        return
    check_ffmpeg()
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = audio_file.with_name(audio_file.stem + ".tmp.wav")
    temporary.unlink(missing_ok=True)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(source), "-map", "0:a:0", "-vn",
        "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le", str(temporary),
    ]
    log("[Audio] Extrayendo y normalizando el audio del video...")
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            explanation = result.stderr.strip().splitlines()[-8:]
            raise RuntimeError(
                "FFmpeg no pudo extraer el audio. Comprueba que el archivo tenga sonido.\n"
                + "\n".join(explanation)
            )
        if not _valid_wav(temporary):
            raise RuntimeError("El WAV generado está vacío o es inválido.")
        temporary.replace(audio_file)
    finally:
        temporary.unlink(missing_ok=True)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / source.getframerate()


def make_parts(
    audio_file: Path, output_dir: Path, fragment_minutes: int,
    log: Callable[[str], None] | None = None,
) -> list[AudioPart]:
    """Divide en partes exactas de N minutos. N=0 procesa el WAV completo."""
    log = log or _default_log
    if fragment_minutes < 0:
        raise ValueError("Los minutos por fragmento no pueden ser negativos.")
    total_seconds = wav_duration(audio_file)
    if fragment_minutes == 0:
        return [AudioPart(1, audio_file, 0.0, total_seconds)]

    frames_per_part = fragment_minutes * 60 * SAMPLE_RATE
    output_dir.mkdir(parents=True, exist_ok=True)
    parts: list[AudioPart] = []

    with wave.open(str(audio_file), "rb") as source:
        total_frames = source.getnframes()
        params = source.getparams()
        number_of_parts = math.ceil(total_frames / frames_per_part)
        for i in range(number_of_parts):
            first_frame = i * frames_per_part
            frames_this_part = min(frames_per_part, total_frames - first_frame)
            path = output_dir / f"parte_{i+1:03d}.wav"
            parts.append(AudioPart(
                i + 1,
                path,
                first_frame / SAMPLE_RATE,
                frames_this_part / SAMPLE_RATE,
            ))
            if _valid_wav(path, expected_frames=frames_this_part):
                log(f"[Audio] Fragmento {i+1}/{number_of_parts}: ya existe")
                continue

            temporary = output_dir / f"parte_{i+1:03d}.tmp.wav"
            temporary.unlink(missing_ok=True)
            try:
                source.setpos(first_frame)
                with wave.open(str(temporary), "wb") as target:
                    target.setparams(params)
                    remaining = frames_this_part
                    while remaining:
                        frames = min(remaining, 10 * SAMPLE_RATE)
                        block = source.readframes(frames)
                        if not block:
                            raise RuntimeError(f"Fin inesperado leyendo {audio_file}")
                        actual = len(block) // (CHANNELS * SAMPLE_WIDTH)
                        target.writeframesraw(block)
                        remaining -= actual
                if not _valid_wav(temporary, expected_frames=frames_this_part):
                    raise RuntimeError(f"Fragmento de audio corrupto: {path.name}")
                temporary.replace(path)
                log(f"[Audio] Fragmento {i+1}/{number_of_parts}: creado")
            finally:
                temporary.unlink(missing_ok=True)
    return parts
