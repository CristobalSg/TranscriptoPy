# Contexto del proyecto

## Propósito

`transcriptor-clases` es una herramienta CLI en Python para transcribir localmente videos o audios largos, especialmente clases universitarias de dos horas o más.

El flujo es:

1. Recibir un archivo multimedia desde la CLI.
2. Extraer y normalizar el audio con FFmpeg a WAV mono, PCM de 16 bits y 16 kHz.
3. Dividirlo opcionalmente en fragmentos configurables.
4. Transcribirlos secuencialmente con Faster-Whisper.
5. Guardar un TXT por fragmento con marcas temporales globales opcionales.
6. Reanudar trabajos sin repetir fragmentos terminados.
7. Generar `transcripcion_completa.txt` en orden cronológico.

No traduce el contenido: Faster-Whisper se ejecuta con `task="transcribe"` y conserva el idioma hablado.

## Entorno y configuración predeterminada

- Plataforma principal: macOS con Apple Silicon.
- Python soportado: 3.10 o superior; se recomiendan 3.11 o 3.12.
- Motor: `faster-whisper>=1.1,<2`.
- Interfaz dinámica: `rich>=13.7,<15`.
- Dispositivo predeterminado: CPU.
- Si `--precision auto` se usa con CPU, el motor selecciona `int8`.
- No se activa MPS experimental automáticamente.
- FFmpeg debe estar disponible en `PATH`.
- El proyecto se instala con `pip install -e .`.

La versión actual del paquete es `1.1.0`.

## Estructura relevante

```text
src/transcriptor_clases/
├── __init__.py
├── __main__.py
├── cli.py          # argumentos y manejo de salida/errores
├── audio.py        # FFmpeg, validación WAV y fragmentación
├── engine.py       # adaptador de Faster-Whisper
├── progress.py     # métricas, ETA y presentación de consola
└── pipeline.py     # orquestación, reanudación y unión

tests/
└── test_pipeline.py
```

## CLI

Opciones principales existentes:

- `--salida RUTA`
- `--fragmento-minutos N`; `0` desactiva la división.
- `--modelo MODELO`
- `--idioma IDIOMA`; acepta `auto`.
- `--dispositivo {cpu,cuda,auto}`
- `--precision {auto,int8,float16,int8_float16,float32}`
- `--sin-marcas-tiempo`
- `--sin-vad`
- `--reiniciar`
- `--progreso` / `--sin-progreso`
- `--verbose` / `--quiet`

Las opciones de cada pareja son mutuamente excluyentes.

Por defecto, las barras dinámicas solo aparecen cuando stdout es una terminal interactiva. En redirecciones o terminales no interactivas se generan mensajes de texto sin caracteres de control. `--quiet` conserva los errores y los avisos de cancelación.

Ejemplo habitual:

```bash
python -m transcriptor_clases "clase_1.mp4" \
  --fragmento-minutos 20 \
  --modelo medium \
  --idioma es
```

## Progreso y métricas

`engine.py` produce objetos `TranscriptSegment` con:

- La línea de texto lista para guardar.
- El tiempo final relativo del segmento dentro del fragmento.

El progreso estimado se actualiza con `segment.end`; no se hace una segunda pasada ni procesamiento adicional.

La consola muestra:

- Fragmento actual y total.
- Duración real del fragmento.
- Audio cubierto por las marcas temporales.
- Porcentaje estimado del fragmento.
- Tiempo real transcurrido.
- Factor de velocidad.
- ETA del fragmento y del trabajo completo cuando hay datos suficientes.
- Progreso global ponderado por la duración real de cada fragmento.
- Fragmentos completados y pendientes.

La velocidad se define como:

```text
velocidad = segundos de audio procesado / segundos reales transcurridos
```

Por ejemplo, `0.5x` significa que diez minutos de audio requieren aproximadamente veinte minutos reales.

Las estimaciones aparecen como `Calculando...` mientras la muestra es insuficiente. La barra puede permanecer quieta mientras Faster-Whisper procesa internamente. Una marca temporal al final del audio tampoco equivale por sí sola a la finalización: el fragmento solo termina cuando el generador se agota correctamente y el TXT se publica.

## Reanudación y seguridad de archivos

Cada carpeta de salida contiene un `manifest.json` que identifica:

- Ruta, tamaño y fecha de modificación del archivo original.
- Duración configurada para los fragmentos.
- Modelo, idioma, dispositivo y precisión.
- Configuración de VAD y marcas temporales.

Si esos datos cambian, el programa evita mezclar resultados y exige `--reiniciar` u otra carpeta de salida.

Los TXT se escriben de forma segura:

1. Se elimina cualquier temporal anterior del fragmento.
2. Los segmentos se escriben progresivamente en `parte_XXX.tmp.txt`.
3. Al agotarse correctamente el generador, el temporal se renombra atómicamente a `parte_XXX.txt`.
4. Se crea en `.state/parte_XXX.json` un marcador con índice, duración, tamaño y SHA-256.

