"""Create/update brainlife.io Pipeline-tab rules directly via the Warehouse
API.

The `bl` CLI has no rule subcommand (only app/bids/data/datatype/profile/
project/pub/resource) -- this replicates what the web UI's RuleModal.vue
does when you click "+Add Rule" in a project's Pipeline tab:

    POST/PUT https://brainlife.io/api/warehouse/rule
    PUT      https://brainlife.io/api/warehouse/rule/order/<project_id>   (splice into the pipeline group tree)

Gotcha worth knowing before using this: a rule with an unconfigured
*optional* input (e.g. an app's optional "events" input) can silently
never fire -- give it an explicit `input_selection={<input_id>: "ignore"}`
if the input isn't meant to gate the rule.
"""
import json
import urllib.request
import urllib.parse
import re
from pathlib import Path

from .bl_client import bl, parse_bl_json

API = "https://brainlife.io/api/warehouse"
DEFAULT_JWT_FILE = Path.home() / ".config" / "brainlife.io" / ".jwt"


def _jwt(jwt_file):
    return jwt_file.read_text().strip()


def _api(method, path, jwt_file=DEFAULT_JWT_FILE, params=None, body=None):
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(
            {
                k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                for k, v in params.items()
                if v is not None
            }
        )
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_jwt(jwt_file)}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def fetch_app(app_id, jwt_file=DEFAULT_JWT_FILE):
    res = _api("GET", "app", jwt_file, params={"find": {"_id": app_id}, "limit": 1})
    apps = res["apps"]
    if len(apps) != 1:
        raise RuntimeError(f"expected exactly 1 app for id={app_id}, found {len(apps)}")
    return apps[0]


def _compose_output_tag(name):
    tag = name or "untitled"
    return re.sub(r"\W", "_", tag.lower())


def build_rule_payload(
    app,
    project,
    name="",
    config=None,
    branch=None,
    input_tags=None,
    output_tags=None,
    subject_match="",
    session_match="",
    input_subject=None,
    input_session=None,
    input_multicount=None,
    input_selection=None,
    active=True,
):
    """Mirror RuleModal.vue's initializeAppIdsInRuleObj() +
    initializeAppConfigIdsInRuleConfigObj(): fill in every input/output id
    the app declares, defaulting config to the app's declared defaults and
    output tags to a slug of the rule name (composeOutputTag)."""
    config = dict(config or {})
    for key, spec in (app.get("config") or {}).items():
        if spec.get("type") == "input":
            continue  # file-input references, not user config -- RuleModal.vue strips these before posting
        config.setdefault(key, spec.get("default"))

    input_tags = dict(input_tags or {})
    output_tags = dict(output_tags or {})
    input_subject = dict(input_subject or {})
    input_session = dict(input_session or {})
    input_multicount = dict(input_multicount or {})
    input_selection = dict(input_selection or {})

    for inp in app.get("inputs", []):
        iid = inp["id"]
        input_tags.setdefault(iid, [])
        if inp.get("multi"):
            input_multicount.setdefault(iid, "1")

    for out in app.get("outputs", []):
        oid = out["id"]
        output_tags.setdefault(oid, [_compose_output_tag(name)])

    return {
        "project": project,
        "app": app["_id"],
        "branch": branch or app.get("github_branch"),
        "name": name,
        "active": active,
        "config": config,
        "subject_match": subject_match,
        "session_match": session_match,
        "extra_datatype_tags": {},
        "input_selection": input_selection,
        "input_tags": input_tags,
        "input_subject": input_subject,
        "input_session": input_session,
        "input_multicount": input_multicount,
        "input_project_override": {},
        "output_tags": output_tags,
        "archive": {
            out["id"]: {"do": out.get("archive", False), "desc": ""}
            for out in app.get("outputs", [])
        },
    }


