"""Orquestación y reanudación segura con un manifiesto por ejecución."""

from __future__ import annotations

import json
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Iterator

from .audio import extract_audio, make_parts, AudioPart
from .engine import TranscriptSegment
from .progress import ConsoleUI, JobMetrics

VERSION = 1
MANIFEST = "manifest.json"


@dataclass(frozen=True)
class Config:
    input_file: Path
    output_dir: Path
    fragment_minutes: int = 20
    model: str = "small"
    language: str = "es"
    device: str = "cpu"
    precision: str = "auto"
    vad: bool = True
    timestamps: bool = True
    restart: bool = False
    progress: bool | None = None
    verbose: bool = False
    quiet: bool = False


class Transcriber(Protocol):
    def transcribe(
        self, audio: Path, offset_seconds: float
    ) -> Iterator[str | TranscriptSegment]: ...


def _fingerprint(config: Config) -> dict:
    stat = config.input_file.stat()
    return {
        "version": VERSION,
        "input_file": str(config.input_file.resolve()),
        "input_size": stat.st_size,
        "input_mtime_ns": stat.st_mtime_ns,
        "fragment_minutes": config.fragment_minutes,
        "model": config.model,
        "language": config.language,
        "device": config.device,
        "precision": config.precision,
        "vad": config.vad,
        "timestamps": config.timestamps,
    }


def _remove_generated(output_dir: Path) -> None:
    # Borrar SOLAMENTE nombres generados por el programa. Nunca todo el directorio.
    for file in [
        output_dir / MANIFEST,
        output_dir / "audio" / "audio_completo.wav",
        output_dir / "audio" / "audio_completo.tmp.wav",
        output_dir / "transcripcion_completa.txt",
        output_dir / "transcripcion_completa.tmp.txt",
    ]:
        file.unlink(missing_ok=True)
    for directory, pattern in [
        (output_dir / "audio" / "partes", "parte_*.wav"),
        (output_dir / "audio" / "partes", "parte_*.tmp.wav"),
        (output_dir / "txt", "parte_*.txt"),
        (output_dir / "txt", "parte_*.tmp.txt"),
        (output_dir / ".state", "parte_*.json"),
        (output_dir / ".state", "parte_*.tmp.json"),
    ]:
        if directory.exists():
            for file in directory.glob(pattern):
                if file.is_file():
                    file.unlink()


def _prepare_output(config: Config, ui: ConsoleUI) -> bool:
    output = config.output_dir
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / MANIFEST
    expected = _fingerprint(config)
    resumed = manifest.is_file() and not config.restart
    if manifest.is_file():
        if config.restart:
            _remove_generated(output)
        else:
            try:
                current = json.loads(manifest.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise RuntimeError(
                    "El manifiesto es ilegible. Usa --reiniciar o cambia --salida."
                ) from exc
            if current != expected:
                raise RuntimeError(
                    "La configuración o el video cambiaron respecto a esta carpeta. "
                    "Usa --reiniciar para empezar de cero o selecciona otra --salida."
                )
            ui.message("[Preparación] Trabajo compatible encontrado.", technical=True)
    elif any(output.iterdir()):
        raise RuntimeError(
            "La carpeta de salida tiene archivos pero no un manifiesto del programa. "
            "Usa otra carpeta vacía para no sobrescribir datos ajenos."
        )

    # Escritura atómica también para el manifiesto.
    temporary = output / "manifest.tmp.json"
    temporary.write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest)
    return resumed


def _completion_file(output_dir: Path, part_index: int) -> Path:
    return output_dir / ".state" / f"parte_{part_index:03d}.json"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_completion_marker(output_dir: Path, part: AudioPart, target: Path) -> None:
    marker = _completion_file(output_dir, part.index)
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_name(marker.stem + ".tmp.json")
    payload = {
        "part": part.index,
        "audio_duration_seconds": part.duration_seconds,
        "text_size": target.stat().st_size,
        "sha256": _file_digest(target),
    }
    temporary.unlink(missing_ok=True)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(marker)
    finally:
        temporary.unlink(missing_ok=True)


