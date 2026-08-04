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

from oculizer.audio import AudioListener
from oculizer.light import Oculizer
from oculizer.light import mapping
from oculizer.scenes import SceneManager
from oculizer.config import audio_parameters
from oculizer import utils
