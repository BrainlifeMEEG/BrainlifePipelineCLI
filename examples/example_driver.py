#!/usr/bin/env python3
"""Skeleton for a project-specific driver script built on PipelineRunner.

This is a template, not a runnable pipeline -- fill in APPS/INPUT_IDS/
configs for your own project the way local_scripts/ReproduceProject's
replicate_pipeline.py did for its (unrelated) prior study. Copy this file
into your project's own local_scripts/ directory and edit freely; nothing
in brainlife_pipeline_cli itself should need to change per-project.

Usage:
    python example_driver.py --dry-run     # print planned bl commands, run nothing
    python example_driver.py               # actually submit/wait on brainlife.io tasks

Safe to re-run: completed steps are skipped (state persisted in state.json,
next to this script).
"""
import argparse
import json
from pathlib import Path

from brainlife_pipeline_cli import PipelineRunner, find_root_dataset, bl, load_state, save_state

PROJECT = "REPLACE_WITH_YOUR_PROJECT_ID"
BRANCH = "v1.0"
SUBJECTS = ["S01"]

# app_id -> the input id(s) that app declares, in the order run_step's
# dataset_ids will be passed. A bare string is repeated for every dataset
# (e.g. a multi-valued "raw" input fed several files at once).
APPS = {
    "fif2mne": {"id": "REPLACE_WITH_APP_ID", "input_ids": "fif"},
}

STATE_FILE = Path(__file__).parent / "state.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    runner = PipelineRunner(project=PROJECT, branch=BRANCH)
    state = load_state(STATE_FILE)

    print(f"Querying project {PROJECT} for root datasets...")
    catalog = json.loads(bl("data", "query", "--project", PROJECT, "-j", "-l", "5000"))

    for subject in SUBJECTS:
        print(f"\n===== {subject} =====")
        raw_id = find_root_dataset(catalog, subject, ["raw"])

        fif2mne_out = runner.run_step(
            state, STATE_FILE,
            step_key=f"{subject}:fif2mne",
            app_id=APPS["fif2mne"]["id"],
            input_ids=APPS["fif2mne"]["input_ids"],
            dataset_ids=[raw_id],
            config={},
            tags=[subject],
            instance_name=subject,
            dry_run=args.dry_run,
        )
        # ... chain further run_step() calls the same way, threading each
        # step's output dataset id into the next step's dataset_ids.


if __name__ == "__main__":
    main()
