#!/usr/bin/env python3
"""
Use OpenAI to generate Bruno API request files from the latest numbered
CHANGELOG section and a bounded subset of relevant project files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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

MAX_FILE_INDEX = 250
MAX_SELECTED_FILES = 14
MAX_FILE_BYTES = 20_000
MAX_TOTAL_CONTEXT_CHARS = 120_000
HTTP_METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
}
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

PRIORITY_FILE_NAMES = {
    "README.md",
    "urls.py",
    "views.py",
    "auth_views.py",
    "serializers.py",
    "models.py",
    "settings.py",
    "bruno.json",
}


def print_step(message: str) -> None:
    print(f"[bru-generator] {message}", flush=True)


def should_skip(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    return path.suffix.lower() in SKIP_SUFFIXES


def safe_read_text(path: Path, max_bytes: int = MAX_FILE_BYTES) -> str:
    try:
        data = path.read_bytes()[:max_bytes]
    except OSError as exc:
        print_step(f"Could not read {path}: {exc}")
        return ""
    return data.decode("utf-8", errors="ignore")


def extract_latest_changelog_chunk(changelog_path: Path) -> dict[str, str]:
    print_step(f"Parsing latest numbered CHANGELOG section from {changelog_path}.")
    text = safe_read_text(changelog_path, max_bytes=500_000)
    if not text.strip():
        raise RuntimeError("CHANGELOG.md is empty.")

    pattern = re.compile(
        r"^##\s+(?P<serial>\d+)\.\s+(?P<title>.+?)\s+-\s+\[date:\s*(?P<date>[^\]]+)\]:\s*$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        raise RuntimeError(
            "No changelog sections matched the required format: "
            "## <serial no>. <title> - [date: datetime]:"
        )

    latest_index = len(matches) - 1
    latest = matches[latest_index]
    chunk_start = latest.end()
    chunk_end = matches[latest_index + 1].start() if latest_index + 1 < len(matches) else len(text)
    chunk = text[chunk_start:chunk_end].strip()
    if not chunk:
        raise RuntimeError("Latest changelog section is present but contains no body text.")

    result = {
        "serial": latest.group("serial").strip(),
        "title": latest.group("title").strip(),
        "date": latest.group("date").strip(),
        "body": chunk,
    }
    print_step(
        f"Latest changelog section is #{result['serial']} "
        f"({result['title']}) dated {result['date']}."
    )
    return result


def build_file_index(root: Path) -> list[str]:
    print_step("Building lightweight project file index for file selection.")
    indexed: list[tuple[int, str]] = []

    for current_root, dir_names, file_names in os.walk(root):
        current_path = Path(current_root)
        dir_names[:] = [
            name
            for name in dir_names
            if name not in SKIP_DIRS and not should_skip(current_path / name)
        ]

        for file_name in sorted(file_names):
            path = current_path / file_name
            relative_path = path.relative_to(root)
            if should_skip(relative_path):
                continue

            score = 0
            if file_name in PRIORITY_FILE_NAMES:
                score += 5
            if "bruno-collection" in relative_path.parts:
                score += 3
            if any(
                part in {"server", "administrator", "food", "utils"}
                for part in relative_path.parts
            ):
                score += 2
            if path.suffix.lower() in {".py", ".md", ".json", ".bru", ".yml", ".yaml"}:
                score += 1

            indexed.append((score, str(relative_path)))

    indexed.sort(key=lambda item: (-item[0], item[1]))
    file_index = [path for _, path in indexed[:MAX_FILE_INDEX]]
    print_step(f"Indexed {len(file_index)} candidate files.")
    return file_index


def call_openai(prompt: str, model: str) -> str:
    print_step(f"Calling OpenAI Responses API with model: {model}.")
    if OpenAI is None:
        raise RuntimeError("The openai package is not installed. Install it with: pip install openai")

    client = OpenAI()
    response = client.responses.create(
        model=model,
        input=prompt,
    )
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
    payload = text[start : end + 1]
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse model JSON response: {exc}") from exc


def choose_files_with_llm(
    changelog_chunk: dict[str, str],
    file_index: list[str],
    model: str,
) -> list[str]:
    print_step("Asking OpenAI which files are worth reading.")
    prompt = f"""
You are selecting the minimum set of repository files needed to generate Bruno
API request files.

You will receive:
- only the latest CHANGELOG section body
- a lightweight file index

Choose at most {MAX_SELECTED_FILES} files that are most useful for discovering:
- API endpoints
- request payloads
- authentication flows
- required headers or tokens
- existing Bruno collection conventions

Prefer files such as urls.py, views.py, serializers.py, models.py, README.md,
and existing Bruno collection files when relevant.

Return JSON only with this shape:
{{
  "files_to_read": ["path/one.py", "path/two.py"],
  "why": "short explanation"
}}

Latest changelog metadata:
- serial: {changelog_chunk["serial"]}
- title: {changelog_chunk["title"]}
- date: {changelog_chunk["date"]}

