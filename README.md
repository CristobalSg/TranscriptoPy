# Transcriptor de clases (Python + Faster-Whisper)

Proyecto de línea de comandos para **convertir un video de 2 horas (o más) a archivos TXT**, funcionando localmente en tu computadora. Extrae el audio usando FFmpeg, lo divide **opcionalmente** en fragmentos, transcribe con Faster-Whisper y une todos los TXT en un solo documento.

> **Importante:** Esto es **transcripción** (audio → texto en el idioma hablado), **no traducción** a otro idioma. El programa no necesita claves de API, ni enviar los videos a un servicio remoto. La primera vez que usas un modelo, Faster-Whisper necesita Internet para descargar sus pesos; después puede ejecutarse localmente.

## 1. Requisitos

- Python **3.10 o superior** (para mayor compatibilidad, se recomienda 3.11 o 3.12).
- **FFmpeg** instalado y disponible en el PATH.
- Memoria RAM y espacio libre suficientes. Un WAV mono de 16 kHz/16 bits ocupa aproximadamente **230 MB por 2 horas**, además de copias de los fragmentos y el modelo de IA.
- Conexión a Internet **solo para instalar las dependencias y descargar el modelo la primera vez**.
- No necesitas GPU: por defecto ejecuta en CPU con precisión `int8`.

### Instalar FFmpeg

**macOS** (si tienes Homebrew):

```bash
brew install ffmpeg
```

**Ubuntu/Debian:**

```bash
sudo apt update
sudo apt install ffmpeg
```

**Windows:** instala FFmpeg desde https://ffmpeg.org/download.html (elige un distribuidor para Windows), agrégalo al PATH y abre una consola nueva. Si utilizas `winget`, puedes probar:

```powershell
winget install --id Gyan.FFmpeg -e
```

Comprueba la instalación en cualquier sistema:

```bash
ffmpeg -version
```

## 2. Crear y activar el entorno virtual (`venv`)

Descomprime el ZIP, abre la consola **dentro de la carpeta `transcriptor_clases`** y ejecuta lo siguiente.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

### Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Si PowerShell bloquea la activación, puedes **usar el entorno sin activarlo** ejecutando ` .\.venv\Scripts\python.exe -m transcriptor_clases ... ` en lugar de `python -m transcriptor_clases ...`.

> `pip install -e .` instala las dependencias de `pyproject.toml` y registra el comando `transcribir-clase`. Como alternativa, puedes ejecutar `python -m pip install -r requirements.txt` y usar `PYTHONPATH=src` o instalar el proyecto con `pip install -e .` (recomendado).

### Comprobar que funciona

```bash
python -m transcriptor_clases --help
```

También puedes usar `transcribir-clase --help` si tu `venv` está activado.

## 3. Uso básico desde la consola

La ruta del video debe ir entre comillas si contiene espacios.

### Opción recomendada: 6 fragmentos de 20 minutos

```bash
python -m transcriptor_clases "videos/clase.mp4" --fragmento-minutos 20 --modelo small --idioma es
```

Para un video de **120 minutos**, se generan **6 archivos WAV**, **6 archivos TXT** y `transcripcion_completa.txt`.

### Mejor precisión con el modelo `medium`

```bash
python -m transcriptor_clases "videos/clase.mp4" --fragmento-minutos 20 --modelo medium --idioma es
```

`medium` suele dar mejor calidad que `small`, pero consume más recursos y puede tardar bastante en CPU.

### Progreso y estadísticas

En una terminal interactiva se muestran dos barras: una para el fragmento actual y otra para el trabajo completo. La barra del fragmento avanza con la marca final de cada segmento que entrega Faster-Whisper; la barra global pondera las duraciones reales de los WAV, incluido un último fragmento más corto.

```text
Fragmento 2/7  ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁  67%  audio 00:13:24/00:20:00 | 0.72x | ETA: ~00:09:08
Progreso general ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁  29%  1/7 completos | ETA: ~01:04:00
```

La velocidad se define como `segundos de audio / segundos reales`: `0.5x` significa que 10 minutos de audio requieren aproximadamente 20 minutos reales. La ETA aparece como `Calculando...` hasta disponer de una muestra suficiente y se reajusta con el trabajo observado.

Para forzar la preferencia de progreso o desactivar las barras:

```bash
python -m transcriptor_clases "videos/clase.mp4" --progreso
python -m transcriptor_clases "videos/clase.mp4" --sin-progreso
```

Cuando stdout no es una terminal (por ejemplo, al redirigir a un archivo), no se emiten secuencias de control ni barras dinámicas: se conservan mensajes de texto legibles.

### Sin dividir el audio

```bash
python -m transcriptor_clases "videos/clase.mp4" --fragmento-minutos 0
```

Generará `audio_completo.wav`, `txt/parte_001.txt` y `transcripcion_completa.txt`.

### Fragmentos de 10 minutos, sin marcas de tiempo

