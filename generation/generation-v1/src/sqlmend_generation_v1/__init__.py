"""SQLMend Phase 10 closed-book and Retrieval-v1 RAG generation core."""

from .contracts import G0_SYSTEM_ID, G1_SYSTEM_ID
from .inputs import prepare_inputs
from .paths import ProjectPaths
from .runner import generate_system

__all__ = [
    "G0_SYSTEM_ID",
    "G1_SYSTEM_ID",
    "ProjectPaths",
    "generate_system",
    "prepare_inputs",
]

__version__ = "1.0.0"
