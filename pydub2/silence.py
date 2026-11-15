"""
Various functions for finding/manipulating silence in AudioSegments
"""
import itertools
import operator

import _pydub2

from .utils import db_to_float


def _detect_silence_slow(audio_segment, min_silence_len, silence_thresh, seek_step):
    # the odd arguments the core refuses, done like pydub does
    seg_len = len(audio_segment)
    silence_thresh = db_to_float(silence_thresh) * audio_segment.max_possible_amplitude
    last_slice_start = seg_len - min_silence_len
    slice_starts = range(0, last_slice_start + 1, seek_step)
    if last_slice_start % seek_step:
        slice_starts = itertools.chain(slice_starts, [last_slice_start])

    silence_starts = [i for i in slice_starts if audio_segment[i:i + min_silence_len].rms <= silence_thresh]
    if not silence_starts:
        return []

    silent_ranges = []
    prev_i = silence_starts.pop(0)
    current_range_start = prev_i
    for silence_start_i in silence_starts:
        continuous = (silence_start_i == prev_i + seek_step)
        silence_has_gap = silence_start_i > (prev_i + min_silence_len)
        if not continuous and silence_has_gap:
            silent_ranges.append([current_range_start, prev_i + min_silence_len])
            current_range_start = silence_start_i
        prev_i = silence_start_i
    silent_ranges.append([current_range_start, prev_i + min_silence_len])
    return silent_ranges


def detect_silence(audio_segment, min_silence_len=1000, silence_thresh=-16, seek_step=1):
    """
    Returns a list of all silent sections [start, end] in milliseconds of audio_segment.
    Inverse of detect_nonsilent()

    audio_segment - the segment to find silence in
    min_silence_len - the minimum length for any silent section
    silence_thresh - the upper bound for how quiet is silent in dFBS
    seek_step - step size for interating over the segment in ms
    """
    seg_len = len(audio_segment)
    if seg_len < min_silence_len:
        return []
    seek_step = operator.index(seek_step)
    if seek_step == 0:
        raise ValueError("seek_step cant be 0")
    if seek_step < 0 or min_silence_len < 0:
        return _detect_silence_slow(audio_segment, min_silence_len, silence_thresh, seek_step)
    return _pydub2.detect_silence(audio_segment.raw_data, audio_segment.frame_rate,
                                  audio_segment.channels, audio_segment.sample_width,
                                  min_silence_len, silence_thresh, seek_step)


def detect_nonsilent(audio_segment, min_silence_len=1000, silence_thresh=-16, seek_step=1):
    """
    Returns a list of all nonsilent sections [start, end] in milliseconds of audio_segment.
    Inverse of detect_silent()

    audio_segment - the segment to find silence in
    min_silence_len - the minimum length for any silent section
    silence_thresh - the upper bound for how quiet is silent in dFBS
    seek_step - step size for interating over the segment in ms
    """
    silent_ranges = detect_silence(audio_segment, min_silence_len, silence_thresh, seek_step)
    len_seg = len(audio_segment)

    # if there is no silence, the whole thing is nonsilent
    if not silent_ranges:
        return [[0, len_seg]]

    # short circuit when the whole audio segment is silent
    if silent_ranges[0][0] == 0 and silent_ranges[0][1] == len_seg:
        return []

    prev_end_i = 0
    nonsilent_ranges = []
    for start_i, end_i in silent_ranges:
        nonsilent_ranges.append([prev_end_i, start_i])
        prev_end_i = end_i

    if end_i != len_seg:
        nonsilent_ranges.append([prev_end_i, len_seg])

    if nonsilent_ranges[0] == [0, 0]:
        nonsilent_ranges.pop(0)

    return nonsilent_ranges


def split_on_silence(audio_segment, min_silence_len=1000, silence_thresh=-16, keep_silence=100,
                     seek_step=1, return_ranges=False):
    """
    Returns list of audio segments from splitting audio_segment on silent sections

    audio_segment - original pydub.AudioSegment() object

    min_silence_len - (in ms) minimum length of a silence to be used for
        a split. default: 1000ms

    silence_thresh - (in dBFS) anything quieter than this will be
        considered silence. default: -16dBFS

    keep_silence - (in ms or True/False) leave some silence at the beginning
        and end of the chunks. Keeps the sound from sounding like it
        is abruptly cut off.
        When the length of the silence is less than the keep_silence duration
        it is split evenly between the preceding and following non-silent
        segments.
        If True is specified, all the silence is kept, if False none is kept.
        default: 100ms

    seek_step - step size for interating over the segment in ms

    return_ranges - when True the list holds (chunk, (start, end)) with the
        position of the chunk in audio_segment in ms
    """

    output = detect_nonsilent(audio_segment, min_silence_len, silence_thresh, seek_step)
    return _cut_ranges(audio_segment, output, keep_silence, return_ranges)


def detect_speech(audio_segment, threshold=0.5, min_speech_len=250, min_silence_len=100):
    """
    Returns a list of [start, end] in milliseconds where somebody is talking
    """
    return _pydub2.detect_speech(audio_segment.raw_data, audio_segment.frame_rate,
                                 audio_segment.channels, audio_segment.sample_width,
                                 threshold, min_speech_len, min_silence_len)


def split_on_speech(audio_segment, keep_silence=100, threshold=0.5, min_speech_len=250,
                    min_silence_len=100, return_ranges=False):
    """
    Like split_on_silence but the chunks are the parts where somebody is talking
    """
    output = detect_speech(audio_segment, threshold, min_speech_len, min_silence_len)
    return _cut_ranges(audio_segment, output, keep_silence, return_ranges)


def _cut_ranges(audio_segment, ranges, keep_silence, return_ranges=False):
    # from the itertools documentation
    def pairwise(iterable):
        "s -> (s0,s1), (s1,s2), (s2, s3), ..."
        a, b = itertools.tee(iterable)
        next(b, None)
        return zip(a, b)

    if isinstance(keep_silence, bool):
        keep_silence = len(audio_segment) if keep_silence else 0

    output_ranges = [
        [ start - keep_silence, end + keep_silence ]
        for (start,end) in ranges
    ]

    for range_i, range_ii in pairwise(output_ranges):
        last_end = range_i[1]
        next_start = range_ii[0]
        if next_start < last_end:
            range_i[1] = (last_end+next_start)//2
            range_ii[0] = range_i[1]

    output_ranges = [(max(start, 0), min(end, len(audio_segment))) for start, end in output_ranges]
    chunks = [audio_segment[start:end] for start, end in output_ranges]
    return list(zip(chunks, output_ranges)) if return_ranges else chunks


def detect_leading_silence(sound, silence_threshold=-50.0, chunk_size=10):
    """
    Returns the millisecond/index that the leading silence ends.

    audio_segment - the segment to find silence in
    silence_threshold - the upper bound for how quiet is silent in dFBS
    chunk_size - chunk size for interating over the segment in ms
    """
    trim_ms = 0 # ms
    assert chunk_size > 0 # to avoid infinite loop
    while sound[trim_ms:trim_ms+chunk_size].dBFS < silence_threshold and trim_ms < len(sound):
        trim_ms += chunk_size

    # if there is no end it should return the length of the segment
    return min(trim_ms, len(sound))


