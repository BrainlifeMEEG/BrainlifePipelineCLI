# BrainlifePipelineCLI

A small, project-agnostic toolkit for scripting brainlife.io pipelines:
chaining app runs with retry and on-disk checkpointing, and managing
Pipeline-tab automation rules directly via the Warehouse API (the `bl` CLI
has no `rule` subcommand at all).

Extracted from `local_scripts/ReproduceProject/`, which was built for one
specific reproduction ("Latinus et al. 2015", project
`6a5939af13254aef69aecae4`) and hardcoded that project's app ids, subjects
and configs throughout. This package keeps the reusable engine and drops
all of that project-specific content — every function takes project/app/
config as parameters. Use it as a library from a small driver script you
write per project (see `examples/example_driver.py`), or use the `blpipe`
CLI directly for one-off rule management or single app runs.

## Why this exists instead of the `bl` CLI alone

- `bl app run` cannot group multiple runs into one visible brainlife.io
  "process" — it hardcodes a random instance name every call. This package
  vendors a one-line-patched copy of that script
  (`vendor/bl_scripts/bl-app-run-instanced.js`) adding `--instance-name` so
  every step for one subject/session can share a single amaretti instance.
- `bl app wait` crashes with an `UnhandledPromiseRejection` surprisingly
  often even when the underlying task finished successfully — wasting
  duplicate submissions on retry. `PipelineRunner.app_wait()` polls the
  amaretti task API directly instead, which doesn't have that failure mode.
- `bl` has no way to create or edit Pipeline-tab rules. `rules.py`
  replicates what the web UI's `RuleModal.vue` does by calling the
  Warehouse API directly.
- Long pipelines fail partway through (platform archiving flakiness is
  common). `PipelineRunner.run_step()` checkpoints every successful step to
  a JSON state file and skips already-done steps on re-run, so a driver
  script is safe to just re-invoke after a failure.

## Install / requirements

- Python 3.9+, standard library only (no pip installs needed for the
  Python side).
- The `bl` CLI installed and authenticated (`~/.config/brainlife.io/.jwt`
  must exist and be valid — same file the `bl` CLI itself uses).
- Node.js, with the system-wide `brainlife` npm package installed (its
  `node_modules` provides `commander`, `axios`, `request-promise-native`,
  etc. that the vendored script needs). `PipelineRunner` defaults
  `node_modules` to `/usr/lib/node_modules/brainlife/node_modules` — pass
  a different path via `PipelineRunner(..., node_modules=...)` if your
  system installed it elsewhere.

To use as a library, either run scripts from inside this directory, or add
it to your `PYTHONPATH`:

```bash
export PYTHONPATH="/path/to/BrainlifePipelineCLI:$PYTHONPATH"
```

## Usage

### As a library (chaining app runs)

```python
from brainlife_pipeline_cli import PipelineRunner, find_root_dataset, bl, load_state, save_state
import json

runner = PipelineRunner(project="YOUR_PROJECT_ID", branch="v1.0")
state = load_state("state.json")

catalog = json.loads(bl("data", "query", "--project", runner.project, "-j", "-l", "5000"))
raw_id = find_root_dataset(catalog, subject="S01", required_tags=["raw"])

out_dataset_id = runner.run_step(
    state, "state.json",
    step_key="S01:fif2mne",
    app_id="APP_ID",
    input_ids="fif",           # or a tuple, one input id per dataset, for multi-input apps
    dataset_ids=[raw_id],
    config={},
    tags=["S01"],
    instance_name="S01",       # groups every step tagged this way into one process
)
```

`run_step` mutates and checkpoints `state` after every successful step, and
skips (returning the cached output id) if `step_key` is already recorded —
safe to re-run the whole driver script after any failure. See
`examples/example_driver.py` for a fuller skeleton.

### CLI (`blpipe`)

```bash
# create a Pipeline-tab rule (mirrors "+Add Rule" in the web UI)
python -m brainlife_pipeline_cli.cli rule create \
    --project PROJECT_ID --app APP_ID --name "My rule" \
    --input-tag raw:clean --add-to-pipeline --dry-run

# list a project's rules
python -m brainlife_pipeline_cli.cli rule list --project PROJECT_ID

# splice an already-created rule into a named pipeline group
python -m brainlife_pipeline_cli.cli rule pipeline-add \
    --project PROJECT_ID --rule-id RULE_ID --group "QC"

# submit and wait on a single app run
python -m brainlife_pipeline_cli.cli step run \
    --project PROJECT_ID --app APP_ID --branch v1.0 \
    --input-id fif --dataset DATASET_ID --config config.json

# inspect a checkpoint file
python -m brainlife_pipeline_cli.cli state show state.json
```

(A `blpipe` console-script entrypoint isn't wired up via packaging yet —
invoke as `python -m brainlife_pipeline_cli.cli ...` for now, or add a
`setup.py`/`pyproject.toml` if you want `pip install -e .` + a real
`blpipe` binary.)

## Known operational gotchas (carried forward from the Latinus work)

- **Amaretti/osiris archiving flakiness**: tasks can fail transiently for
  infrastructure reasons unrelated to the app itself. `run_step`'s
  retry loop (4 attempts, 30s apart by default — tune via
  `PipelineRunner(max_attempts=..., retry_delay_s=...)`) exists because of
  this.
- **`bl app wait` unreliability**: see above — this package never calls it;
  `app_wait()` polls task status directly instead.
- **Config-schema-key vs. `main.py`-lookup-key drift**: a class of app bug
  where an app's `config.json` declares one key name but `main.py` reads a
  different one (e.g. `average-all` vs `average_all`) — looks like a
  platform/infra failure (task fails with no obvious cause) but is
  actually an app bug. Worth checking an app's own code before assuming a
  failure is this tool's fault.
- **Pipeline rules with an unconfigured optional input silently never
  fire**: if an app has an optional input (e.g. `epoch`'s optional
  `events`) that a rule doesn't need to gate on, set
  `input_selection={"events": "ignore"}` explicitly when creating the rule
  — otherwise the rule can sit `active: true` forever without ever
  triggering a single task.

## What's not here

Anything project-specific: app id registries, per-app config values,
subject/session lists, and the actual step sequence for any one pipeline.
Those belong in a driver script in the project's own repo (following the
pattern in `examples/example_driver.py`), imported against this package —
not added here.
