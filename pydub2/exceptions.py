class PydubException(Exception):
    """
    Base class for any Pydub exception
    """

    format = reason = hint = None


class TooManyMissingFrames(PydubException):
    pass


class InvalidDuration(PydubException):
    pass


class InvalidTag(PydubException):
    pass


class InvalidID3TagVersion(PydubException):
    pass


class CouldntDecodeError(PydubException):
    pass


class CouldntEncodeError(PydubException):
    pass


class MissingAudioParameter(PydubException):
    pass


def tagged(e, format=None, reason=None, hint=None):
    # for tools: what was sniffed, a short stable code and the command that fixes it
    e.format, e.reason, e.hint = format, reason, hint
    return e
