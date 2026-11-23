"""Regression checks for bounded logs and checkpoint-safe ASR memory settings."""
import json
import sys
import threading
import time
import tracemalloc

import pytest

from factory import config, media, pipeline
from factory.models import JobRequest
from factory.progress import Progress


@pytest.mark.parametrize('newline', [True, False])
def test_noisy_child_does_not_accumulate_logs(monkeypatch, newline):
    monkeypatch.setattr(media, 'preflight', lambda: None)
    # 32 MiB of output with a deliberately slower consumer, including the case
    # where readline() without a size limit would allocate one enormous string.
    chunk = "'x'*4095+'\\n'" if newline else "'x'*4096"
    command = [sys.executable, '-c',
               f"import sys; chunk={chunk}; "
               "[sys.stdout.write(chunk) for _ in range(8192)]"]
    tracemalloc.start()
    try:
        media.run(command, tick=lambda *_: time.sleep(.0001), timeout=30)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024**2


def test_cancellation_releases_reader_with_full_log_queue(monkeypatch):
    monkeypatch.setattr(media, 'preflight', lambda: None)
    before = set(threading.enumerate())
    def cancel(*_):
        time.sleep(.1)  # Let the output queue fill before cancellation.
        raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError, match='cancelled'):
        media.run([sys.executable, '-c',
                   "import sys\nwhile True: sys.stdout.write('x'*4096)"], tick=cancel)
    assert not (set(threading.enumerate()) - before)


def test_media_progress_does_not_rescan_scratch_per_log_line(monkeypatch):
    checks = []
    progress = []
    monkeypatch.setattr(media, 'preflight', lambda: checks.append(True))
    media.run([sys.executable, '-c',
               "for i in range(1000): print('out_time_us='+str(i))"],
              tick=lambda current, _: progress.append(current) if current is not None else None,
              timeout=15)
    assert 2 <= len(checks) < 20
    assert len(progress) == 1000 and progress[-1] == .000999


def test_asr_retry_keeps_small_chunks_and_reuses_completed_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'AUDIO_CHUNK_SECONDS', 300)
    root = tmp_path/'job'
    root.mkdir()
    downloads = []
    monkeypatch.setattr(media, 'download', lambda source, path, start, end, *a:
                        (downloads.append((start, end)), path.touch()))
    failed = False
    def transcribe(args, *a, **kw):
        nonlocal failed
        output = args[4]
        if output.endswith('transcript-300.json') and not failed:
            failed = True
            raise RuntimeError('interrupted second chunk')
        with open(output, 'w', encoding='utf-8') as stream:
            json.dump({'language': 'en', 'segments': [
                {'start': 1, 'end': 2, 'text': 'speech',
                 'words': [{'word': ' speech', 'start': 1, 'end': 2}]}]}, stream)
    monkeypatch.setattr(media, 'run', transcribe)
    request = JobRequest(source='upload:test.mp4', rights_confirmed=True,
                         save_drive=False, start=100)
    progress = Progress(lambda *a, **kw: None)
    def process():
        return pipeline.transcribe_chunks(request.source, request, root, {},
                                          750, 720, progress, lambda **kw: None)
    with pytest.raises(RuntimeError, match='interrupted'):
        process()
    monkeypatch.setattr(config, 'AUDIO_CHUNK_SECONDS', 60)
    result = process()
    assert downloads == [(100, 400), (400, 700), (400, 700), (700, 750)]
    assert [s['start'] for s in result['segments']] == [1, 301, 601]
    assert [s['words'][0]['start'] for s in result['segments']] == [1, 301, 601]
    assert json.loads((root/'asr-chunks.json').read_text()) == {'seconds': 300}
