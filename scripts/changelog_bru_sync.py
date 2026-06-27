#!/usr/bin/env python3
"""
Generate a changelog entry from git changes, then sync Bruno request files from
the latest changelog section.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - surfaced at runtime
    OpenAI = None  # type: ignore[assignment]


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_CHANGELOG = "CHANGELOG.md"
DEFAULT_BRUNO_DIR = "bruno-collection/generated"
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEFAULT_MARKER = "sync bru"

MAX_FILE_BYTES = 20_000
MAX_DIFF_BYTES = 12_000
MAX_CHANGED_FILES = 25
MAX_PROJECT_TREE_DEPTH = 2
MAX_PROJECT_FILES_PER_DIR = 20
MAX_BRUNO_FILES_TO_READ = 20
MAX_PROMPT_CHARS = 120_000

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
BODY_METHODS = {"post", "put", "patch"}

SKIP_DIRS = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "env",
    "htmlcov",
    "node_modules",
    "staticfiles",
    "venv",
    "scripts",
}

SKIP_SUFFIXES = {
    ".7z",
    ".db",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".log",
    ".pdf",
    ".png",
    ".pyc",
    ".pyo",
    ".sqlite3",
    ".webp",
    ".zip",
}

SKIP_FILE_NAMES = {".DS_Store"}

CHANGELOG_HEADER_RE = re.compile(
    r"^##\s+(?P<serial>\d+)\.\s+(?P<title>.+?)\s+-\s+\[date:\s*(?P<date>[^\]]+)\]:\s*$",
    re.MULTILINE,
)


def print_step(message: str) -> None:
    print(f"[changelog-bru-sync] {message}", flush=True)


def run_command(command: list[str], root: Path) -> str:
    print_step(f"Running command: {' '.join(command)}")
    try:
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print_step(f"Command not found: {command[0]}")
        return ""

    if result.returncode != 0:
        stderr = result.stderr.strip()
        print_step(f"Command failed with exit code {result.returncode}: {stderr}")
        return ""

    return result.stdout


def should_skip(path: Path) -> bool:
    if path.name in SKIP_FILE_NAMES:
        return True
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    return path.suffix.lower() in SKIP_SUFFIXES


def should_ignore_for_changelog(path: Path) -> bool:
    if should_skip(path):
        return True
    ignored_prefixes = {
        Path("CHANGELOG.md"),
        Path("bruno-collection/generated"),
        Path("scripts/__pycache__"),
    }
    return any(path == prefix or prefix in path.parents for prefix in ignored_prefixes)


def safe_read_text(path: Path, max_bytes: int = MAX_FILE_BYTES) -> str:
    try:
        data = path.read_bytes()[:max_bytes]
    except OSError as exc:
        print_step(f"Could not read {path}: {exc}")
        return ""
    return data.decode("utf-8", errors="ignore")


def call_openai(prompt: str, model: str) -> str:
    print_step(f"Calling OpenAI Responses API with model: {model}.")
    if OpenAI is None:
        raise RuntimeError("The openai package is not installed. Install it with: pip install openai")

    client = OpenAI()
    response = client.responses.create(model=model, input=prompt)
    output = response.output_text.strip()
    if not output:
        raise RuntimeError("OpenAI returned empty output.")
    print_step(f"Received {len(output)} characters from OpenAI.")
    return output


def extract_json_object(text: str) -> Any:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise RuntimeError("Model response did not contain a JSON object.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse model JSON response: {exc}") from exc


def trim_context(context: dict[str, Any], max_chars: int = MAX_PROMPT_CHARS) -> dict[str, Any]:
    encoded = json.dumps(context, ensure_ascii=False)
    if len(encoded) <= max_chars:
        print_step(f"Context size is {len(encoded)} characters.")
        return context

    print_step(f"Trimming context from {len(encoded)} to about {max_chars} characters.")
    for key in ("changed_files", "selected_bru_files"):
        section = context.get(key)
        if not isinstance(section, dict):
            continue
        for path in sorted(section, key=lambda item: len(json.dumps(section[item], ensure_ascii=False)), reverse=True):
            if len(json.dumps(context, ensure_ascii=False)) <= max_chars:
                break
            payload = section[path]
            if isinstance(payload, dict):
                if "content" in payload and isinstance(payload["content"], str):
                    payload["content"] = payload["content"][: max(1200, len(payload["content"]) // 2)] + "\n... trimmed ..."
                if "diff" in payload and isinstance(payload["diff"], str):
                    payload["diff"] = payload["diff"][: max(800, len(payload["diff"]) // 2)] + "\n... trimmed ..."
            elif isinstance(payload, str):
                section[path] = payload[: max(1200, len(payload) // 2)] + "\n... trimmed ..."

    print_step(f"Trimmed context size is {len(json.dumps(context, ensure_ascii=False))} characters.")
    return context


def collect_project_tree(root: Path) -> list[str]:
    print_step(
        f"Collecting shallow project tree with depth={MAX_PROJECT_TREE_DEPTH}, "
        f"max_files_per_dir={MAX_PROJECT_FILES_PER_DIR}."
    )
    lines: list[str] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > MAX_PROJECT_TREE_DEPTH:
            return

        try:
            entries = sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        except OSError as exc:
            print_step(f"Could not list {directory}: {exc}")
            return

        files_seen = 0
        for entry in entries:
            rel = entry.relative_to(root)
            if should_skip(rel):
                continue
            indent = "  " * depth
            if entry.is_dir():
                lines.append(f"{indent}- {rel}/")
                walk(entry, depth + 1)
                continue
            files_seen += 1
            if files_seen <= MAX_PROJECT_FILES_PER_DIR:
                lines.append(f"{indent}- {rel}")
            elif files_seen == MAX_PROJECT_FILES_PER_DIR + 1:
                lines.append(f"{indent}- ... more files omitted")

    walk(root, 0)
    print_step(f"Collected {len(lines)} project tree lines.")
    return lines


def collect_project_context(root: Path) -> dict[str, Any]:
    print_step("Collecting light project context for changelog generation.")
    context_files = [
        "README.md",
        "server/server/urls.py",
        "server/server/settings.py",
    ]
    context: dict[str, str] = {}
    for relative in context_files:
        path = root / relative
        if path.exists():
            text = safe_read_text(path, max_bytes=10_000)
            if text.strip():
                context[relative] = text
    return {
        "project_name": root.name,
        "project_tree": collect_project_tree(root),
        "light_context_files": context,
    }


def classify_git_status(xy: str) -> str | None:
    if xy == "??" or "A" in xy:
        return "added"
    if "M" in xy or "R" in xy or "C" in xy:
        return "modified"
    return None


def classify_name_status(code: str) -> str | None:
    if not code:
        return None
    if code[0] == "A":
        return "added"
    if code[0] in {"M", "R", "C"}:
        return "modified"
    return None


def find_marker_window(root: Path, marker: str) -> dict[str, Any] | None:
    print_step(f"Searching git history for marker commits containing: {marker}")
    log_output = run_command(
        [
            "git",
            "log",
            "--grep",
            marker,
            "--fixed-strings",
            "--pretty=format:%H%x09%s",
        ],
        root,
    )
    markers: list[dict[str, str]] = []
    for line in log_output.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        if sha and subject:
            markers.append({"sha": sha.strip(), "subject": subject.strip()})

    if not markers:
        print_step("No marker commits found. Falling back to working-tree-only detection.")
        return None

    base_marker = markers[1] if len(markers) >= 2 else markers[0]
    print_step(
        f"Using marker commit {base_marker['sha'][:12]} "
        f"({base_marker['subject']}) as the diff baseline."
    )

    parent = run_command(["git", "rev-parse", f"{base_marker['sha']}^"], root).strip()
    if parent:
        diff_from = parent
        include_marker_commit = True
    else:
        diff_from = base_marker["sha"]
        include_marker_commit = False

    head_sha = run_command(["git", "rev-parse", "HEAD"], root).strip()
    return {
        "marker": marker,
        "base_marker_sha": base_marker["sha"],
        "base_marker_subject": base_marker["subject"],
        "head_sha": head_sha,
        "diff_from_sha": diff_from,
        "include_marker_commit": include_marker_commit,
        "marker_count": len(markers),
    }


def collect_marker_range_changes(
    root: Path,
    marker_window: dict[str, Any] | None,
) -> list[dict[str, str]]:
    if not marker_window:
        return []

    diff_from = marker_window["diff_from_sha"]
    head_sha = marker_window["head_sha"]
    print_step(
        f"Collecting committed file changes from {diff_from[:12]} to {head_sha[:12]}."
    )
    output = run_command(
        ["git", "diff", "--name-status", f"{diff_from}..{head_sha}"],
        root,
    )

    changes: list[dict[str, str]] = []
    for raw_line in output.splitlines():
        clean = raw_line.strip()
        if not clean:
            continue
        parts = clean.split("\t")
        code = parts[0]
        path_text = parts[-1]
        path = Path(path_text)
        if should_ignore_for_changelog(path):
            continue
        status_type = classify_name_status(code)
        if status_type is None:
            continue
        changes.append({"path": path_text, "status": status_type, "source": "marker-range"})

    print_step(f"Found {len(changes)} committed changes in the marker window.")
    return changes


def collect_worktree_changes(root: Path) -> list[dict[str, str]]:
    print_step("Parsing current git status for added and modified files.")
    status_output = run_command(
        ["git", "status", "--short", "--untracked-files=all"],
        root,
    )
    entries: list[dict[str, str]] = []
    for raw_line in status_output.splitlines():
        if not raw_line.strip():
            continue
        xy = raw_line[:2]
        path_text = raw_line[3:]
        path_text = path_text.split(" -> ")[-1].strip()
        path = Path(path_text)
        if should_ignore_for_changelog(path):
            continue

        status_type = classify_git_status(xy)
        if status_type is None:
            continue

        entries.append(
            {
                "path": path_text,
                "status": status_type,
                "xy": xy,
                "source": "working-tree",
            }
        )

    print_step(f"Found {len(entries)} working-tree changes.")
    return entries


def detect_changes(root: Path, marker: str) -> dict[str, Any]:
    marker_window = find_marker_window(root, marker)
    committed_changes = collect_marker_range_changes(root, marker_window)
    worktree_changes = collect_worktree_changes(root)

    deduped: dict[str, dict[str, str]] = {}
    for entry in committed_changes:
        deduped[entry["path"]] = entry
    for entry in worktree_changes:
        deduped[entry["path"]] = entry

    changed = list(deduped.values())[:MAX_CHANGED_FILES]
    print_step(f"Detected {len(changed)} changed files after merging marker-range and working-tree changes.")
    return {
        "marker_window": marker_window,
        "changed_files": changed,
    }


def collect_changed_file_context(
    root: Path,
    changed_files: list[dict[str, str]],
    marker_window: dict[str, Any] | None,
) -> dict[str, Any]:
    print_step("Collecting deep context for changed files.")
    contexts: dict[str, Any] = {}
    for entry in changed_files:
        relative = entry["path"]
        path = root / relative
        base_diff = ""
        if marker_window:
            base_diff = run_command(
                [
                    "git",
                    "diff",
                    marker_window["diff_from_sha"],
                    "--",
                    relative,
                ],
                root,
            )
        diff_unstaged = run_command(["git", "diff", "--", relative], root)
        diff_staged = run_command(["git", "diff", "--cached", "--", relative], root)
        combined_diff = (base_diff + "\n" + diff_staged + "\n" + diff_unstaged).strip()[
            :MAX_DIFF_BYTES
        ]
        content = safe_read_text(path) if path.exists() else ""
        contexts[relative] = {
            "status": entry["status"],
            "git_xy": entry.get("xy", ""),
            "source": entry.get("source", ""),
            "diff": combined_diff,
            "content": content,
        }
    print_step(f"Collected deep context for {len(contexts)} changed files.")
    return contexts


def get_next_changelog_serial(changelog_path: Path) -> int:
    text = safe_read_text(changelog_path, max_bytes=500_000)
    matches = list(CHANGELOG_HEADER_RE.finditer(text))
    return int(matches[-1].group("serial")) + 1 if matches else 1


def format_changelog_entry(
    serial: int,
    title: str,
    timestamp: str,
    added: list[dict[str, str]],
    modified: list[dict[str, str]],
) -> str:
    lines = [f"## {serial}. {title} - [date: {timestamp}]", ""]
    lines.append("### Added:")
    if added:
        for item in added:
            lines.append(f"- {item['path']}: {item['summary']}")
    else:
        lines.append("- None.")
    lines.append("")
    lines.append("### Modified:")
    if modified:
        for item in modified:
            lines.append(f"- {item['path']}: {item['summary']}")
    else:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def append_changelog_entry(
    changelog_path: Path,
    serial: int,
    title: str,
    added: list[dict[str, str]],
    modified: list[dict[str, str]],
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print_step(f"Appending changelog entry #{serial} dated {timestamp}.")
    entry = format_changelog_entry(serial, title, timestamp, added, modified)
    existing = safe_read_text(changelog_path, max_bytes=500_000) if changelog_path.exists() else ""
    separator = "\n\n" if existing.strip() else ""
    with changelog_path.open("a", encoding="utf-8") as changelog:
        changelog.write(separator)
        changelog.write(entry)
    print_step("CHANGELOG.md updated.")
    return entry


def generate_changelog_entry_with_llm(
    project_context: dict[str, Any],
    change_detection_context: dict[str, Any],
    changed_file_context: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    print_step("Asking OpenAI to summarize changed files into changelog data.")
    prompt_context = trim_context(
        {
            "project_context": project_context,
            "change_detection": change_detection_context,
            "changed_files": changed_file_context,
        }
    )
    prompt = f"""
