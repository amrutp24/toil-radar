"""
Toil Tracker - Detect, visualize, and reduce DevOps toil
"""

from .toil_detector import ToilDetector
from .cli import main as cli_main

__version__ = "0.1.0"
__all__ = ["ToilDetector", "cli_main"]