"""
Toil Radar - Detect, visualize, and reduce DevOps toil
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("toil-radar")
except PackageNotFoundError:
    __version__ = "unknown"

from .toil_detector import ToilDetector
from .cli import main as cli_main

__all__ = ["ToilDetector", "cli_main", "__version__"]