You are writing a changelog entry for a software repository.

Provide:
1. a short title describing what changed overall
2. an Added section summarizing newly added files
3. a Modified section summarizing changed files

Requirements:
- Use only the provided repository context.
- Project context should stay short; changed-file analysis should be detailed.
- For API-related files, mention route paths, HTTP methods, auth behavior, and
  example payloads when the file context supports them.
- For non-API files, summarize the main functionality added or changed.
- Keep each file summary to one line suitable for a markdown bullet.
- Return JSON only with this exact shape:
{{
  "title": "short title",
  "added": [
    {{
      "path": "path/to/file.py",
      "summary": "what functionality was added"
    }}
  ],
  "modified": [
    {{
      "path": "path/to/file.py",
      "summary": "what changed"
    }}
  ]
}}

Repository context:
{json.dumps(prompt_context, indent=2, ensure_ascii=False)}
""".strip()
    payload = extract_json_object(call_openai(prompt, model))
    title = str(payload.get("title", "")).strip()
    added = payload.get("added", [])
    modified = payload.get("modified", [])
    if not title:
        raise RuntimeError("Model did not return a changelog title.")
    if not isinstance(added, list) or not isinstance(modified, list):
        raise RuntimeError("Model returned invalid Added/Modified data.")
    print_step("Received structured changelog data from OpenAI.")
    return {
        "title": title,
        "added": [item for item in added if isinstance(item, dict) and item.get("path") and item.get("summary")],
        "modified": [item for item in modified if isinstance(item, dict) and item.get("path") and item.get("summary")],
    }


def extract_latest_changelog_section(changelog_path: Path) -> dict[str, str]:
    print_step(f"Parsing latest changelog section from {changelog_path}.")
    text = safe_read_text(changelog_path, max_bytes=500_000)
    matches = list(CHANGELOG_HEADER_RE.finditer(text))
    if not matches:
        raise RuntimeError("No numbered changelog sections found.")
    latest_index = len(matches) - 1
    latest = matches[latest_index]
    body_start = latest.end()
    body_end = matches[latest_index + 1].start() if latest_index + 1 < len(matches) else len(text)
    return {
        "serial": latest.group("serial").strip(),
        "title": latest.group("title").strip(),
        "date": latest.group("date").strip(),
        "body": text[body_start:body_end].strip(),
    }


def collect_bruno_structure(root: Path, bruno_root: Path) -> dict[str, Any]:
    print_step("Collecting Bruno collection structure.")
    files: list[str] = []
    for path in sorted(bruno_root.rglob("*")):
        if path.is_file() and not should_skip(path.relative_to(root)):
            files.append(str(path.relative_to(root)))
    return {
        "collection_root": str(bruno_root.relative_to(root)),
        "files": files,
    }


def plan_bruno_changes_with_llm(
    latest_changelog: dict[str, str],
    bruno_structure: dict[str, Any],
    bruno_output_root: str,
    model: str,
) -> dict[str, Any]:
    print_step("Asking OpenAI to plan Bruno file additions and modifications.")
    prompt = f"""
