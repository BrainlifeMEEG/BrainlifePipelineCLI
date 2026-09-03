"""Chain brainlife.io app runs with retry + on-disk checkpointing.

`bl app run` hardcodes a random instance name on every call (see
bl-app-run.js: `apps[0].tags + "." + Math.random()`), so it can never group
multiple runs into a single brainlife.io "process" (an amaretti instance).
vendor/bl_scripts/bl-app-run-instanced.js is a locally-patched copy of that
script (one added --instance-name flag, otherwise identical staging/
submission logic) that reuses an existing instance by exact name instead.
Give every step for one subject/session the same instance_name and the
whole chain shows up as one process on brainlife.io.

`bl app wait` crashes with an UnhandledPromiseRejection surprisingly often
even when the underlying task finishes successfully (confirmed repeatedly:
task status 'finished' while `bl app wait` had already errored out) --
costing wasted duplicate task submissions on retry. app_wait() below polls
the amaretti task API directly instead, which doesn't have that failure
mode.
"""
import json
import os
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .bl_client import bl, parse_bl_json
from .state import save_state

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "bl_scripts"
DEFAULT_APP_RUN_INSTANCED = VENDOR_DIR / "bl-app-run-instanced.js"
DEFAULT_NODE_MODULES = "/usr/lib/node_modules/brainlife/node_modules"
DEFAULT_JWT_FILE = Path.home() / ".config" / "brainlife.io" / ".jwt"


def find_root_dataset(catalog, subject, required_tags):
    """Pick the one dataset in a `bl data query` catalog matching a subject
    and a set of required tags. Raises if there isn't exactly one match."""
    matches = [
        d
        for d in catalog
        if d.get("meta", {}).get("subject") == subject
        and set(required_tags).issubset(set(d.get("tags", [])))
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly 1 root dataset for subject={subject} tags={required_tags}, "
            f"found {len(matches)}"
        )
    return matches[0]["_id"]


def input_flags(input_ids, dataset_ids):
    """Pair each dataset id with its app-declared input id: '<input_id>:<dataset_id>'.

    input_ids is either a single input id (repeated for every dataset --
    e.g. `concat`'s single multi-valued "raw" slot) or a sequence with one
    id per dataset, in the same order as dataset_ids.
    """
    if isinstance(input_ids, str):
        input_ids = (input_ids,) * len(dataset_ids)
    if len(input_ids) != len(dataset_ids):
        raise ValueError(
            f"expected {len(input_ids)} inputs, got {len(dataset_ids)}"
        )
    return [f"{iid}:{did}" for iid, did in zip(input_ids, dataset_ids)]