```bash
python -m transcriptor_clases "videos/clase.mp4" --fragmento-minutos 10 --sin-marcas-tiempo
```

### Elegir carpeta de salida

```bash
python -m transcriptor_clases "videos/clase.mp4" --salida "mis_transcripciones/clase_1"
```

### Detección automática de idioma

```bash
python -m transcriptor_clases "videos/entrevista.mp4" --idioma auto
```

### GPU NVIDIA compatible (opcional)

```bash
python -m transcriptor_clases "videos/clase.mp4" --modelo medium --dispositivo cuda --precision float16
```

Requiere hardware NVIDIA, controladores y bibliotecas CUDA/cuDNN compatibles con la versión de CTranslate2 instalada. Si no cuentas con ello, utiliza la configuración predeterminada en CPU.

## 4. Cómo reanudar si se interrumpe

Por ejemplo, si ya se escribieron `parte_001.txt`, `parte_002.txt` y `parte_003.txt`, vuelve a **ejecutar el mismo comando**:

```bash
python -m transcriptor_clases "videos/clase.mp4" --fragmento-minutos 20 --modelo small --idioma es
```

El sistema reutilizará el audio válido y los TXT terminados, y transcribirá las partes pendientes. Si una parte quedó a medias, el archivo temporal `.tmp.txt` **no se considera finalizado** y se vuelve a procesar.

Cada TXT nuevo se publica mediante renombrado atómico y queda asociado a un marcador interno en `.state/` con su tamaño y SHA-256. Al reanudar, un TXT vacío sin marcador, un archivo modificado o un marcador inválido se reprocesan. Los TXT no vacíos creados por la versión anterior se validan y migran automáticamente; no hace falta reiniciar un trabajo existente.

Puedes detener el proceso con `Ctrl+C`. El archivo temporal del fragmento actual se descarta, los fragmentos anteriores permanecen intactos y la consola informa cuántos quedaron guardados. Ejecuta de nuevo exactamente el mismo comando para continuar.

Para **empezar de cero en esa misma carpeta**, usa `--reiniciar`:

```bash
python -m transcriptor_clases "videos/clase.mp4" --reiniciar
```

**Precaución:** `--reiniciar` borra los archivos que el programa generó con nombres reservados (`parte_*.txt`, `parte_*.wav`, `audio_completo.wav`, etc.) dentro de la carpeta elegida. No lo uses si hiciste modificaciones manuales sobre esos archivos sin guardar antes una copia.

La configuración y las propiedades del archivo de origen se registran en `manifest.json`. Si cambias el video o parámetros como el modelo, duración, idioma o marcas de tiempo, el programa **no mezcla resultados incompatibles**: te pedirá otra `--salida` o `--reiniciar`.

## 5. Estructura de los resultados

```text
resultados/
└── clase/
    ├── manifest.json
    ├── .state/                 # marcadores de finalización verificables
    ├── audio/
    │   ├── audio_completo.wav
    │   └── partes/
    │       ├── parte_001.wav
    │       ├── parte_002.wav
    │       ├── parte_003.wav
    │       ├── parte_004.wav
    │       ├── parte_005.wav
    │       └── parte_006.wav
    ├── txt/
    │   ├── parte_001.txt
    │   ├── parte_002.txt
    │   ├── parte_003.txt
    │   ├── parte_004.txt
    │   ├── parte_005.txt
    │   └── parte_006.txt
    └── transcripcion_completa.txt
```

Ejemplo del contenido del TXT (tiempos **globales** del video, incluso en la parte 2 o 3):

```text
[00:20:00.000 --> 00:20:04.300] Hoy vamos a estudiar los conceptos fundamentales.
[00:20:04.300 --> 00:20:07.800] Comenzaremos con una definición.
```

Los TXT se guardan en **UTF-8**, aptos para abrir con VS Code, Bloc de notas u otros editores. El TXT completo incluye separadores `===== PARTE 001 =====` cuando hay fragmentación.

## 6. Qué hace internamente

1. **Validación:** comprueba la ruta de entrada, la carpeta de salida y un manifiesto de reanudación.
2. **Extracción:** FFmpeg genera `audio/audio_completo.wav` (mono, PCM 16 bits, 16 kHz).
3. **Fragmentación opcional:** Python (`wave`) crea WAV de N minutos; se trabaja en **bloques pequeños en memoria** y los límites son exactos en número de muestras.
4. **Transcripción:** Faster-Whisper procesa las partes secuencialmente con un mismo modelo cargado una sola vez.
5. **Guardado seguro:** al acabar cada parte, se guarda `txt/parte_XXX.txt` de forma atómica.
6. **Unión:** se crea `transcripcion_completa.txt`, con todas las partes en orden.

**Nota sobre los límites:** al cortar exactamente cada 20 minutos, puede dividirse una frase en la frontera entre partes. La división ayuda a reanudar y organizar el trabajo, pero no implica por sí misma mejor precisión ni menor tiempo total.

## 7. Opciones disponibles

