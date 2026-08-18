"""Reusable engine for scripting brainlife.io pipelines: chaining app runs
with retry/checkpointing, and managing Pipeline-tab rules.

Extracted from local_scripts/ReproduceProject/ (built for a prior,
project-specific reproduction) and generalized so it carries no
project/app/subject-specific content of its own. See README.md.
"""
from .bl_client import bl, parse_bl_json
from .state import load_state, save_state
from .runner import PipelineRunner, find_root_dataset, input_flags
from .rules import (
    fetch_app,
    build_rule_payload,
    create_rule,
    update_rule,
    list_rules,
    get_project_pipelines,
    add_rule_to_pipeline,
    set_pipeline,
)

__all__ = [
    "bl",
    "parse_bl_json",
    "load_state",
    "save_state",
    "PipelineRunner",
    "find_root_dataset",
    "input_flags",
    "fetch_app",
    "build_rule_payload",
    "create_rule",
    "update_rule",
    "list_rules",
    "get_project_pipelines",
    "add_rule_to_pipeline",
    "set_pipeline",
]
