"""Pruebas sin descargar pesos de Whisper ni requerir GPU."""

import shutil
import tempfile
import unittest
import wave
import importlib.util
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from transcriptor_clases.audio import AudioPart, make_parts, wav_duration
from transcriptor_clases.cli import build_parser, main
from transcriptor_clases.engine import timestamp, TranscriptSegment
from transcriptor_clases.pipeline import Config, run
from transcriptor_clases.progress import (
    ConsoleUI, JobMetrics, fragment_progress, global_progress, remaining_time,
    speed_factor,
)


def silent_wav(path: Path, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = round(seconds * 16000)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        while frames:
            current = min(frames, 16000)
            output.writeframes(b"\x00\x00" * current)
            frames -= current


def make_test_part(index: int, duration: float) -> AudioPart:
    return AudioPart(index, Path(f"parte_{index:03d}.wav"), 0.0, duration)


class FakeEngine:
    calls = 0

    def __init__(self, _config):
        FakeEngine.calls = 0

    def transcribe(self, audio: Path, offset_seconds: float):
        FakeEngine.calls += 1
        yield f"[{timestamp(offset_seconds)}] Texto de {audio.stem}"


class ProgressiveEngine(FakeEngine):
    def transcribe(self, audio: Path, offset_seconds: float):
        ProgressiveEngine.calls += 1
        yield TranscriptSegment("Primer segmento", 0.5)
        yield TranscriptSegment("Segundo segmento", 1.0)


class InterruptEngine:
    def __init__(self, _config):
        pass

    def transcribe(self, _audio: Path, _offset_seconds: float):
        yield TranscriptSegment("Todavía temporal", 0.5)
        raise KeyboardInterrupt


class TTYBuffer(StringIO):
    def isatty(self):
        return True


class AudioTests(unittest.TestCase):
    def test_split_exact_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            wav = folder / "audio.wav"
            silent_wav(wav, 61.0)
            parts = make_parts(wav, folder / "partes", 1)
            self.assertEqual(len(parts), 2)
            self.assertAlmostEqual(parts[0].offset_seconds, 0.0)
            self.assertAlmostEqual(parts[0].duration_seconds, 60.0)
            self.assertAlmostEqual(parts[1].offset_seconds, 60.0)
            self.assertAlmostEqual(wav_duration(parts[1].path), 1.0)
            # La segunda ejecución reutiliza los WAV existentes.
            self.assertEqual(make_parts(wav, folder / "partes", 1), parts)

    def test_no_split(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            wav = folder / "audio.wav"
            silent_wav(wav, 2.5)
            parts = make_parts(wav, folder / "partes", 0)
            self.assertEqual(len(parts), 1)
            self.assertEqual(parts[0].path, wav)

    def test_timestamp(self):
        self.assertEqual(timestamp(3661.234), "01:01:01.234")


class ProgressTests(unittest.TestCase):
    def test_fragment_progress_is_bounded(self):
        self.assertEqual(fragment_progress(30, 60), 0.5)
        self.assertEqual(fragment_progress(90, 60), 1.0)
        self.assertEqual(fragment_progress(-1, 60), 0.0)
        self.assertEqual(fragment_progress(1, 0), 0.0)

    def test_global_progress_uses_real_durations(self):
        # La parte corta terminada representa 10/100, no 1/2.
        self.assertAlmostEqual(global_progress([10, 90], {1}), 0.1)
        self.assertAlmostEqual(global_progress([10, 90], {1}, 2, 45), 0.55)

    def test_speed_and_eta_require_data(self):
        self.assertIsNone(speed_factor(0, 10))
        self.assertIsNone(speed_factor(10, 0))
        self.assertEqual(speed_factor(10, 20), 0.5)
        self.assertIsNone(remaining_time(100, None))
        self.assertEqual(remaining_time(100, 0.5), 200)

    def test_metrics_wait_before_estimating(self):
        now = [0.0]
        metrics = JobMetrics(100, clock=lambda: now[0])
        metrics.start_part()
        metrics.update_part(0.5)
        now[0] = 0.5
        self.assertIsNone(metrics.speed)
        metrics.update_part(10)
        now[0] = 20
        self.assertEqual(metrics.speed, 0.5)
        self.assertEqual(metrics.eta(10), 180)

    def test_dynamic_progress_respects_tty_and_flags(self):
        self.assertTrue(ConsoleUI(progress=True, stream=TTYBuffer()).dynamic)
        self.assertFalse(ConsoleUI(progress=False, stream=TTYBuffer()).dynamic)
        self.assertFalse(ConsoleUI(progress=True, stream=StringIO()).dynamic)
        self.assertFalse(ConsoleUI(progress=True, quiet=True, stream=TTYBuffer()).dynamic)

    @unittest.skipUnless(importlib.util.find_spec("rich"), "Rich no está instalado")
    def test_dynamic_progress_renders_fragment_and_global_tasks(self):
        stream = TTYBuffer()
        now = [0.0]
        part = make_test_part(1, 10.0)
        metrics = JobMetrics(10.0, clock=lambda: now[0])
        ui = ConsoleUI(progress=True, stream=stream)
        ui.configure_parts([part], set())
        metrics.start_part()
        ui.start_part(part, metrics)
        now[0] = 5.0
        metrics.update_part(5.0)
        ui.update_part(part, 5.0, metrics)
        elapsed = metrics.finish_part(10.0)
        ui.finish_part(part, elapsed, metrics)
        rendered = stream.getvalue()
        self.assertIn("Fragmento 1/1", rendered)
        self.assertIn("Progreso general", rendered)
        self.assertIn("50%", rendered)
        self.assertIn("última marca", rendered)


class CliTests(unittest.TestCase):
    def test_progress_flags(self):
        parser = build_parser()
        self.assertIsNone(parser.parse_args(["input.wav"]).progress)
        self.assertTrue(parser.parse_args(["input.wav", "--progreso"]).progress)
        self.assertFalse(parser.parse_args(["input.wav", "--sin-progreso"]).progress)

    def test_cli_passes_console_options(self):
        captured = []
        with patch("transcriptor_clases.cli.run", side_effect=lambda config: captured.append(config)):
            self.assertEqual(main(["input.wav", "--sin-progreso", "--verbose"]), 0)
        self.assertFalse(captured[0].progress)
        self.assertTrue(captured[0].verbose)
        self.assertFalse(captured[0].quiet)

    def test_cli_handles_keyboard_interrupt_without_traceback(self):
        with patch("transcriptor_clases.cli.run", side_effect=KeyboardInterrupt):
            with patch("sys.stderr", new_callable=StringIO) as errors:
                self.assertEqual(main(["input.wav"]), 130)
        self.assertIn("INTERRUMPIDO", errors.getvalue())


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg no está instalado")
class PipelineTests(unittest.TestCase):
    def test_complete_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "entrada.wav"
            silent_wav(source, 61)
            config = Config(source, folder / "salida", fragment_minutes=1)
            complete = run(config, engine_factory=FakeEngine)
            text = complete.read_text(encoding="utf-8")
            self.assertIn("PARTE 001", text)
            self.assertIn("PARTE 002", text)
            self.assertIn("00:01:00.000", text)
            self.assertEqual(FakeEngine.calls, 2)
            self.assertTrue((folder / "salida" / ".state" / "parte_001.json").is_file())

            # Reanudación: ninguna parte terminada debe transcribirse otra vez.
            def fail_if_initialized(_):
                raise AssertionError("No debía inicializarse el modelo en reanudación")
            self.assertEqual(run(config, engine_factory=fail_if_initialized), complete)

            # Una configuración distinta NO debe reutilizar el mismo manifiesto.
            with self.assertRaisesRegex(RuntimeError, "configuración"):
                run(Config(source, folder / "salida", fragment_minutes=0), engine_factory=FakeEngine)

    def test_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "entrada.wav"
            silent_wav(source, 1)
            out = folder / "salida"
            run(Config(source, out), engine_factory=FakeEngine)
            # Restart con parámetros distintos: elimina los archivos generados y reejecuta.
            run(Config(source, out, fragment_minutes=0, restart=True), engine_factory=FakeEngine)
            self.assertTrue((out / "transcripcion_completa.txt").is_file())
            self.assertFalse((out / "audio" / "partes" / "parte_001.wav").exists())

    def test_empty_transcription_is_not_considered_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "entrada.wav"
            silent_wav(source, 1)
            out = folder / "salida"
            config = Config(source, out, fragment_minutes=0)
            run(config, engine_factory=FakeEngine)
            target = out / "txt" / "parte_001.txt"
            marker = out / ".state" / "parte_001.json"
            target.write_text("", encoding="utf-8")
            marker.unlink()
            run(config, engine_factory=FakeEngine)
            self.assertTrue(target.read_text(encoding="utf-8").strip())
            self.assertEqual(FakeEngine.calls, 1)

    def test_modified_completed_file_is_reprocessed(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "entrada.wav"
            silent_wav(source, 1)
            out = folder / "salida"
            config = Config(source, out, fragment_minutes=0)
            run(config, engine_factory=FakeEngine)
            (out / "txt" / "parte_001.txt").write_text("truncado", encoding="utf-8")
            run(config, engine_factory=FakeEngine)
            self.assertIn("Texto", (out / "txt" / "parte_001.txt").read_text(encoding="utf-8"))
            self.assertEqual(FakeEngine.calls, 1)

    def test_keyboard_interrupt_keeps_only_final_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "entrada.wav"
            silent_wav(source, 1)
            out = folder / "salida"
            stream = StringIO()
            ui = ConsoleUI(progress=False, stream=stream, error_stream=stream)
            with self.assertRaises(KeyboardInterrupt):
                run(
                    Config(source, out, fragment_minutes=0),
                    engine_factory=InterruptEngine,
                    ui=ui,
                )
            self.assertFalse((out / "txt" / "parte_001.txt").exists())
            self.assertFalse((out / "txt" / "parte_001.tmp.txt").exists())
            self.assertIn("puede", stream.getvalue().lower())

    def test_progressive_segments_generate_joined_text(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "entrada.wav"
            silent_wav(source, 1)
            complete = run(
                Config(source, folder / "salida", fragment_minutes=0),
                engine_factory=ProgressiveEngine,
                ui=ConsoleUI(progress=False, stream=StringIO()),
            )
            self.assertEqual(
                complete.read_text(encoding="utf-8"),
                "Primer segmento\nSegundo segmento\n\n",
            )


if __name__ == "__main__":
    unittest.main()
