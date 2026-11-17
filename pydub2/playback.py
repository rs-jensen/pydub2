"""
Support for playing AudioSegments. Pyaudio will be used if it's installed,
otherwise will fallback to ffplay. Pyaudio is a *much* nicer solution, but
is tricky to install. See my notes on installing pyaudio in a virtualenv (on
OSX 10.10): https://gist.github.com/jiaaro/9767512210a1d80a8a0d
"""

import atexit
import os
import subprocess
import threading
from tempfile import NamedTemporaryFile
from .utils import _using, get_player_name, make_chunks, NOWINDOW, which

def _player(seg):
    with _using(seg.converter):
        PLAYER = get_player_name()
    if not os.path.dirname(PLAYER):
        PLAYER = which(PLAYER)
        if not PLAYER:
            raise FileNotFoundError("ffplay not found, pydub2 needs it to play")
    return PLAYER


def _play_with_ffplay(seg):
    PLAYER = _player(seg)
    f = NamedTemporaryFile("w+b", suffix=".wav", delete=False)
    f.close()
    try:
        seg.export(f.name, "wav")
        subprocess.call([PLAYER, "-nodisp", "-autoexit", "-hide_banner", "-loglevel", "quiet", f.name],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWINDOW)
    finally:
        os.remove(f.name)


def _play_with_pyaudio(seg):
    import pyaudio

    p = pyaudio.PyAudio()
    stream = p.open(format=p.get_format_from_width(seg.sample_width),
                    channels=seg.channels,
                    rate=seg.frame_rate,
                    output=True)

    # Just in case there were any exceptions/interrupts, we release the resource
    # So as not to raise OSError: Device Unavailable should play() be used again
    try:
        # break audio into half-second chunks (to allows keyboard interrupts)
        for chunk in make_chunks(seg, 500):
            stream.write(chunk._data)
    finally:
        stream.stop_stream()
        stream.close()

        p.terminate()


def _play_with_simpleaudio(seg):
    import simpleaudio
    return simpleaudio.play_buffer(
        seg.raw_data,
        num_channels=seg.channels,
        bytes_per_sample=seg.sample_width,
        sample_rate=seg.frame_rate
    )


def play(audio_segment):
    try:
        playback = _play_with_simpleaudio(audio_segment)
        try:
            playback.wait_done()
        except KeyboardInterrupt:
            playback.stop()
    except ImportError:
        pass
    else:
        return

    try:
        _play_with_pyaudio(audio_segment)
        return
    except ImportError:
        pass
    else:
        return

    _play_with_ffplay(audio_segment)


_live = set()


def _stop_all():
    # the watcher threads are daemons and die with the interpreter, so the players
    # would play on and the temp files would stay
    handles = list(_live)
    for h in handles:
        try:
            h._halt()
        except Exception:
            # the others still have to be stopped
            pass
    for h in handles:
        h._done.wait(5)


class PlayHandle:
    """
    Sound that is playing in the background. `stop()` ends it, `wait()` blocks until it
    has ended, and leaving a `with` block stops it. A handle that is dropped lets the sound
    play to its end, and every sound that is still playing when the interpreter exits is stopped.
    """

    def __init__(self, halt, done):
        self._halt = halt
        self._done = done
        _live.difference_update([h for h in _live if h._done.is_set()])
        _live.add(self)
        # registered once, and last, so it runs before the other exit handlers
        atexit.unregister(_stop_all)
        atexit.register(_stop_all)

    def stop(self):
        self._halt()
        self._done.wait()

    def wait(self, timeout=None):
        """Return True when the sound has ended, False when `timeout` seconds went first."""
        return self._done.wait(timeout)

    def is_playing(self):
        return not self._done.is_set()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()


def _async_simpleaudio(seg):
    play_obj = _play_with_simpleaudio(seg)
    done = threading.Event()

    def watch():
        try:
            play_obj.wait_done()
        finally:
            done.set()

    threading.Thread(target=watch, daemon=True).start()
    return PlayHandle(play_obj.stop, done)


def _async_pyaudio(seg):
    import pyaudio

    p = pyaudio.PyAudio()
    try:
        stream = p.open(format=p.get_format_from_width(seg.sample_width),
                        channels=seg.channels, rate=seg.frame_rate, output=True)
    except BaseException:
        p.terminate()
        raise
    done, halt = threading.Event(), threading.Event()

    def run():
        try:
            for chunk in make_chunks(seg, 100):
                if halt.is_set():
                    break
                stream.write(chunk._data)
        finally:
            try:
                stream.stop_stream()
                stream.close()
                p.terminate()
            finally:
                done.set()

    threading.Thread(target=run, daemon=True).start()
    return PlayHandle(halt.set, done)


def _async_ffplay(seg):
    PLAYER = _player(seg)
    f = NamedTemporaryFile("w+b", suffix=".wav", delete=False)
    f.close()  # closed before ffplay opens it, Windows cannot delete an open file
    done = threading.Event()
    proc = None

    def watch():
        try:
            proc.wait()
        finally:
            try:
                os.remove(f.name)
            except OSError:
                pass
            done.set()

    try:
        seg.export(f.name, "wav").close()
        proc = subprocess.Popen([PLAYER, "-nodisp", "-autoexit", "-hide_banner", "-loglevel", "quiet", f.name],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=NOWINDOW)
        threading.Thread(target=watch, daemon=True).start()
    except BaseException:
        if proc:
            proc.kill()
            proc.wait()
        os.remove(f.name)
        raise

    def halt():
        try:
            proc.terminate()
            proc.wait(2)
        except subprocess.TimeoutExpired:
            proc.kill()  # it ignored the polite request
        except OSError:
            pass

    return PlayHandle(halt, done)


def play_async(audio_segment):
    """Start playing and return a `PlayHandle`. Same backends and order as `play`."""
    for start in (_async_simpleaudio, _async_pyaudio):
        try:
            return start(audio_segment)
        except ImportError:
            pass
    return _async_ffplay(audio_segment)