Latest changelog body:
{changelog_chunk["body"]}

Project file index:
{json.dumps(file_index, indent=2)}
""".strip()
    response_text = call_openai(prompt, model)
    payload = extract_json_object(response_text)
    chosen = payload.get("files_to_read", [])
    if not isinstance(chosen, list):
        raise RuntimeError("files_to_read was not a JSON list.")

    filtered: list[str] = []
    allowed = set(file_index)
    for item in chosen:
        if isinstance(item, str) and item in allowed and item not in filtered:
            filtered.append(item)
        if len(filtered) >= MAX_SELECTED_FILES:
            break

    if not filtered:
        raise RuntimeError("Model did not select any readable files.")

    print_step(f"Model selected {len(filtered)} files to inspect.")
    return filtered


def read_selected_files(root: Path, selected_files: list[str]) -> dict[str, str]:
    print_step("Reading the selected project files.")
    samples: dict[str, str] = {}
    for relative in selected_files:
        text = safe_read_text(root / relative)
        if text.strip():
            samples[relative] = text
    if not samples:
        raise RuntimeError("None of the selected files could be read.")
    print_step(f"Loaded {len(samples)} file contents.")
    return samples


def trim_generation_context(context: dict[str, Any]) -> dict[str, Any]:
    print_step("Trimming generation context if needed.")
    encoded = json.dumps(context, ensure_ascii=False)
    if len(encoded) <= MAX_TOTAL_CONTEXT_CHARS:
        print_step(f"Generation context size is {len(encoded)} characters.")
        return context

    file_contents = context.get("selected_file_contents", {})
    for path in sorted(file_contents, key=lambda key: len(file_contents[key]), reverse=True):
        if len(json.dumps(context, ensure_ascii=False)) <= MAX_TOTAL_CONTEXT_CHARS:
            break
        original = file_contents[path]
        file_contents[path] = original[: max(1500, len(original) // 2)] + "\n... trimmed ..."

    print_step(f"Trimmed generation context size is {len(json.dumps(context, ensure_ascii=False))} characters.")
    return context


def normalize_relative_bru_path(path: str, output_subdir: str) -> str:
    normalized = path.strip().lstrip("/")
    output_prefix = output_subdir.strip("/").rstrip("/")
    if output_prefix and normalized.startswith(output_prefix + "/"):
        normalized = normalized[len(output_prefix) + 1 :]
    if normalized.startswith("generated/"):
        normalized = normalized[len("generated/") :]
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
    text = str(value).replace("\n", " ").strip()
    return text


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


def render_bru_request(
    request: dict[str, Any],
    seq: int,
) -> str:
    method = str(request.get("method", "get")).strip().lower()
    if method not in HTTP_METHODS:
        raise RuntimeError(f"Unsupported HTTP method for Bruno rendering: {method}")

    name = sanitize_meta_name(str(request.get("name", "Untitled")))
    url = str(request.get("url", "{{baseUrl}}/")).strip()
    if not url:
        raise RuntimeError("Generated request spec is missing a URL.")

    auth_mode = "inherit"
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
        f"  auth: {auth_mode}",
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


def generate_bru_request_specs(
    changelog_chunk: dict[str, str],
    selected_file_contents: dict[str, str],
    existing_bruno_files: dict[str, str],
    output_subdir: str,
    model: str,
) -> list[dict[str, Any]]:
    print_step("Asking OpenAI to generate structured Bruno request specs.")
    prompt = f"""
You are generating structured request specs for Bruno `.bru` files for a Django
REST API project.

Use the provided changelog summary and the selected source files to infer:
- which endpoints exist
- which endpoints deserve Bruno requests
- request methods
- JSON payloads
- auth token usage

Requirements:
- Generate file paths relative to `{output_subdir}` under `bruno-collection`.
- Use `{{{{baseUrl}}}}` for URLs.
- Use `{{{{authToken}}}}` for bearer auth when an endpoint requires authentication.
- Favor concrete payload examples derived from serializers/views/models.
- Do not invent endpoints that are not supported by the code.
- Do not return raw `.bru` text.
- Include a sensible mix of auth and app endpoints.
- Stick to HTTP methods Bruno can represent here: GET, POST, PUT, PATCH, DELETE,
  HEAD, OPTIONS.
- If a request has no JSON body, return `"json_body": null`.
- If a request needs an auth token, include the header
  `"Authorization": "Bearer {{{{authToken}}}}"`.
- For JSON requests, include `"Content-Type": "application/json"` in headers.

