"""Auto subtitles for ECHOES Montage.

Run by the native editor with ECHOES' embedded Python. Transcribes an audio file with
faster-whisper (the same local model as lyrics sync) and prints one JSON object per line:
{"language": ...}, {"start", "end", "text"} captions, {"progress": 0..1}, {"done": true}
or {"error": ...}. Captions are short (a few words) so they read well on screen.
"""
import json
import os
import sys


def emit(**fields):
    print(json.dumps(fields, ensure_ascii=False), flush=True)


def captions(segment, max_words=7, max_seconds=3.2):
    words = [w for w in (segment.words or []) if w.word.strip()]
    if not words:
        text = segment.text.strip()
        if text:
            yield segment.start, segment.end, text
        return
    chunk = []
    for word in words:
        chunk.append(word)
        text = word.word.strip()
        sentence_end = text[-1:] in ".?!…" and len(chunk) >= 3
        if len(chunk) >= max_words or word.end - chunk[0].start >= max_seconds or sentence_end:
            yield chunk[0].start, chunk[-1].end, "".join(w.word for w in chunk).strip()
            chunk = []
    if chunk:
        yield chunk[0].start, chunk[-1].end, "".join(w.word for w in chunk).strip()


def load_wav(path, rate=16000):
    """PCM WAV -> mono float32 at 16 kHz. The bundled PyAV cannot open files for faster-whisper."""
    import wave
    import numpy as np
    with wave.open(path, "rb") as source:
        width, channels, source_rate = source.getsampwidth(), source.getnchannels(), source.getframerate()
        raw = source.readframes(source.getnframes())
    if width != 2:
        raise ValueError("ожидался 16-битный WAV")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if source_rate != rate and len(data):
        positions = np.arange(0, len(data), source_rate / rate)
        data = np.interp(positions, np.arange(len(data)), data).astype(np.float32)
    return data


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        emit(error="usage: montage_transcribe.py AUDIO [language] [model]")
        return 1
    path = sys.argv[1]
    language = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "auto" else None
    size = sys.argv[3] if len(sys.argv) > 3 else "small"
    try:
        from faster_whisper import WhisperModel
    except Exception as error:  # noqa: BLE001
        emit(error="faster-whisper недоступен: %s" % error)
        return 2
    cpu = os.cpu_count() or 4
    try:
        model = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=max(1, min(4, cpu - 1)), local_files_only=True)
    except Exception:  # noqa: BLE001
        emit(error="model")
        return 3
    try:
        audio = load_wav(path)
    except Exception as error:  # noqa: BLE001
        emit(error="не удалось прочитать звук: %s" % error)
        return 4
    segments, info = model.transcribe(audio, language=language, word_timestamps=True, vad_filter=True)
    emit(language=info.language)
    total = max(info.duration, 0.01)
    for segment in segments:
        for start, end, text in captions(segment):
            emit(start=round(start, 3), end=round(end, 3), text=text)
        emit(progress=round(min(1.0, segment.end / total), 3))
    emit(done=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
