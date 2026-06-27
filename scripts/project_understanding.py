#!/usr/bin/env python3
"""
AI-powered project understanding generator.

This script gathers a bounded amount of repository context, sends it to OpenAI,
and appends the model-generated project understanding summary to CHANGELOG.md.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from datetime import datetime

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime for user clarity
    OpenAI = None  # type: ignore[assignment]


DEFAULT_CHANGELOG = "CHANGELOG.md"
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROJECT_ROOT = SCRIPT_DIR.parent

MAX_TREE_DEPTH = 3
MAX_FILES_PER_DIR = 30
MAX_GIT_COMMITS = 30
MAX_CHANGED_FILES = 150
MAX_SAMPLED_FILES = 45
MAX_FILE_BYTES = 18_000
MAX_CONTEXT_CHARS = 100_000

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
    ".github",
    "bruno-collection",
}

SKIP_FILE_SUFFIXES = {
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

IMPORTANT_FILE_NAMES = {
    ".env.example",
    ".gitignore",
    "AGENTS.md",
    "Dockerfile",
    "README.md",
    "asgi.py",
    "bruno.json",
    "docker-compose.yml",
    "manage.py",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "settings.py",
    "urls.py",
    "wsgi.py",
}

IMPORTANT_SUFFIXES = {
    ".bru",
    ".cfg",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def print_step(message: str) -> None:
    print(f"[project-ai-summary] {message}", flush=True)


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

    return result.stdout.strip()


def should_skip(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    return path.suffix.lower() in SKIP_FILE_SUFFIXES


def safe_read_text(path: Path, max_bytes: int = MAX_FILE_BYTES) -> str:
    try:
        data = path.read_bytes()[:max_bytes]
    except OSError as exc:
        print_step(f"Could not read {path}: {exc}")
        return ""
    return data.decode("utf-8", errors="ignore")


def collect_directory_tree(root: Path) -> list[str]:
    print_step(
        f"Scanning directory tree with depth={MAX_TREE_DEPTH}, "
        f"max_files_per_dir={MAX_FILES_PER_DIR}."
    )
    lines: list[str] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > MAX_TREE_DEPTH:
            return

        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda item: (item.is_file(), item.name.lower()),
            )
        except OSError as exc:
            print_step(f"Could not list {directory}: {exc}")
            return

        visible_entries = [entry for entry in entries if not should_skip(entry)]
        files_seen = 0

        for entry in visible_entries:
            relative_path = entry.relative_to(root)
            indent = "  " * depth

            if entry.is_dir():
                lines.append(f"{indent}- {relative_path}/")
                walk(entry, depth + 1)
                continue

            files_seen += 1
            if files_seen <= MAX_FILES_PER_DIR:
                lines.append(f"{indent}- {relative_path}")
            elif files_seen == MAX_FILES_PER_DIR + 1:
                lines.append(f"{indent}- ... more files omitted")

    walk(root, 0)
    print_step(f"Collected {len(lines)} tree lines.")
    return lines


def collect_git_context(root: Path) -> dict[str, Any]:
    print_step("Collecting git commit messages and changed files.")
    commit_messages = run_command(
        ["git", "log", f"--max-count={MAX_GIT_COMMITS}", "--pretty=format:%h %s"],
        root,
    ).splitlines()

    historical_changes_raw = run_command(
        [
            "git",
            "log",
            f"--max-count={MAX_GIT_COMMITS}",
            "--name-status",
            "--pretty=format:",
        ],
        root,
    ).splitlines()
    working_tree_changes = run_command(["git", "status", "--short"], root).splitlines()

    historical_changes: list[str] = []
    for line in historical_changes_raw:
        clean = line.strip()
        if not clean:
            continue
        pieces = clean.split(maxsplit=1)
        if len(pieces) != 2:
            continue
        status, path_text = pieces
        path = Path(path_text.split("\t")[-1])
        if status[0] in {"A", "M", "R", "C"} and not should_skip(path):
            historical_changes.append(clean)

    print_step(
        f"Collected {len(commit_messages)} commits, "
        f"{len(historical_changes)} historical changes, "
        f"{len(working_tree_changes)} working-tree changes."
    )
    return {
        "recent_commit_messages": commit_messages[:MAX_GIT_COMMITS],
        "recent_added_modified_files": historical_changes[:MAX_CHANGED_FILES],
        "current_working_tree_changes": working_tree_changes[:MAX_CHANGED_FILES],
    }


def collect_candidate_files(root: Path) -> list[Path]:
    print_step("Finding important files to sample without walking huge folders deeply.")
    candidates: list[Path] = []

    for current_root, dir_names, file_names in os.walk(root):
        current_path = Path(current_root)
        relative_current = current_path.relative_to(root)
        depth = 0 if str(relative_current) == "." else len(relative_current.parts)

        dir_names[:] = [
            name
            for name in dir_names
            if name not in SKIP_DIRS
            and not should_skip(current_path / name)
            and depth < MAX_TREE_DEPTH + 1
        ]

        for file_name in sorted(file_names):
            path = current_path / file_name
            relative_path = path.relative_to(root)
            if should_skip(relative_path):
                continue

            is_important_name = file_name in IMPORTANT_FILE_NAMES
            is_important_suffix = path.suffix.lower() in IMPORTANT_SUFFIXES
            is_app_file = file_name in {
                "models.py",
                "serializers.py",
                "views.py",
                "auth_views.py",
                "admin.py",
            }

            if is_important_name or is_important_suffix or is_app_file:
                candidates.append(relative_path)

    unique_candidates = list(dict.fromkeys(candidates))[:MAX_SAMPLED_FILES]
    print_step(f"Selected {len(unique_candidates)} files for small content samples.")
    return unique_candidates


def collect_file_samples(root: Path, files: list[Path]) -> dict[str, str]:
    print_step(f"Reading bounded samples from {len(files)} files.")
    samples: dict[str, str] = {}
    for relative_path in files:
        text = safe_read_text(root / relative_path)
        if text.strip():
            samples[str(relative_path)] = text
    print_step(f"Collected {len(samples)} readable file samples.")
    return samples


def trim_context(context: dict[str, Any]) -> dict[str, Any]:
    print_step(f"Trimming context to about {MAX_CONTEXT_CHARS} characters.")
    encoded = json.dumps(context, indent=2, ensure_ascii=False)
    if len(encoded) <= MAX_CONTEXT_CHARS:
        print_step(f"Context size is {len(encoded)} characters; no trimming needed.")
        return context

    file_samples = context.get("file_samples", {})
    for path in sorted(
        file_samples,
        key=lambda key: len(file_samples[key]),
        reverse=True,
    ):
        if len(json.dumps(context, ensure_ascii=False)) <= MAX_CONTEXT_CHARS:
            break
        original = file_samples[path]
        shortened = original[: max(1_000, len(original) // 2)]
        file_samples[path] = shortened + "\n... sample trimmed ..."

    trimmed_size = len(json.dumps(context, ensure_ascii=False))
    print_step(f"Trimmed context size is {trimmed_size} characters.")
    return context


def build_prompt(context: dict[str, Any]) -> str:
    print_step("Building the OpenAI prompt.")
    return f"""
