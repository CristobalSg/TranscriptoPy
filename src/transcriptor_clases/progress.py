"""Presentación de consola y cálculos de progreso independientes del motor."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, TextIO

from .audio import AudioPart


def fragment_progress(processed_seconds: float, duration_seconds: float) -> float:
    """Fracción estimada del audio cubierta por las marcas de los segmentos."""
    if duration_seconds <= 0:
        return 0.0
    return min(1.0, max(0.0, processed_seconds) / duration_seconds)


def global_progress(
    durations: Sequence[float], completed: set[int], current_index: int | None = None,
    current_processed_seconds: float = 0.0,
) -> float:
    """Progreso ponderado por las duraciones reales, no por número de partes."""
    total = sum(max(0.0, duration) for duration in durations)
    if total <= 0:
        return 0.0
    covered = sum(
        max(0.0, duration)
        for index, duration in enumerate(durations, start=1)
        if index in completed
    )
    if current_index is not None and current_index not in completed:
        duration = durations[current_index - 1]
        covered += min(max(0.0, current_processed_seconds), max(0.0, duration))
    return min(1.0, covered / total)


def speed_factor(audio_seconds: float, elapsed_seconds: float) -> float | None:
    """Segundos de audio procesado por segundo real (p. ej. 0.5x)."""
    if audio_seconds <= 0 or elapsed_seconds <= 0:
        return None
    return audio_seconds / elapsed_seconds


def remaining_time(remaining_audio_seconds: float, speed: float | None) -> float | None:
    if remaining_audio_seconds <= 0:
        return 0.0
    if speed is None or speed <= 0:
        return None
    return remaining_audio_seconds / speed


def format_duration(seconds: float | None, *, approximate: bool = False) -> str:
    if seconds is None:
        return "Calculando..."
    value = max(0, round(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    text = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"~{text}" if approximate else text


@dataclass
class JobMetrics:
    """Estado medible de una ejecución, usando un reloj inyectable en pruebas."""

    total_audio_seconds: float
    initial_completed_audio_seconds: float = 0.0
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self.started_at = self.clock()
        self.measured_audio_seconds = 0.0
        self.measured_elapsed_seconds = 0.0
        self.part_started_at: float | None = None
        self.part_processed_seconds = 0.0

    def start_part(self) -> None:
        self.part_started_at = self.clock()
        self.part_processed_seconds = 0.0

    def update_part(self, processed_seconds: float) -> None:
        self.part_processed_seconds = max(self.part_processed_seconds, processed_seconds)

    def finish_part(self, duration_seconds: float) -> float:
        elapsed = self.current_part_elapsed
        self.measured_elapsed_seconds += elapsed
        self.measured_audio_seconds += max(0.0, duration_seconds)
        self.part_started_at = None
        self.part_processed_seconds = 0.0
        return elapsed

    @property
    def elapsed(self) -> float:
        return max(0.0, self.clock() - self.started_at)

    @property
    def current_part_elapsed(self) -> float:
        if self.part_started_at is None:
            return 0.0
        return max(0.0, self.clock() - self.part_started_at)

    @property
    def observed_audio_seconds(self) -> float:
        return self.measured_audio_seconds + self.part_processed_seconds

    @property
    def observed_elapsed_seconds(self) -> float:
        return self.measured_elapsed_seconds + self.current_part_elapsed

    @property
    def speed(self) -> float | None:
        # Un segmento inicial diminuto no basta para dar una estimación útil.
        if self.observed_audio_seconds < 1.0 or self.observed_elapsed_seconds < 1.0:
            return None
        return speed_factor(self.observed_audio_seconds, self.observed_elapsed_seconds)

    @property
    def current_part_speed(self) -> float | None:
        if self.part_processed_seconds < 1.0 or self.current_part_elapsed < 1.0:
            return None
        return speed_factor(self.part_processed_seconds, self.current_part_elapsed)

    def eta(self, covered_audio_seconds: float) -> float | None:
        return remaining_time(
            max(0.0, self.total_audio_seconds - covered_audio_seconds), self.speed
        )


class ConsoleUI:
    """Salida profesional con Rich cuando existe y degradación limpia a texto."""

    def __init__(
        self, *, progress: bool | None = None, quiet: bool = False,
        verbose: bool = False, stream: TextIO = sys.stdout,
        error_stream: TextIO = sys.stderr,
    ) -> None:
        self.quiet = quiet
        self.verbose = verbose
        self.stream = stream
        self.error_stream = error_stream
        self.is_tty = bool(getattr(stream, "isatty", lambda: False)())
        self.dynamic = not quiet and progress is not False and self.is_tty
        self._rich_progress = None
        self._fragment_task = None
        self._global_task = None
        self._parts: list[AudioPart] = []
        self._completed: set[int] = set()

    def message(self, text: str, *, technical: bool = False, error: bool = False) -> None:
        if self.quiet and not error:
            return
        if technical and not self.verbose:
            return
        target = self.error_stream if error else self.stream
        if self._rich_progress and target is self.stream:
            self._rich_progress.console.print(text, markup=False)
        else:
            print(text, file=target, flush=True)

    def stage(self, name: str, detail: str | None = None) -> None:
        text = f"[{name}]"
        if detail:
            text += f" {detail}"
        self.message(text)

    def job_header(self, config, parts: Sequence[AudioPart], resumed: bool) -> None:
        if self.quiet:
            return
        precision = "int8" if config.precision == "auto" and config.device == "cpu" else config.precision
        language = "Automático" if config.language == "auto" else config.language
        duration = sum(part.duration_seconds for part in parts)
        lines = [
            "TRANSCRIPTOR DE CLASES", "",
            f"Archivo:      {config.input_file.name}",
            f"Duración:     {format_duration(duration)}",
            f"Modelo:       {config.model}",
            f"Dispositivo:  {config.device.upper()}",
            f"Precisión:    {precision}",
            f"Idioma:       {language}",
            f"Fragmentos:   {len(parts)}",
            f"Salida:       {config.output_dir}",
            f"Estado:       {'Reanudando trabajo anterior' if resumed else 'Trabajo nuevo'}",
        ]
        self.message("\n".join(lines))

    def resume_summary(self, parts: Sequence[AudioPart], completed: set[int]) -> None:
        if not completed or self.quiet:
            return
        pending = len(parts) - len(completed)
        next_part = next((part.index for part in parts if part.index not in completed), None)
        lines = [
            "", "Se encontró un trabajo anterior.",
            f"Fragmentos completados: {len(completed)}/{len(parts)}",
            f"Fragmentos pendientes:  {pending}/{len(parts)}",
        ]
        if next_part is not None:
            lines.append(f"Reanudando desde el fragmento {next_part}...")
        self.message("\n".join(lines))

    def configure_parts(self, parts: Sequence[AudioPart], completed: set[int]) -> None:
        self._parts = list(parts)
        self._completed = set(completed)

    def start_part(self, part: AudioPart, metrics: JobMetrics) -> None:
        if self.quiet:
            return
        self.message(f"\n[Transcripción] Fragmento {part.index}/{len(self._parts)} "
                     f"({format_duration(part.duration_seconds)} de audio)")
        if len(self._parts) <= 20:
            states = []
            for item in self._parts:
                symbol = "✓" if item.index in self._completed else "→" if item.index == part.index else " "
                states.append(f"[{symbol}]{item.index}")
            self.message("Progreso general: " + " ".join(states))
        else:
            self.message(
                f"Progreso general: {len(self._completed)}/{len(self._parts)} completos; "
                f"fragmento {part.index} en proceso."
            )
        if not self.dynamic:
            return
        try:
            from rich.progress import (
                BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn,
            )
            from rich.console import Console
        except ImportError:
            self.dynamic = False
            self.message("[Progreso] Rich no está instalado; se usará salida de texto.", technical=True)
            return
        console = Console(file=self.stream)
        self._rich_progress = Progress(
            TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(),
            TextColumn("{task.fields[detail]}"), TimeElapsedColumn(), console=console,
            transient=False,
        )
        total = sum(item.duration_seconds for item in self._parts)
        completed_audio = sum(
            item.duration_seconds for item in self._parts if item.index in self._completed
        )
        self._fragment_task = self._rich_progress.add_task(
            f"Fragmento {part.index}/{len(self._parts)}", total=part.duration_seconds,
            detail="audio estimado; finalización pendiente",
        )
        self._global_task = self._rich_progress.add_task(
            "Progreso general", total=total, completed=completed_audio,
            detail=f"{len(self._completed)}/{len(self._parts)} completos | ETA: Calculando...",
        )
        self._rich_progress.start()

    def update_part(self, part: AudioPart, processed: float, metrics: JobMetrics) -> None:
        if not self._rich_progress:
            return
        bounded = min(max(0.0, processed), part.duration_seconds)
        completed_audio = sum(
            item.duration_seconds for item in self._parts if item.index in self._completed
        )
        speed = metrics.speed
        speed_text = "Calculando..." if speed is None else f"{speed:.2f}x"
        eta = metrics.eta(completed_audio + bounded)
        part_eta = remaining_time(
            max(0.0, part.duration_seconds - bounded), metrics.current_part_speed
        )
        self._rich_progress.update(
            self._fragment_task, completed=bounded,
            detail=(
                f"audio {format_duration(bounded)}/{format_duration(part.duration_seconds)} "
                f"| {speed_text} | ETA: {format_duration(part_eta, approximate=True)}"
            ),
        )
        self._rich_progress.update(
            self._global_task, completed=completed_audio + bounded,
            detail=f"{len(self._completed)}/{len(self._parts)} completos | ETA: {format_duration(eta, approximate=True)}",
        )

    def finish_part(self, part: AudioPart, elapsed: float, metrics: JobMetrics) -> None:
        if self._rich_progress:
            task = self._rich_progress.tasks[self._fragment_task]
            self._rich_progress.update(
                self._fragment_task,
                detail=(
                    "fragmento finalizado; última marca "
                    f"{format_duration(float(task.completed))}/{format_duration(part.duration_seconds)}"
                ),
            )
            completed_audio = sum(
                item.duration_seconds
                for item in self._parts
                if item.index in self._completed or item.index == part.index
            )
            self._rich_progress.update(
                self._global_task, completed=completed_audio,
                detail=f"{len(self._completed) + 1}/{len(self._parts)} completos",
            )
            self._rich_progress.stop()
        self._rich_progress = None
        self._completed.add(part.index)
        speed = speed_factor(part.duration_seconds, elapsed)
        if not self.quiet:
            speed_text = "No disponible" if speed is None else f"{speed:.2f}x"
            self.message(
                f"[Guardado] Fragmento {part.index}/{len(self._parts)} completado en "
                f"{format_duration(elapsed)} (velocidad {speed_text})."
            )

    def stop_dynamic(self) -> None:
        if self._rich_progress:
            self._rich_progress.stop()
            self._rich_progress = None

    def final_summary(self, completed: int, total: int, metrics: JobMetrics, output: Path) -> None:
        if self.quiet:
            return
        speed = metrics.speed
        speed_text = "No disponible" if speed is None else f"{speed:.2f}x"
        self.message(
            "\n[Finalización]\n"
            f"Tiempo total transcurrido: {format_duration(metrics.elapsed)}\n"
            f"Duración de audio:        {format_duration(metrics.total_audio_seconds)}\n"
            f"Velocidad medida:          {speed_text}\n"
            f"Fragmentos completados:    {completed}/{total}\n"
            "Fragmentos pendientes:     0\n"
            f"Transcripción completa:    {output}"
        )

