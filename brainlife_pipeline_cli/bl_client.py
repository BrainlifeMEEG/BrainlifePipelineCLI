"""Thin wrapper around the `bl` CLI, plus a parser tolerant of its
occasional debug noise on stdout."""
import json
import re
import subprocess


def bl(*args, capture=True):
    """Run `bl <args>`, raising with stderr on non-zero exit."""
    cmd = ["bl", *args]
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout


def parse_bl_json(out):
    """`bl ... -j` sometimes prints a stray debug console.log (JS object
    literal, not JSON) before the real JSON.stringify result. Both start
    with a line that is just '{' or '[', so find the *last* such block.

    Only column-0 braces/brackets are top-level blocks (debug dump(s) or
    the real JSON); nested object/array braces inside either block are
    indented and must be skipped. The debug dumps are JS console.log(obj)
    (unquoted keys); the real result is JSON.stringify(x, null, 4) (quoted
    keys) -- use that to pick the right one, in case there's more than one
    column-0 block (e.g. multi-input apps).
    """
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        pass
    lines = out.splitlines()
    candidates = [
        i
        for i, line in enumerate(lines)
        if line in ("{", "[")
        and i + 1 < len(lines)
        and re.match(r'^\s*"[^"]+":', lines[i + 1])
    ]
    if not candidates:
        raise RuntimeError(f"no JSON object found in bl output:\n{out}")
    start = sum(len(l) + 1 for l in lines[: candidates[-1]])
    return json.loads(out[start:])
