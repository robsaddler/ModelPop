"""Generation pipelines and the intent router."""

from modelpop.generation.cad_gates import Gate, GateReport, GateResult, evaluate
from modelpop.generation.cad_loop import (
    Attempt,
    CadGenerationRun,
    CadLoopGenerator,
    edit_part,
    extract_script,
    generate_part,
)
from modelpop.generation.cad_prompt import SYSTEM_PROMPT, build_edit, build_request
from modelpop.generation.command_loop import CommandEditRun, edit_by_description
from modelpop.generation.command_prompt import build_edit_request, read_commands
from modelpop.generation.gpu_lease import GpuBusyError, GpuLease
from modelpop.generation.mesh_generator import (
    ExternalMeshGenerator,
    GenerationEnvironment,
    find_generation_python,
)

__all__ = [
    "SYSTEM_PROMPT",
    "Attempt",
    "CadGenerationRun",
    "CadLoopGenerator",
    "CommandEditRun",
    "ExternalMeshGenerator",
    "Gate",
    "GateReport",
    "GateResult",
    "GenerationEnvironment",
    "GpuBusyError",
    "GpuLease",
    "build_edit",
    "build_edit_request",
    "build_request",
    "edit_by_description",
    "edit_part",
    "evaluate",
    "extract_script",
    "find_generation_python",
    "generate_part",
    "read_commands",
]