Return JSON only with this shape:
{{
  "requests": [
    {{
      "path": "auth/login.bru",
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

Latest changelog metadata:
{json.dumps(changelog_chunk, indent=2, ensure_ascii=False)}

Existing Bruno files:
{json.dumps(existing_bruno_files, indent=2, ensure_ascii=False)}

Selected file contents:
{json.dumps(selected_file_contents, indent=2, ensure_ascii=False)}
""".strip()
    response_text = call_openai(prompt, model)
    payload = extract_json_object(response_text)
    requests = payload.get("requests")
    if not isinstance(requests, list) or not requests:
        raise RuntimeError("Model did not return any request specs.")

    normalized_specs: list[dict[str, Any]] = []
    for item in requests:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str):
            continue
        normalized_path = normalize_relative_bru_path(path, output_subdir)
        if not normalized_path or not normalized_path.endswith(".bru"):
            continue
        if ".." in Path(normalized_path).parts:
            continue
        method = str(item.get("method", "")).strip().lower()
        if method not in HTTP_METHODS:
            continue
        spec = {
            "path": normalized_path,
            "name": item.get("name", Path(normalized_path).stem.replace("_", " ")),
            "method": method,
            "url": item.get("url", "{{baseUrl}}/"),
            "headers": item.get("headers", {}),
            "json_body": item.get("json_body"),
        }
        normalized_specs.append(spec)

    if not normalized_specs:
        raise RuntimeError("Model output did not contain any valid request specs.")

    print_step(f"Model generated {len(normalized_specs)} request specs.")
    return normalized_specs


def build_bru_files_from_specs(
    request_specs: list[dict[str, Any]],
) -> dict[str, str]:
    print_step("Rendering valid Bruno request files from structured specs.")
    rendered: dict[str, str] = {}
    for seq, spec in enumerate(request_specs, start=1):
        rendered[spec["path"]] = render_bru_request(spec, seq)
    print_step(f"Rendered {len(rendered)} Bruno files.")
    return rendered


def read_existing_bruno_context(root: Path) -> dict[str, str]:
    print_step("Reading existing Bruno collection context.")
    files = [
        "bruno-collection/bruno.json",
        "bruno-collection/environments/local.bru",
    ]
    context: dict[str, str] = {}
    for relative in files:
        text = safe_read_text(root / relative)
        if text.strip():
            context[relative] = text
    print_step(f"Loaded {len(context)} existing Bruno context files.")
    return context


def resolve_bruno_output_dir(root: Path, bruno_dir: str) -> tuple[Path, str]:
    target_root = (root / bruno_dir).resolve()
    bruno_root = (root / "bruno-collection").resolve()

    if target_root != bruno_root and bruno_root not in target_root.parents:
        raise RuntimeError("--bruno-dir must point inside bruno-collection.")

    relative_output = str(target_root.relative_to(bruno_root))
    return target_root, relative_output


def write_bru_files(target_root: Path, files: dict[str, str]) -> list[Path]:
    print_step(f"Writing Bruno files into {target_root}.")
    target_root.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for relative_path, content in files.items():
        full_path = (target_root / relative_path).resolve()
        if target_root not in full_path.parents:
            raise RuntimeError(f"Refusing to write outside target Bruno directory: {relative_path}")
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        written.append(full_path)

    print_step(f"Wrote {len(written)} Bruno files.")
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Bruno request files from the latest numbered CHANGELOG section."
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_PROJECT_ROOT),
        help="Repository root. Defaults to the parent of scripts/.",
    )
    parser.add_argument(
        "--changelog",
        default=DEFAULT_CHANGELOG,
        help="CHANGELOG file to parse. Defaults to CHANGELOG.md.",
    )
    parser.add_argument(
        "--bruno-dir",
        default=DEFAULT_BRUNO_DIR,
        help="Output directory relative to repo root. Defaults to bruno-collection/generated.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="OpenAI model to use. Defaults to OPENAI_MODEL or gpt-4.1-mini.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated file JSON instead of writing files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    changelog_path = root / args.changelog
    bruno_target_root, bruno_relative_output = resolve_bruno_output_dir(
        root, args.bruno_dir
    )

    print_step(f"Starting Bruno generation workflow in {root}.")
    latest_chunk = extract_latest_changelog_chunk(changelog_path)
    file_index = build_file_index(root)
    selected_files = choose_files_with_llm(latest_chunk, file_index, args.model)
    selected_file_contents = read_selected_files(root, selected_files)
    existing_bruno_files = read_existing_bruno_context(root)

    generation_context = {
        "latest_changelog_chunk": latest_chunk,
        "selected_file_contents": selected_file_contents,
        "existing_bruno_files": existing_bruno_files,
    }
    generation_context = trim_generation_context(generation_context)

    request_specs = generate_bru_request_specs(
        generation_context["latest_changelog_chunk"],
        generation_context["selected_file_contents"],
        generation_context["existing_bruno_files"],
        bruno_relative_output,
        args.model,
    )
    generated_files = build_bru_files_from_specs(request_specs)

    if args.dry_run:
        print_step("Dry run enabled; printing generated Bruno file payload.")
        print(json.dumps(generated_files, indent=2, ensure_ascii=False))
    else:
        written = write_bru_files(bruno_target_root, generated_files)
        print_step("Generated Bruno files:")
        for path in written:
            print_step(f"- {path.relative_to(root)}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print_step(f"ERROR: {exc}")
        raise SystemExit(1)