| Opción | Efecto |
| --- | --- |
| `--salida RUTA` | Ubicación de los resultados. |
| `--fragmento-minutos N` | Minutos por parte (20 predeterminado, 0 sin dividir). |
| `--modelo MODELO` | `tiny`, `base`, `small`, `medium`, `large-v3` u otro compatible. |
| `--idioma es` | Especificar español. Usa `auto` para detectar idioma. |
| `--dispositivo cpu` | CPU por defecto; también `cuda` o `auto`. |
| `--precision auto` | Cuando el dispositivo es `cpu`, el valor `auto` se convierte en `int8`. |
| `--sin-marcas-tiempo` | El TXT no incluye marcas de tiempo. |
| `--sin-vad` | No elimina silencios mediante VAD. |
| `--reiniciar` | Reprocesa desde el inicio y limpia resultados generados. |
| `--progreso` | Solicita barras dinámicas si stdout es una terminal. |
| `--sin-progreso` | Desactiva las barras y usa mensajes de texto. |
| `--verbose` | Muestra detalles técnicos de carga y reanudación. |
| `--quiet` | Oculta mensajes informativos, pero conserva errores y cancelaciones. |
| `-h`, `--help` | Muestra la ayuda. |

`--verbose` y `--quiet` son excluyentes. `--progreso` y `--sin-progreso` también.

## 8. Limitaciones de las estimaciones

- Faster-Whisper entrega segmentos mediante un generador. La barra solo puede avanzar cuando llega un segmento nuevo y puede quedar quieta mientras el modelo calcula internamente.
- El porcentaje representa audio cubierto por marcas temporales, no una confirmación de que el fragmento terminó. La interfaz mantiene visible esa distinción y solo marca el fragmento como finalizado al agotarse correctamente el generador y publicar el TXT.
- Los silencios, especialmente con VAD activo, pueden producir saltos o dejar la última marca antes del final físico del WAV.
- La ETA es aproximada: cambia según la complejidad del audio, el modelo, la temperatura del equipo y la carga del sistema. No se hace una segunda pasada ni procesamiento adicional para alimentar la barra.

## 9. Preguntas frecuentes

**¿Hay que dejar la consola abierta?** Sí. Se ejecuta localmente y termina al completar los resultados. Si lo detienes, los TXT ya finalizados quedan guardados.

**¿Cuánto demora?** Depende de CPU/GPU, modelo, calidad del audio y duración. Un video de 2 horas puede tardar desde algunos minutos con una GPU potente hasta horas en CPU. Dividirlo aporta reanudación, no necesariamente velocidad.

**¿Puedo usar archivos de audio en vez de video?** Sí. FFmpeg acepta, entre otros, MP3, WAV, M4A, MKV y MP4. El resultado se normaliza a WAV antes de transcribir.

**¿Qué pasa si el profesor usa terminología difícil?** Un modelo más grande suele ayudar, pero conviene corregir manualmente nombres propios, fórmulas y términos técnicos. El modelo puede equivocarse o inventar palabras ante audio de mala calidad.

**¿Traducirá la clase al inglés?** No. El programa usa `task="transcribe"` y conserva el idioma hablado.

**¿Puedo borrar los WAV cuando termine?** Sí, después de verificar que los TXT están correctos. Si después quieres repetir una transcripción, se tendrán que extraer nuevamente.

**¿Necesito una clave de OpenAI?** No. Se usa un modelo open-source local mediante Faster-Whisper, sin API.

## 10. Pruebas automáticas

Desde el proyecto con el entorno virtual activado:

```bash
python -m pip install -e ".[test]"
python -m pytest -q
```

Como alternativa con solo la biblioteca estándar:

```bash
PYTHONPATH="$PWD/src" python -m unittest discover -s tests -v
```

Los tests utilizan transcriptores ficticios y archivos WAV diminutos: **no descargan modelos, no se conectan a Internet ni necesitan GPU**. Las pruebas de integración se omiten si FFmpeg no está instalado.

## 11. Organización del código

```text
transcriptor_clases/
├── README.md
├── pyproject.toml
├── requirements.txt
├── .gitignore
├── LICENSE
├── src/
│   └── transcriptor_clases/
│       ├── __init__.py
│       ├── __main__.py      # python -m transcriptor_clases
│       ├── cli.py           # argumentos de consola
│       ├── audio.py         # FFmpeg + troceado de WAV
│       ├── engine.py        # motor Faster-Whisper
│       ├── progress.py      # métricas, ETA y presentación de consola
│       └── pipeline.py      # reanudación + TXT + unión
└── tests/
    └── test_pipeline.py
```

## Referencias

- Faster-Whisper (documentación de sus opciones, modelos y VAD): https://github.com/SYSTRAN/faster-whisper
- FFmpeg (descarga e instalación): https://ffmpeg.org/download.html

---

Desarrollado para transcribir clases universitarias extensas, con salida TXT y recuperación de progreso. No incluye transcripción en directo ni identificación de hablantes.
