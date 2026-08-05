import importlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE_VERSIONS = ['v4', 'v5']
_V6_REQUIRED_FILES = ('scaler.pkl', 'pca.pkl', 'kmeans.pkl', 'scene_mapping.json', '.ready')


def _v6_is_installed():
    model_dir = Path(__file__).parent / 'v6'
    return all((model_dir / filename).is_file() for filename in _V6_REQUIRED_FILES)


def list_available_versions():
    """List complete predictor versions that can be loaded now."""
    versions = _BASE_VERSIONS.copy()
    if _v6_is_installed():
        versions.append('v6')
    return versions


# Kept for callers that display the currently installed choices.
AVAILABLE_VERSIONS = list_available_versions()

def get_predictor(version='v6'):
    """
    Get a ScenePredictor class for the specified version.
    
    Args:
        version: Version string (currently 'v4', 'v5', or installed 'v6')
        
    Returns:
        ScenePredictor: Class of the specified predictor version
        
    Raises:
        ValueError: If version is not available
        ImportError: If the predictor module cannot be imported
    """
    available_versions = list_available_versions()
    if version not in available_versions:
        raise ValueError(f"Predictor version '{version}' not available. Available versions: {available_versions}")
    
    try:
        # Dynamic import of the predictor module
        module_path = f"oculizer.scene_predictors.{version}.predictor"
        module = importlib.import_module(module_path)
        
        # Get the ScenePredictor class from the module
        ScenePredictor = getattr(module, 'ScenePredictor')
        
        logger.info(f"Loaded ScenePredictor from {module_path}")
        return ScenePredictor
        
    except ImportError as e:
        logger.error(f"Failed to import predictor version {version}: {e}")
        raise ImportError(f"Could not import predictor version '{version}': {e}")
    except AttributeError as e:
        logger.error(f"ScenePredictor class not found in {version}: {e}")
        raise ImportError(f"ScenePredictor class not found in version '{version}': {e}")

# Backward-compatible direct import follows the application default.
from .v6.predictor import ScenePredictor