Al reanudar:

- Un TXT con marcador solo se reutiliza si tamaño, hash, índice y duración coinciden.
- Un TXT vacío sin marcador no se considera terminado.
- Un TXT modificado o con marcador corrupto se reprocesa.
- Los TXT no vacíos creados por la versión anterior se aceptan porque aquella versión ya publicaba el nombre final atómicamente; al encontrarlos se genera su marcador nuevo.
- Los archivos `.tmp.txt` nunca se consideran resultados completos.
- Los WAV válidos ya existentes se reutilizan.
- El TXT unificado siempre se reconstruye siguiendo el orden de los fragmentos.

`--reiniciar` elimina exclusivamente los nombres reservados generados por el programa. No debe ampliarse para borrar indiscriminadamente la carpeta de salida.

## Cancelación y errores

Al recibir `Ctrl+C` durante una transcripción:

- Se detiene la presentación dinámica.
- Se cierra y elimina el temporal del fragmento actual.
- Los fragmentos publicados anteriormente quedan intactos.
- Se informa cuántos fragmentos están guardados y cuántos quedan pendientes.
- La CLI devuelve el código `130` sin traceback.

Si la interrupción sucede antes de disponer del listado de fragmentos, la CLI muestra un mensaje breve indicando que el trabajo puede reanudarse.

Los errores reales devuelven código `1` y conservan un mensaje diagnóstico.

## Pruebas

Las pruebas usan WAV pequeños y motores simulados. No descargan modelos Whisper, no requieren GPU y no se conectan a Internet durante la ejecución.

Cobertura actual relevante:

- Límites y fragmentación WAV.
- Marcas temporales.
- Progreso de fragmento y progreso global ponderado.
- Velocidad y ETA sin datos suficientes.
- Renderizado real de tareas Rich cuando la dependencia está disponible.
- Opciones CLI con y sin progreso.
- Reanudación sin inicializar nuevamente el motor.
- Exclusión de TXT vacíos o modificados.
- Marcadores de finalización.
- Cancelación mediante `KeyboardInterrupt`.
- Escritura temporal y limpieza.
- Generación del TXT unificado.
- Reinicio controlado.

Último resultado ejecutado:

```text
18 passed in 0.21s
```

Comandos:

```bash
python -m pip install -e ".[test]"
python -m pytest -q
```

Alternativa:

```bash
PYTHONPATH="$PWD/src" python -m unittest discover -s tests -v
```

## Estado del trabajo local existente

Hay un trabajo en `resultados/clase_1/` asociado a `clase_1.mp4` con:

- Manifiesto versión 1.
- Fragmentos de 20 minutos.
- Modelo `medium`.
- Idioma `es`.
- CPU y precisión `auto`, que se resuelve a `int8`.
- VAD y marcas temporales activados.
- `txt/parte_001.txt` no vacío.

Este trabajo no fue continuado ni modificado durante la implementación. La siguiente ejecución con la misma configuración deberá validar y migrar el primer TXT, reutilizar los WAV y continuar desde la primera parte pendiente.

No borrar `resultados/`, los WAV, los TXT ni `clase_1.mp4` durante cambios ordinarios.

## Particularidad observada del entorno

El entorno existente usa Python 3.14 dentro de `.venv`. En este sistema macOS marcó el archivo `__editable__.transcriptor_clases-1.1.0.pth` como oculto y Python 3.14 lo omitió al iniciar. El paquete editable se construyó e instaló correctamente, pero ese `venv` concreto necesitó temporalmente:

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m transcriptor_clases --help
```

La recomendación estable es recrear el entorno con Python 3.11 o 3.12 y volver a ejecutar `pip install -e .`. Esta particularidad no debe confundirse con un error del import diferido de Faster-Whisper.

## Invariantes para cambios futuros

- Inspeccionar las firmas y estructuras existentes antes de cambiarlas.
- Mantener compatibilidad con los motores personalizados que todavía producen cadenas simples.
- No modificar innecesariamente el formato de las transcripciones.
- Conservar marcas temporales globales, no relativas al fragmento.
- No considerar completo un fragmento solo por la existencia de un TXT vacío.
- No publicar el nombre final antes de completar correctamente el generador.
- No marcar 100% de audio estimado si la última marca temporal no llegó al final; la finalización real debe mostrarse por separado.
- No cargar el modelo hasta encontrar el primer fragmento pendiente.
- Cargar Faster-Whisper de forma diferida para que `--help` funcione sin importar el motor.
- Mantener CPU + `int8` como configuración conservadora.
- No activar MPS automáticamente.
- No descargar modelos ni usar videos largos en pruebas.
- No borrar datos existentes ni ampliar el alcance de `--reiniciar`.
- Mantener `python -m transcriptor_clases` y el comando `transcribir-clase`.

