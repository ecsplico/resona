"""Audio encoding helpers shared by the local TTS engines."""
import io

import numpy as np

from .types import SpeechResult


def to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """Encode a float waveform to 16-bit PCM WAV bytes."""
    import soundfile as sf

    samples = np.asarray(samples, dtype=np.float32).squeeze()
    if samples.ndim == 0:
        samples = samples.reshape(1)
    buf = io.BytesIO()
    sf.write(buf, samples, int(sample_rate), format="WAV", subtype="PCM_16")
    return buf.getvalue()


def to_numpy(audio) -> np.ndarray:
    """Coerce a torch.Tensor / MLX array / list to a float32 numpy array."""
    detach = getattr(audio, "detach", None)
    if callable(detach):  # torch.Tensor
        audio = detach().cpu().numpy()
    return np.asarray(audio, dtype=np.float32).squeeze()


def wav_result(samples: np.ndarray, sample_rate: int) -> SpeechResult:
    """Build a :class:`SpeechResult` (WAV) from a waveform + rate."""
    return SpeechResult(
        audio=to_wav_bytes(samples, sample_rate),
        content_type="audio/wav",
        sample_rate=int(sample_rate),
    )
