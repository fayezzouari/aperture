"""Shared inference contract between the Aperture control plane and its runtimes.

Anything in here is imported by *both* the API (to validate configs, render
overlays, describe presets) and the runtime (to actually run the pipeline), so a
config that validates in the UI behaves identically on a GPU worker.
"""

from aperture_core.configspec import (  # noqa: F401
    OVERRIDABLE_THRESHOLDS,
    Backend,
    Decoder,
    InputSpec,
    ModelConfig,
    PostprocessConfig,
    PreprocessConfig,
    Task,
)
from aperture_core.presets import PRESETS, preset_config, preset_summaries  # noqa: F401

__all__ = [
    "ModelConfig",
    "PreprocessConfig",
    "PostprocessConfig",
    "InputSpec",
    "Task",
    "Backend",
    "Decoder",
    "OVERRIDABLE_THRESHOLDS",
    "PRESETS",
    "preset_config",
    "preset_summaries",
]