def _valid_transcription(output_dir: Path, part: AudioPart) -> bool:
    """Acepta marcadores verificables y migra TXT no vacíos del formato anterior."""
    target = output_dir / "txt" / f"parte_{part.index:03d}.txt"
    if not target.is_file():
        return False
    marker = _completion_file(output_dir, part.index)
    if marker.is_file():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            return (
                payload.get("part") == part.index
                and abs(
                    float(payload.get("audio_duration_seconds")) - part.duration_seconds
                ) < 0.001
                and payload.get("text_size") == target.stat().st_size
                and payload.get("sha256") == _file_digest(target)
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False

    # Versiones anteriores ya publicaban el nombre final solo al terminar. Un TXT
    # vacío o de puro espacio no aporta evidencia suficiente y se reprocesa.
    try:
        if not target.read_text(encoding="utf-8").strip():
            return False
        _write_completion_marker(output_dir, part, target)
        return True
    except (OSError, UnicodeError):
        return False


def _completed_parts(output_dir: Path, parts: list[AudioPart]) -> set[int]:
    return {part.index for part in parts if _valid_transcription(output_dir, part)}


def _write_transcription(
    target: Path, transcriber: Transcriber, part: AudioPart,
    on_progress: Callable[[float], None] | None = None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.stem + ".tmp.txt")
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            for item in transcriber.transcribe(part.path, part.offset_seconds):
                if isinstance(item, TranscriptSegment):
                    line = item.text
                    if on_progress is not None:
                        on_progress(item.end_seconds)
                else:
                    # Compatibilidad con motores personalizados de la versión anterior.
                    line = item
                file.write(line + "\n")
                file.flush()
        temporary.replace(target)
        _write_completion_marker(target.parents[1], part, target)
    finally:
        temporary.unlink(missing_ok=True)


def _join_texts(output_dir: Path, parts: list[AudioPart], fragment_minutes: int) -> Path:
    complete = output_dir / "transcripcion_completa.txt"
    temporary = output_dir / "transcripcion_completa.tmp.txt"
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as target:
            for part in parts:
                source_file = output_dir / "txt" / f"parte_{part.index:03d}.txt"
                if fragment_minutes > 0:
                    target.write(f"===== PARTE {part.index:03d} =====\n")
                with source_file.open("r", encoding="utf-8") as source:
                    for line in source:
                        target.write(line)
                target.write("\n")
        temporary.replace(complete)
    finally:
        temporary.unlink(missing_ok=True)
    return complete


def run(
    config: Config, engine_factory: Callable[[Config], Transcriber] | None = None,
    *, ui: ConsoleUI | None = None, clock: Callable[[], float] = time.monotonic,
) -> Path:
    config = Config(**{**vars(config),
        "input_file": config.input_file.expanduser().resolve(),
        "output_dir": config.output_dir.expanduser().resolve(),
    })
    if not config.input_file.is_file():
        raise RuntimeError(f"No se encontró el video/audio: {config.input_file}")
    if config.fragment_minutes < 0:
        raise RuntimeError("--fragmento-minutos debe ser 0 o un entero positivo.")
    ui = ui or ConsoleUI(
        progress=config.progress, quiet=config.quiet, verbose=config.verbose
    )
    resumed = _prepare_output(config, ui)

    audio_file = config.output_dir / "audio" / "audio_completo.wav"
    ui.stage("Extracción de audio")
    extract_audio(config.input_file, audio_file, log=ui.message)
    ui.stage("Fragmentación")
    parts = make_parts(
        audio_file, config.output_dir / "audio" / "partes", config.fragment_minutes,
        log=ui.message,
    )
    completed = _completed_parts(config.output_dir, parts)
    ui.job_header(config, parts, resumed)
    ui.resume_summary(parts, completed)
    ui.configure_parts(parts, completed)
    metrics = JobMetrics(
        total_audio_seconds=sum(part.duration_seconds for part in parts),
        initial_completed_audio_seconds=sum(
            part.duration_seconds for part in parts if part.index in completed
        ),
        clock=clock,
    )

    transcriber: Transcriber | None = None
    try:
        for part in parts:
            output_txt = config.output_dir / "txt" / f"parte_{part.index:03d}.txt"
            if part.index in completed:
                ui.message(
                    f"[Reanudación] Fragmento {part.index}/{len(parts)} validado; se omite.",
                    technical=True,
                )
                continue
            if transcriber is None:
                ui.stage("Carga del modelo")
                if engine_factory is None:
                    from .engine import WhisperEngine
                    transcriber = WhisperEngine(config, message=ui.message)
                else:
                    transcriber = engine_factory(config)
            metrics.start_part()
            ui.start_part(part, metrics)

            def update(processed: float, current: AudioPart = part) -> None:
                metrics.update_part(min(processed, current.duration_seconds))
                ui.update_part(current, processed, metrics)

            _write_transcription(output_txt, transcriber, part, on_progress=update)
            elapsed = metrics.finish_part(part.duration_seconds)
            completed.add(part.index)
            ui.finish_part(part, elapsed, metrics)
    except KeyboardInterrupt as interruption:
        ui.stop_dynamic()
        ui.message(
            "\nTranscripción interrumpida por el usuario.\n\n"
            f"Fragmentos guardados: {len(completed)}/{len(parts)}\n"
            f"Fragmentos pendientes: {len(parts) - len(completed)}/{len(parts)}\n\n"
            "Puedes continuar ejecutando nuevamente el mismo comando.",
            error=True,
        )
        interruption.transcriptor_reported = True
        raise
    except Exception:
        ui.stop_dynamic()
        raise

    ui.stage("Guardado de resultados")
    complete = _join_texts(config.output_dir, parts, config.fragment_minutes)
    ui.final_summary(len(completed), len(parts), metrics, complete)
    return complete