You are planning Bruno collection updates from a changelog entry.

Use the latest changelog section and the current Bruno directory structure to:
1. decide which `.bru` files should be newly added
2. decide which existing `.bru` files should be modified
3. provide precise instructions for each file
4. choose only the existing Bruno files that need to be read next

Return JSON only with this exact shape:
{{
  "files_to_read": [
    "bruno-collection/generated/auth/login.bru"
  ],
  "actions": [
    {{
      "path": "bruno-collection/generated/auth/login.bru",
      "operation": "modify",
      "instruction": "what needs to change"
    }},
    {{
      "path": "bruno-collection/generated/food/new_endpoint.bru",
      "operation": "add",
      "instruction": "what new request file is needed"
    }}
  ]
}}

Rules:
- Only include existing files in files_to_read.
- Keep files_to_read minimal.
- Paths for new or modified request files must point inside {bruno_output_root}.
- Base decisions on the changelog and current Bruno structure only.

Latest changelog:
{json.dumps(latest_changelog, indent=2, ensure_ascii=False)}

Bruno structure:
{json.dumps(bruno_structure, indent=2, ensure_ascii=False)}
""".strip()
    payload = extract_json_object(call_openai(prompt, model))
    files_to_read = payload.get("files_to_read", [])
    actions = payload.get("actions", [])
    if not isinstance(files_to_read, list) or not isinstance(actions, list):
        raise RuntimeError("Model returned invalid Bruno plan data.")

    existing_files = {
        path for path in bruno_structure["files"] if path.startswith(bruno_output_root + "/")
    }
    filtered_read: list[str] = []
    for item in files_to_read:
        if isinstance(item, str) and item in existing_files and item not in filtered_read:
            filtered_read.append(item)
        if len(filtered_read) >= MAX_BRUNO_FILES_TO_READ:
            break

    filtered_actions: list[dict[str, str]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        path = action.get("path")
        operation = action.get("operation")
        instruction = action.get("instruction")
        if not isinstance(path, str) or not isinstance(operation, str) or not isinstance(instruction, str):
            continue
        if operation not in {"add", "modify"}:
            continue
        if not path.startswith(bruno_output_root + "/"):
            continue
        filtered_actions.append(
            {
                "path": path,
                "operation": operation,
                "instruction": instruction,
            }
        )
    if not filtered_actions:
        raise RuntimeError("Model did not return any actionable Bruno plan items.")
    print_step(
        f"Bruno plan has {len(filtered_actions)} actions and {len(filtered_read)} existing files to read."
    )
    return {"files_to_read": filtered_read, "actions": filtered_actions}


def read_selected_bru_files(root: Path, file_paths: list[str]) -> dict[str, str]:
    print_step("Reading selected Bruno files for targeted update.")
    contents: dict[str, str] = {}
    for relative in file_paths:
        path = root / relative
        if path.exists():
            text = safe_read_text(path)
            if text.strip():
                contents[relative] = text
    print_step(f"Loaded {len(contents)} Bruno files for update context.")
    return contents


def normalize_relative_bru_path(path: str, bruno_target_root: Path, root: Path) -> str:
    normalized = path.strip().lstrip("/")
    if normalized.startswith("bruno-collection/"):
        normalized = normalized[len("bruno-collection/") :]
    target_relative = str(bruno_target_root.relative_to(root))
    if normalized.startswith(target_relative + "/"):
        normalized = normalized[len(target_relative) + 1 :]
    if ".." in Path(normalized).parts:
        raise RuntimeError(f"Invalid Bruno relative path: {path}")
    return normalized


def sanitize_meta_name(value: str) -> str:
    clean = re.sub(r"\s+", " ", value).strip()
    clean = clean.replace("{", "(").replace("}", ")")
    return clean or "Untitled"


def format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).replace("\n", " ").strip()


def format_headers_block(headers: dict[str, Any]) -> str:
    lines = ["headers {"]
    for key, value in headers.items():
        lines.append(f"  {key}: {format_scalar(value)}")
    lines.append("}")
    return "\n".join(lines)


def format_json_body_block(payload: Any) -> str:
    body_json = json.dumps(payload, indent=2, ensure_ascii=False)
    lines = ["body:json {"]
    for line in body_json.splitlines():
        lines.append(f"  {line}")
    lines.append("}")
    return "\n".join(lines)


def render_bru_request(request: dict[str, Any], seq: int) -> str:
    method = str(request.get("method", "get")).strip().lower()
    if method not in HTTP_METHODS:
        raise RuntimeError(f"Unsupported HTTP method for Bruno rendering: {method}")

    name = sanitize_meta_name(str(request.get("name", "Untitled")))
    url = str(request.get("url", "{{baseUrl}}/")).strip()
    body_payload = request.get("json_body")
    body_mode = "json" if method in BODY_METHODS and body_payload is not None else "none"

    lines = [
        "meta {",
        f"  name: {name}",
        "  type: http",
        f"  seq: {seq}",
        "}",
        "",
        f"{method} {{",
        f"  url: {url}",
        f"  body: {body_mode}",
        "  auth: inherit",
        "}",
    ]

    headers = request.get("headers")
    if isinstance(headers, dict) and headers:
        lines.extend(["", format_headers_block(headers)])

    if body_mode == "json":
        lines.extend(["", format_json_body_block(body_payload)])

    lines.extend(
        [
            "",
            "settings {",
            "  encodeUrl: true",
            "  timeout: 0",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def generate_bru_specs_with_llm(
    latest_changelog: dict[str, str],
    plan: dict[str, Any],
    selected_bru_files: dict[str, str],
    bruno_target_root: Path,
    root: Path,
    model: str,
) -> list[dict[str, Any]]:
    print_step("Asking OpenAI to generate Bruno request specs for the planned changes.")
    prompt_context = trim_context(
        {
            "latest_changelog": latest_changelog,
            "plan": plan,
            "selected_bru_files": selected_bru_files,
        }
    )
    relative_output_root = str(bruno_target_root.relative_to(root))
    prompt = f"""
