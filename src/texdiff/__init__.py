"""Fast, shareable LaTeX document difference reports."""
from .models import DiffReport, ExtractionOptions
from .pipeline import generate_git_report, generate_report

__all__ = ["DiffReport", "ExtractionOptions", "generate_report", "generate_git_report"]
__version__ = "2.0.0"