def create_rule(
    project,
    app_id,
    jwt_file=DEFAULT_JWT_FILE,
    name="",
    config=None,
    branch=None,
    input_tags=None,
    output_tags=None,
    subject_match="",
    session_match="",
    input_subject=None,
    input_session=None,
    input_multicount=None,
    input_selection=None,
    active=True,
    dry_run=False,
):
    app = fetch_app(app_id, jwt_file)
    payload = build_rule_payload(
        app,
        project,
        name=name,
        config=config,
        branch=branch,
        input_tags=input_tags,
        output_tags=output_tags,
        subject_match=subject_match,
        session_match=session_match,
        input_subject=input_subject,
        input_session=input_session,
        input_multicount=input_multicount,
        input_selection=input_selection,
        active=active,
    )
    if dry_run:
        print(f"[dry-run] POST rule\n{json.dumps(payload, indent=2)}")
        return payload
    return _api("POST", "rule", jwt_file, body=payload)


def update_rule(rule_id, jwt_file=DEFAULT_JWT_FILE, dry_run=False, **fields):
    if dry_run:
        print(f"[dry-run] PUT rule/{rule_id}\n{json.dumps(fields, indent=2)}")
        return fields
    return _api("PUT", f"rule/{rule_id}", jwt_file, body=fields)


def list_rules(project, jwt_file=DEFAULT_JWT_FILE):
    res = _api(
        "GET",
        "rule",
        jwt_file,
        params={"find": {"project": project}, "sort": "create_date", "limit": 500},
    )
    return res["rules"]


def get_project_pipelines(project):
    projects = parse_bl_json(bl("project", "query", "--id", project, "-j"))
    if not projects:
        raise RuntimeError(f"project not found: {project}")
    return projects[0].get("pipelines")


def _find_group(node, group_name):
    if node.get("type") != "group":
        return None
    if group_name is None or node.get("name") == group_name:
        return node
    for item in node.get("items", []):
        found = _find_group(item, group_name)
        if found:
            return found
    return None


def _contains_rule(node, rule_id):
    if node.get("type") == "rule" and node.get("ruleId") == rule_id:
        return True
    return any(_contains_rule(item, rule_id) for item in node.get("items", []))


def add_rule_to_pipeline(project, rule_id, jwt_file=DEFAULT_JWT_FILE, group_name=None, dry_run=False):
    """Splice a rule into the project's Pipeline-tab group tree. If the
    project has no pipeline tree yet, initialize it from all of the
    project's existing (non-removed) rules first, same as pipelines.vue's
    load() does on first visit -- this will already include `rule_id` if
    it was just created, so no separate append is needed in that case."""
    pipelines = get_project_pipelines(project)
    if not pipelines:
        rules = list_rules(project, jwt_file)
        pipelines = {
            "type": "group",
            "name": "",
            "open": True,
            "color": "inherit",
            "items": [
                {"type": "rule", "ruleId": r["_id"]}
                for r in rules
                if not r.get("removed")
            ],
        }
    elif not _contains_rule(pipelines, rule_id):
        target = _find_group(pipelines, group_name) if group_name else pipelines
        if target is None:
            raise RuntimeError(f"no pipeline group named {group_name!r} in project {project}")
        target.setdefault("items", []).append({"type": "rule", "ruleId": rule_id})

    if dry_run:
        print(f"[dry-run] PUT rule/order/{project}\n{json.dumps(pipelines, indent=2)}")
        return pipelines
    return _api("PUT", f"rule/order/{project}", jwt_file, body=pipelines)


def set_pipeline(project, pipelines, jwt_file=DEFAULT_JWT_FILE, dry_run=False):
    """Replace the project's whole pipeline tree outright (e.g. to organize
    rules into named groups) rather than incrementally splicing one rule in."""
    if dry_run:
        print(f"[dry-run] PUT rule/order/{project}\n{json.dumps(pipelines, indent=2)}")
        return pipelines
    return _api("PUT", f"rule/order/{project}", jwt_file, body=pipelines)
