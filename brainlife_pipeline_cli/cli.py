#!/usr/bin/env python3
"""Command-line entrypoint for brainlife_pipeline_cli.

    blpipe rule create --project ID --app ID --name NAME [--config FILE]
                        [--input-tag INPUT_ID:TAG ...] [--subject-match REGEX]
                        [--session-match REGEX] [--inactive]
                        [--add-to-pipeline] [--group NAME] [--dry-run]
    blpipe rule list --project ID
    blpipe rule pipeline-add --project ID --rule-id ID [--group NAME] [--dry-run]

    blpipe step run --project ID --app ID --branch BRANCH
                     --input-id ID [--input-id ID ...] --dataset ID [--dataset ID ...]
                     [--config FILE] [--tag TAG ...] [--instance-name NAME]
                     [--preferred-resource ID] [--dry-run]

    blpipe state show PATH

One rule / one step per invocation -- for chaining many steps together
with retry and checkpointing across a whole subject/session, use
PipelineRunner as a library from your own driver script (see examples/).
"""
import argparse
import json
from pathlib import Path

from .rules import create_rule, list_rules, add_rule_to_pipeline
from .runner import PipelineRunner
from .state import load_state


def _parse_kv_list(pairs):
    out = {}
    for pair in pairs or []:
        key, _, val = pair.partition(":")
        out.setdefault(key, []).append(val)
    return out


def _cmd_rule_create(args):
    config = json.loads(args.config.read_text()) if args.config else {}
    rule = create_rule(
        project=args.project,
        app_id=args.app,
        name=args.name,
        config=config,
        branch=args.branch,
        input_tags=_parse_kv_list(args.input_tag),
        subject_match=args.subject_match,
        session_match=args.session_match,
        active=not args.inactive,
        dry_run=args.dry_run,
    )
    print(json.dumps(rule, indent=2))
    if args.add_to_pipeline:
        rule_id = rule.get("_id", "DRYRUN")
        add_rule_to_pipeline(args.project, rule_id, group_name=args.group, dry_run=args.dry_run)


def _cmd_rule_list(args):
    for rule in list_rules(args.project):
        print(f"{rule['_id']}  {'active' if rule.get('active') else 'inactive':8}  {rule.get('name', '')}")


def _cmd_rule_pipeline_add(args):
    result = add_rule_to_pipeline(args.project, args.rule_id, group_name=args.group, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


def _cmd_step_run(args):
    config = json.loads(args.config.read_text()) if args.config else {}
    runner = PipelineRunner(project=args.project, branch=args.branch)
    task_id = runner.app_run(
        args.app,
        args.input_id,
        args.dataset,
        config,
        tags=args.tag,
        instance_name=args.instance_name,
        preferred_resource=args.preferred_resource,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        return
    print(f"submitted task {task_id}, waiting...")
    runner.app_wait(task_id)
    outputs = runner.get_outputs(task_id)
    print(json.dumps(outputs, indent=2))


def _cmd_state_show(args):
    state = load_state(args.path)
    for step_key, entry in sorted(state.items()):
        print(f"{step_key}  task={entry.get('task_id')}  outputs={entry.get('outputs')}")


def build_parser():
    parser = argparse.ArgumentParser(prog="blpipe", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    rule = sub.add_parser("rule", help="manage Pipeline-tab rules")
    rule_sub = rule.add_subparsers(dest="rule_command", required=True)

    rc = rule_sub.add_parser("create", help="create a Pipeline-tab rule")
    rc.add_argument("--project", required=True)
    rc.add_argument("--app", required=True)
    rc.add_argument("--name", default="")
    rc.add_argument("--branch", default=None)
    rc.add_argument("--config", type=Path)
    rc.add_argument("--input-tag", action="append", metavar="INPUT_ID:TAG")
    rc.add_argument("--subject-match", default="")
    rc.add_argument("--session-match", default="")
    rc.add_argument("--inactive", action="store_true")
    rc.add_argument("--add-to-pipeline", action="store_true")
    rc.add_argument("--group", default=None)
    rc.add_argument("--dry-run", action="store_true")
    rc.set_defaults(func=_cmd_rule_create)

    rl = rule_sub.add_parser("list", help="list a project's rules")
    rl.add_argument("--project", required=True)
    rl.set_defaults(func=_cmd_rule_list)

    rp = rule_sub.add_parser("pipeline-add", help="splice an existing rule into the Pipeline-tab tree")
    rp.add_argument("--project", required=True)
    rp.add_argument("--rule-id", required=True)
    rp.add_argument("--group", default=None)
    rp.add_argument("--dry-run", action="store_true")
    rp.set_defaults(func=_cmd_rule_pipeline_add)

    step = sub.add_parser("step", help="run a single app step")
    step_sub = step.add_subparsers(dest="step_command", required=True)

    sr = step_sub.add_parser("run", help="submit one app run and wait for it to finish")
    sr.add_argument("--project", required=True)
    sr.add_argument("--app", required=True)
    sr.add_argument("--branch", default="master")
    sr.add_argument("--input-id", action="append", required=True, dest="input_id")
    sr.add_argument("--dataset", action="append", required=True)
    sr.add_argument("--config", type=Path)
    sr.add_argument("--tag", action="append")
    sr.add_argument("--instance-name", default=None)
    sr.add_argument("--preferred-resource", default=None)
    sr.add_argument("--dry-run", action="store_true")
    sr.set_defaults(func=_cmd_step_run)

    state = sub.add_parser("state", help="inspect a checkpoint file")
    state_sub = state.add_subparsers(dest="state_command", required=True)
    ss = state_sub.add_parser("show", help="print a state.json file's completed steps")
    ss.add_argument("path", type=Path)
    ss.set_defaults(func=_cmd_state_show)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
