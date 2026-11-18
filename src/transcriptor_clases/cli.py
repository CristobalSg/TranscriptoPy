"""Interfaz de línea de comandos del transcriptor de clases."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import Config, run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transcribir-clase",
        description="Transcribe videos o audios largos a TXT usando Faster-Whisper.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("entrada", type=Path, help="Ruta del MP4, MKV, MP3, WAV, etc.")
    parser.add_argument(
        "--salida", type=Path, default=None,
        help="Carpeta de resultados (por defecto: resultados/NOMBRE_DEL_VIDEO).",
    )
    parser.add_argument(
        "--fragmento-minutos", type=int, default=20,
        help="Duración de cada fragmento; usa 0 para no dividir.",
    )
    parser.add_argument(
        "--modelo", default="small", help="Modelo: tiny, base, small, medium, large-v3, etc.",
    )
    parser.add_argument(
        "--idioma", default="es", help="Idioma del audio (es, en, etc.) o 'auto'.",
    )
    parser.add_argument(
        "--dispositivo", choices=("cpu", "cuda", "auto"), default="cpu",
        help="Procesador donde se ejecuta el modelo.",
    )
    parser.add_argument(
        "--precision", choices=("auto", "int8", "float16", "int8_float16", "float32"),
        default="auto", help="Precisión interna; con el dispositivo CPU se usa int8 por defecto.",
    )
    parser.add_argument(
        "--sin-marcas-tiempo", action="store_true",
        help="TXT solo con texto, sin [HH:MM:SS --> HH:MM:SS].",
    )
    parser.add_argument(
        "--sin-vad", action="store_true",
        help="Desactiva filtro de silencios (VAD).",
    )
    parser.add_argument(
        "--reiniciar", action="store_true",
        help="Rehacer el trabajo; elimina solo resultados propios en esa carpeta.",
    )
    progress = parser.add_mutually_exclusive_group()
    progress.add_argument(
        "--progreso", dest="progress", action="store_true",
        help="Activar barras dinámicas cuando la salida sea una terminal.",
    )
    progress.add_argument(
        "--sin-progreso", dest="progress", action="store_false",
        help="Usar solamente mensajes de texto, sin barras dinámicas.",
    )
    parser.set_defaults(progress=None)
    detail = parser.add_mutually_exclusive_group()
    detail.add_argument(
        "--verbose", action="store_true",
        help="Mostrar detalles técnicos y decisiones de reanudación.",
    )
    detail.add_argument(
        "--quiet", action="store_true",
        help="Ocultar mensajes informativos; los errores siguen visibles.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.fragmento_minutos < 0:
        parser.error("--fragmento-minutos no puede ser negativo")
    if not args.idioma.strip():
        parser.error("--idioma no puede estar vacío")
    output = args.salida or Path.cwd() / "resultados" / args.entrada.stem
    config = Config(
        input_file=args.entrada,
        output_dir=output,
        fragment_minutes=args.fragmento_minutos,
        model=args.modelo,
        language=args.idioma,
        device=args.dispositivo,
        precision=args.precision,
        vad=not args.sin_vad,
        timestamps=not args.sin_marcas_tiempo,
        restart=args.reiniciar,
        progress=args.progress,
        verbose=args.verbose,
        quiet=args.quiet,
    )
    try:
        run(config)
    except KeyboardInterrupt as interruption:
        if not getattr(interruption, "transcriptor_reported", False):
            print(
                "\n[INTERRUMPIDO] El trabajo puede reanudarse con el mismo comando.",
                file=sys.stderr,
            )
        return 130
    except (RuntimeError, OSError, ValueError) as error:
        print(f"\n[ERROR] {error}", file=sys.stderr)
        return 1
    return 0