@dataclass
class PipelineRunner:
    """Bundles the platform/project settings that would otherwise be
    module-level constants, so this engine carries no project-specific
    state of its own. One instance per project (and branch) you're
    scripting against.
    """

    project: str
    branch: str = "master"
    app_run_instanced: Path = DEFAULT_APP_RUN_INSTANCED
    node_modules: str = DEFAULT_NODE_MODULES
    jwt_file: Path = DEFAULT_JWT_FILE
    max_attempts: int = 4
    retry_delay_s: int = 30

    def app_run(
        self,
        app_id,
        input_ids,
        dataset_ids,
        config,
        tags=None,
        instance_name=None,
        instance_id=None,
        preferred_resource=None,
        dry_run=False,
        direct_deps=False,
    ):
        cmd = [
            "node",
            str(self.app_run_instanced),
            "--id",
            app_id,
            "--project",
            self.project,
            "--branch",
            self.branch,
            "--config",
            json.dumps(config),
            "-j",
        ]
        if preferred_resource:
            cmd += ["--preferred-resource", preferred_resource]
        for flag in input_flags(input_ids, dataset_ids):
            cmd += ["--input", flag]
        for tag in tags or []:
            cmd += ["--tag", tag]
        if instance_id:
            cmd += ["--instance-id", instance_id]
        elif instance_name:
            cmd += ["--instance-name", instance_name]
        if direct_deps:
            cmd += ["--direct-deps"]

        if dry_run:
            print(f"[dry-run] {' '.join(cmd)}")
            return None

        env = {**os.environ, "NODE_PATH": self.node_modules}
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"command failed: {' '.join(cmd)}\n{result.stderr}")
        task = parse_bl_json(result.stdout)
        if isinstance(task, list):
            task = task[0]
        return task["_id"]

    def app_wait(self, task_id, poll_interval=15, timeout_s=3600, dry_run=False):
        if dry_run:
            print(f"[dry-run] wait {task_id}")
            return

        token = self.jwt_file.read_text().strip()
        url = "https://brainlife.io/api/amaretti/task?find=" + urllib.request.quote(
            json.dumps({"_id": task_id})
        )
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {token}"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            tasks = data.get("tasks", [])
            if tasks:
                status = tasks[0].get("status")
                if status == "finished":
                    return
                if status in ("failed", "stopped"):
                    raise RuntimeError(
                        f"task {task_id} {status}: {tasks[0].get('status_msg')}"
                    )
            time.sleep(poll_interval)
        raise RuntimeError(f"task {task_id} did not finish within {timeout_s}s")

    def get_outputs(self, task_id, timeout_s=120, poll_interval=5):
        """Fetch a finished task's output datasets, waiting for each to
        reach status 'stored' (a task can report 'finished' while its
        outputs are still transiently 'storing', making them unusable as
        inputs yet)."""
        deadline = time.time() + timeout_s
        while True:
            out = bl("data", "query", "--taskid", task_id, "-j")
            datasets = parse_bl_json(out)
            if datasets and all(d.get("status") == "stored" for d in datasets):
                return {
                    d["prov"]["output_id"]: d["_id"]
                    for d in datasets
                    if d.get("prov", {}).get("output_id")
                }
            if time.time() >= deadline:
                statuses = {
                    d.get("prov", {}).get("output_id"): d.get("status")
                    for d in datasets
                }
                raise RuntimeError(
                    f"task {task_id} outputs not fully stored after {timeout_s}s: {statuses}"
                )
            time.sleep(poll_interval)

    def run_step(
        self,
        state,
        state_path,
        step_key,
        app_id,
        input_ids,
        dataset_ids,
        config,
        tags=None,
        instance_name=None,
        instance_id=None,
        preferred_resource=None,
        dry_run=False,
        output_id="out_dir",
        direct_deps=False,
    ):
        """Skip-if-already-done, else submit/wait/retry, checkpointing
        `state` to `state_path` after every successful step. `state` is
        mutated in place so callers can keep threading it through a chain
        of calls.

        `direct_deps=True` skips the CLI's usual /dataset/stage round-trip
        and depends directly on each input dataset's own producing task
        (via its prov.task/prov.subdir) instead -- avoids a redundant
        staging task and archive read on every step of a multi-app chain
        within one instance. Requires every input dataset to carry
        prov.task/prov.subdir (true for both app-task outputs and `bl data
        upload`ed datasets; not true for datasets predating that
        provenance tracking). Does not change how a step's own output gets
        archived -- that step is unavoidable and unaffected either way.
        """
        if step_key in state and "outputs" in state[step_key]:
            print(f"[skip] {step_key} already done -> {state[step_key]['outputs']}")
            return state[step_key]["outputs"][output_id]

        if dry_run:
            print(f"[run] {step_key}: app={app_id} inputs={dataset_ids} config={config}")
            self.app_run(
                app_id, input_ids, dataset_ids, config,
                tags=tags, instance_name=instance_name, instance_id=instance_id,
                preferred_resource=preferred_resource, dry_run=dry_run,
                direct_deps=direct_deps,
            )
            return f"DRYRUN:{step_key}"

        last_error = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                print(
                    f"[run] {step_key} (attempt {attempt}/{self.max_attempts}): "
                    f"app={app_id} inputs={dataset_ids} config={config}"
                )
                task_id = self.app_run(
                    app_id, input_ids, dataset_ids, config,
                    tags=tags, instance_name=instance_name, instance_id=instance_id,
                    preferred_resource=preferred_resource,
                    direct_deps=direct_deps,
                )
                self.app_wait(task_id)
                outputs = self.get_outputs(task_id)
                if output_id not in outputs:
                    raise RuntimeError(
                        f"task {task_id} finished but produced no '{output_id}' output: {outputs}"
                    )
                state[step_key] = {"task_id": task_id, "outputs": outputs}
                save_state(state_path, state)
                print(f"[done] {step_key} -> {outputs}")
                return outputs[output_id]
            except (RuntimeError, KeyError) as e:
                last_error = e
                print(f"[retry] {step_key} attempt {attempt}/{self.max_attempts} failed: {e}")
                if attempt < self.max_attempts:
                    time.sleep(self.retry_delay_s)
        raise RuntimeError(
            f"{step_key} failed after {self.max_attempts} attempts: {last_error}"
        )
