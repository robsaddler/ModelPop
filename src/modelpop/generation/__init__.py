"""Generation pipelines and the intent router."""

from modelpop.generation.cad_gates import Gate, GateReport, GateResult, evaluate
from modelpop.generation.cad_loop import (
    Attempt,
    CadGenerationRun,
    extract_script,
    generate_part,
)
from modelpop.generation.cad_prompt import SYSTEM_PROMPT, build_request

__all__ = [
    "SYSTEM_PROMPT",
    "Attempt",
    "CadGenerationRun",
    "Gate",
    "GateReport",
    "GateResult",
    "build_request",
    "evaluate",
    "extract_script",
    "generate_part",
]
