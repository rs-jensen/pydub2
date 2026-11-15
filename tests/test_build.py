import pytest

import _pydub2


def test_decode_garbage_raises():
    with pytest.raises(RuntimeError):
        _pydub2.decode(b"not audio")
