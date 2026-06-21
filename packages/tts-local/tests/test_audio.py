import io

import numpy as np
import soundfile as sf

from resona_tts_local.audio import to_numpy, to_wav_bytes, wav_result


def test_wav_bytes_roundtrip():
    data = np.linspace(-1.0, 1.0, 2400, dtype=np.float32)
    raw = to_wav_bytes(data, 24000)
    assert raw[:4] == b"RIFF"
    arr, sr = sf.read(io.BytesIO(raw))
    assert sr == 24000
    assert len(arr) == 2400


def test_wav_result_shape():
    result = wav_result(np.zeros(16, dtype=np.float32), 16000)
    assert result["content_type"] == "audio/wav"
    assert result["sample_rate"] == 16000
    assert isinstance(result["audio"], bytes)


def test_to_numpy_from_list():
    out = to_numpy([0.0, 0.5, 1.0])
    assert out.dtype == np.float32
    assert out.shape == (3,)


def test_to_numpy_detaches_tensor_like():
    class _Tensorish:
        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.array([1.0, 2.0], dtype=np.float32)

    out = to_numpy(_Tensorish())
    assert list(out) == [1.0, 2.0]
