import os
import subprocess
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


def _resolve_safe_path(relative_path: str) -> Path:
    target = (WORKSPACE_ROOT / relative_path).resolve()
    if WORKSPACE_ROOT not in target.parents and target != WORKSPACE_ROOT:
        raise ValueError(f"Access denied outside workspace: {relative_path}")
    return target


def create_or_update_file(relative_path: str, content: str) -> str:
    """Create a new file or overwrite an existing file inside the workspace."""
    try:
        target = _resolve_safe_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} characters to '{relative_path}'."
    except Exception as e:
        return f"Failed to write '{relative_path}': {e}"


def read_file(relative_path: str) -> str:
    """Read and return the contents of a file inside the workspace."""
    try:
        target = _resolve_safe_path(relative_path)
        if not target.exists():
            return f"File '{relative_path}' does not exist."
        return target.read_text(encoding="utf-8")
    except Exception as e:
        return f"Failed to read '{relative_path}': {e}"


def run_shell_command(command: str) -> str:
    """Run a shell command inside the workspace and return stdout/stderr."""
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (completed.stdout or "") + (
            f"\n[STDERR]\n{completed.stderr}" if completed.stderr else ""
        )
        output = output.strip() or "(No output)"
        return f"Exit code {completed.returncode}:\n{output[:2000]}"
    except Exception as e:
        return f"Command failed: {e}"


def open_application(app_name_or_path: str) -> str:
    """Launch an application or open a file/URL on Windows."""
    try:
        if os.name == "nt":
            os.startfile(app_name_or_path)
        else:
            subprocess.Popen([app_name_or_path])
        return f"Launched '{app_name_or_path}'."
    except Exception as e:
        return f"Could not launch '{app_name_or_path}': {e}"