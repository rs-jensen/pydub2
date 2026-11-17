import os

strict = os.environ.get("PYDUB2_STRICT", "").lower() in ("1", "true", "yes", "on")
max_duration = None
max_bytes = None
ffmpeg_timeout = None

from .audio_segment import AudioSegment

__version__ = "0.0.2"
