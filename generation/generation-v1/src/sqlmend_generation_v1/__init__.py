"""SQLMend Generation Baseline and Generation v1 comparison core."""

from .contracts import BASELINE_SYSTEM_ID, GENERATION_V1_SYSTEM_ID
from .inputs import prepare_inputs
from .paths import ProjectPaths
from .runner import generate_system

__all__ = [
    "BASELINE_SYSTEM_ID",
    "GENERATION_V1_SYSTEM_ID",
    "ProjectPaths",
    "generate_system",
    "prepare_inputs",
]

__version__ = "1.0.0"
