"""Toil Radar - Detect, quantify, and reduce SRE/DevOps toil."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("toil-radar")
except PackageNotFoundError:
    __version__ = "unknown"

from .git_signals import scan as scan_git
from .github_signals import scan as scan_github
from .pagerduty_signals import scan as scan_pagerduty
from .report import summarize
from .cli import main as cli_main

__all__ = ["scan_git", "scan_github", "scan_pagerduty", "summarize", "cli_main", "__version__"]