You are updating a Bruno collection from a changelog and a targeted update plan.

You will receive:
- the latest changelog section
- the list of Bruno actions to perform
- only the existing Bruno files that need to be modified

Return JSON only with this exact shape:
{{
  "requests": [
    {{
      "path": "bruno-collection/generated/auth/login.bru",
      "operation": "modify",
      "name": "Login",
      "method": "POST",
      "url": "{{{{baseUrl}}}}/rest-auth/login/",
      "headers": {{
        "Content-Type": "application/json"
      }},
      "json_body": {{
        "email": "user@example.com",
        "password": "StrongPassword123"
      }}
    }}
  ]
}}

Rules:
- Every returned path must be inside `{relative_output_root}`.
- Use only these methods: GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS.
- Include `Authorization: Bearer {{{{authToken}}}}` only when required.
- For JSON requests, include `Content-Type: application/json`.
- If no request body is needed, use `"json_body": null`.
- Preserve useful existing requests when modifying files, but update them to match the changelog instructions.
- For added files, create new request specs.
- Do not return raw `.bru` text.

Context:
{json.dumps(prompt_context, indent=2, ensure_ascii=False)}
""".strip()
    payload = extract_json_object(call_openai(prompt, model))
    requests = payload.get("requests", [])
    if not isinstance(requests, list) or not requests:
        raise RuntimeError("Model did not return any Bruno request specs.")

    normalized_specs: list[dict[str, Any]] = []
    for item in requests:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str):
            continue
        normalized_path = normalize_relative_bru_path(raw_path, bruno_target_root, root)
        if not normalized_path.endswith(".bru"):
            continue
        method = str(item.get("method", "")).strip().lower()
        if method not in HTTP_METHODS:
            continue
        normalized_specs.append(
            {
                "path": normalized_path,
                "operation": str(item.get("operation", "modify")),
                "name": item.get("name", Path(normalized_path).stem.replace("_", " ")),
                "method": method,
                "url": item.get("url", "{{baseUrl}}/"),
                "headers": item.get("headers", {}),
                "json_body": item.get("json_body"),
            }
        )

    if not normalized_specs:
        raise RuntimeError("Model output did not contain any valid Bruno request specs.")
    print_step(f"Received {len(normalized_specs)} Bruno request specs.")
    return normalized_specs


def resolve_bruno_target_root(root: Path, bruno_dir: str) -> Path:
    target_root = (root / bruno_dir).resolve()
    bruno_root = (root / "bruno-collection").resolve()
    if target_root != bruno_root and bruno_root not in target_root.parents:
        raise RuntimeError("--bruno-dir must point inside bruno-collection.")
    return target_root


def write_bru_files(target_root: Path, specs: list[dict[str, Any]]) -> list[Path]:
    print_step(f"Writing Bruno files into {target_root}.")
    target_root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for seq, spec in enumerate(specs, start=1):
        full_path = (target_root / spec["path"]).resolve()
        if target_root not in full_path.parents:
            raise RuntimeError(f"Refusing to write outside target Bruno directory: {spec['path']}")
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(render_bru_request(spec, seq), encoding="utf-8")
        written.append(full_path)
    print_step(f"Wrote {len(written)} Bruno files.")
    return written


def run_changelog_generation(
    root: Path,
    changelog_path: Path,
    model: str,
    marker: str,
    dry_run: bool,
) -> dict[str, Any]:
    detection = detect_changes(root, marker)
    changed_files = detection["changed_files"]
    if not changed_files:
        raise RuntimeError("No added or modified git-tracked/untracked files found.")

    project_context = collect_project_context(root)
    changed_context = collect_changed_file_context(
        root,
        changed_files,
        detection["marker_window"],
    )
    llm_data = generate_changelog_entry_with_llm(
        project_context,
        {
            "marker": marker,
            "marker_window": detection["marker_window"],
            "changed_file_count": len(changed_files),
        },
        changed_context,
        model,
    )
    serial = get_next_changelog_serial(changelog_path)

    if dry_run:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        preview = format_changelog_entry(
            serial,
            llm_data["title"],
            timestamp,
            llm_data["added"],
            llm_data["modified"],
        )
        print_step("Dry run enabled; changelog entry preview:")
        print(preview)
    else:
        append_changelog_entry(
            changelog_path,
            serial,
            llm_data["title"],
            llm_data["added"],
            llm_data["modified"],
        )

    return {
        "serial": str(serial),
        "title": llm_data["title"],
        "added": llm_data["added"],
        "modified": llm_data["modified"],
    }


def run_bruno_sync(root: Path, changelog_path: Path, bruno_dir: str, model: str, dry_run: bool) -> dict[str, Any]:
    latest_changelog = extract_latest_changelog_section(changelog_path)
    bruno_root = (root / "bruno-collection").resolve()
    bruno_target_root = resolve_bruno_target_root(root, bruno_dir)
    bruno_output_root = str(bruno_target_root.relative_to(root))
    bruno_structure = collect_bruno_structure(root, bruno_root)
    plan = plan_bruno_changes_with_llm(
        latest_changelog,
        bruno_structure,
        bruno_output_root,
        model,
    )
    selected_bru_files = read_selected_bru_files(root, plan["files_to_read"])
    specs = generate_bru_specs_with_llm(
        latest_changelog,
        plan,
        selected_bru_files,
        bruno_target_root,
        root,
        model,
    )

    if dry_run:
        print_step("Dry run enabled; printing Bruno request specs.")
        print(json.dumps(specs, indent=2, ensure_ascii=False))
        written: list[Path] = []
    else:
        written = write_bru_files(bruno_target_root, specs)

    return {
        "plan": plan,
        "specs": specs,
        "written": [str(path.relative_to(root)) for path in written],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate changelog entries from git changes and sync Bruno files from the latest changelog."
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_PROJECT_ROOT),
        help="Repository root. Defaults to the parent of scripts/.",
    )
    parser.add_argument(
        "--changelog",
        default=DEFAULT_CHANGELOG,
        help="Changelog file to append and parse. Defaults to CHANGELOG.md.",
    )
    parser.add_argument(
        "--bruno-dir",
        default=DEFAULT_BRUNO_DIR,
        help="Bruno output directory relative to repo root. Defaults to bruno-collection/generated.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="OpenAI model to use. Defaults to OPENAI_MODEL or gpt-4.1-mini.",
    )
    parser.add_argument(
        "--marker",
        default=DEFAULT_MARKER,
        help="Commit-message marker used to choose the git diff baseline. Defaults to 'sync bru'.",
    )
    parser.add_argument(
        "--changelog-only",
        action="store_true",
        help="Only generate and append the changelog entry.",
    )
    parser.add_argument(
        "--bruno-only",
        action="store_true",
        help="Only parse the latest changelog and update Bruno files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated outputs instead of writing files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    changelog_path = root / args.changelog

    print_step(f"Starting workflow in {root}.")
    if not args.bruno_only:
        run_changelog_generation(
            root,
            changelog_path,
            args.model,
            args.marker,
            args.dry_run,
        )

    if not args.changelog_only:
        run_bruno_sync(root, changelog_path, args.bruno_dir, args.model, args.dry_run)

    print_step("Workflow complete.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print_step(f"ERROR: {exc}")
        raise SystemExit(1)
