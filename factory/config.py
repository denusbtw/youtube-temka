import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

# OpenShorts prints Unicode encoder/ASR diagnostics; Windows consoles may default
# to cp1251 even when all media/transcript files use UTF-8.
if os.name=='nt':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')

load_dotenv()

DATA = Path(os.environ.get('FACTORY_DATA', 'factory-data')).resolve()
SCRATCH = Path(os.environ.get('FACTORY_SCRATCH',str(DATA/'scratch'))).resolve()
DB = DATA / 'jobs.sqlite3'
GIB = 1024 ** 3
SCRATCH_BYTES = int(float(os.environ.get('SCRATCH_GB', '8')) * GIB)
RESERVE_BYTES = int(float(os.environ.get('FREE_RESERVE_GB', '4')) * GIB)
TOKEN = os.environ.get('FACTORY_TOKEN', '')
AUTOMATIC_PUBLISHING = os.environ.get('FACTORY_AUTOMATIC_PUBLISHING', '1') == '1'
OLLAMA = os.environ.get('OLLAMA_URL', 'http://host.docker.internal:11434').rstrip('/')
MODEL = os.environ.get('OLLAMA_MODEL', 'qwen2.5:3b')
ASR_MODEL = os.environ.get('WHISPER_MODEL','small')
# Bound decoded audio/features even for multi-hour sources.
AUDIO_CHUNK_SECONDS = max(60, min(900, int(os.environ.get('ASR_CHUNK_SECONDS', '300'))))
AUTOMATIC_RETRIES = 2
SHORTS_MAX_SECONDS = int(os.environ.get('FACTORY_SHORTS_MAX_SECONDS', '180'))
try:
    preferences=json.loads((DATA/'settings.json').read_text(encoding='utf-8'))
    MODEL=preferences.get('ollama_model',MODEL)
    ASR_MODEL=preferences.get('whisper_model',ASR_MODEL)
except (OSError,ValueError):
    pass


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
