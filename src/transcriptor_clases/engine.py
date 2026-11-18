"""Adaptador de Faster-Whisper: carga el modelo una sola vez."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from .pipeline import Config


def timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


@dataclass(frozen=True)
class TranscriptSegment:
    """Línea lista para guardar y final temporal relativo dentro del fragmento."""

    text: str
    end_seconds: float


class WhisperEngine:
    def __init__(
        self, config: Config, message: Callable[..., None] | None = None
    ) -> None:
        # Import diferido: python -m transcriptor_clases --help funciona aun sin dependencias.
        from faster_whisper import WhisperModel

        precision = config.precision
        if precision == "auto" and config.device == "cpu":
            precision = "int8"
        self._message = message or (lambda text, **_: print(text, flush=True))
        self._message(
            f"[Modelo] Cargando {config.model!r} en {config.device} "
            f"(precisión: {precision})..."
        )
        self._message(
            "[Modelo] La primera ejecución puede descargar archivos de Internet.",
            technical=True,
        )
        self.model = WhisperModel(config.model, device=config.device, compute_type=precision)
        self.language = None if config.language == "auto" else config.language
        self.vad = config.vad
        self.timestamps = config.timestamps

    def transcribe(self, audio: Path, offset_seconds: float) -> Iterator[TranscriptSegment]:
        segments, info = self.model.transcribe(
            str(audio),
            language=self.language,
            beam_size=5,
            vad_filter=self.vad,
            task="transcribe",  # Conserva el idioma original; NO traduce.
        )
        self._message(f"[Modelo] Idioma detectado/seleccionado: {info.language}")
        for segment in segments:
            content = segment.text.strip()
            if not content:
                continue
            if self.timestamps:
                start = timestamp(offset_seconds + segment.start)
                end = timestamp(offset_seconds + segment.end)
                text = f"[{start} --> {end}] {content}"
            else:
                text = content
            yield TranscriptSegment(text=text, end_seconds=float(segment.end))
