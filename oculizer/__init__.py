import os
import tempfile
from pathlib import Path

# Some read-only/containerized Python installations do not give Numba a
# writable cache location for librosa's compiled helpers. A process-local,
# portable cache root prevents librosa initialization from failing before the
# audio source is selected.
os.environ.setdefault(
    "NUMBA_CACHE_DIR",
    str(Path(tempfile.gettempdir()) / "oculizer-numba-cache"),
)

__all__ = ("Oculizer", "LogicalSceneRegistry", "audio_parameters", "utils")


def __getattr__(name):
    """Keep lightweight submodules from importing the audio/ML stack."""
    if name == "Oculizer":
        from oculizer.light import Oculizer
        return Oculizer
    if name == "LogicalSceneRegistry":
        from oculizer.scenes import LogicalSceneRegistry
        return LogicalSceneRegistry
    if name == "audio_parameters":
        from oculizer.config import audio_parameters
        return audio_parameters
    if name == "utils":
        from oculizer import utils
        return utils
    raise AttributeError(name)