You are a senior software engineer analyzing a repository.

Use only the repository context below. Infer the project technology/stack by
combining:
- git commit messages
- files added or modified in git history
- current working-tree changes
- shallow directory structure
- sampled important files

Return Markdown only. Follow this exact shape:

# <Project title>

## 1. Initial Project Summary - [date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]:
## Technology Stack:
- <technology used>
- <technology used>

## Project directory:
.
├── file1
└── file2
└── folder
    ├── file3
    ├── ...

<technical summary, like which file is for what, what functionalities are there etc.>

Rules:
- Do not mention that context was truncated unless it materially affects confidence.
- Do not invent external services or features that are not supported by context.
- Keep the technology list concise but complete.
- The technical summary should explain important directories/files and core functionality.
- Do not wrap the whole answer in a code block.
- Keep the Project Directory tree concise, but include all important files and directories.

Repository context:
{json.dumps(context, indent=2, ensure_ascii=False)}
""".strip()


def call_openai(prompt: str, model: str) -> str:
    print_step(f"Calling OpenAI Responses API with model: {model}.")
    if OpenAI is None:
        raise RuntimeError(
            "The openai package is not installed. Install it with: pip install openai"
        )

    client = OpenAI()
    response = client.responses.create(
        model=model,
        input=prompt,
    )
    summary = response.output_text.strip()
    if not summary:
        raise RuntimeError("OpenAI returned an empty summary.")
    print_step(f"Received {len(summary)} characters from OpenAI.")
    return summary


def append_to_changelog(root: Path, changelog_name: str, summary: str) -> Path:
    changelog_path = root / changelog_name
    print_step(f"Appending AI-generated summary to {changelog_path}.")
    existing = safe_read_text(changelog_path) if changelog_path.exists() else ""
    separator = "\n\n" if existing.strip() else ""

    with changelog_path.open("a", encoding="utf-8") as file:
        file.write(separator)
        file.write(summary.rstrip())
        file.write("\n")

    print_step("CHANGELOG append complete.")
    return changelog_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use OpenAI to generate and append a project understanding summary."
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_PROJECT_ROOT),
        help="Repository root to analyze. Defaults to the parent of scripts/.",
    )
    parser.add_argument(
        "--changelog",
        default=DEFAULT_CHANGELOG,
        help=f"Changelog file to append to. Defaults to {DEFAULT_CHANGELOG}.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="OpenAI model to use. Defaults to OPENAI_MODEL or gpt-4.1-mini.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the AI summary instead of appending it to the changelog.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()

    print_step(f"Starting AI-powered repository analysis in {root}.")
    if not root.exists():
        print_step(f"Repository root does not exist: {root}")
        return 1

    context = {
        "project_directory_name": root.name,
        "directory_tree": collect_directory_tree(root),
        "git": collect_git_context(root),
    }
    sampled_files = collect_candidate_files(root)
    context["file_samples"] = collect_file_samples(root, sampled_files)
    context = trim_context(context)

    prompt = build_prompt(context)
    print_step(prompt)
    summary = call_openai(prompt, args.model)

    if args.dry_run:
        print_step("Dry run enabled; printing summary instead of writing CHANGELOG.")
        print(summary)
    else:
        changelog_path = append_to_changelog(root, args.changelog, summary)
        print_step(f"Done. Summary appended to {changelog_path}.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print_step(f"ERROR: {exc}")
        raise SystemExit(1)
