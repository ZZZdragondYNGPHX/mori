"""Headless integration utilities. None of these commands imports Qt."""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .adapters.codex import HOOK_TYPES, CodexAdapter
from .client import send_event
from .events import CompanionEvent, EventType
from .paths import state_dir
from .transport import atomic_json


def hooks_config():
    launcher = str(Path(__file__).resolve().parents[1] / "companionctl.py")
    command = shlex.join([sys.executable, launcher, "codex-hook"])
    windows = subprocess.list2cmdline([sys.executable, launcher, "codex-hook"])
    return {"hooks": {
        name: [{"hooks": [{"type": "command", "command": command,
                           "commandWindows": windows, "timeout": 1}]}]
        for name in HOOK_TYPES
    }}


def install_codex_hooks(destination):
    """Merge only our handlers; retain every existing user's hook and metadata."""
    path = Path(destination).expanduser().resolve()
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(existing, dict) or not isinstance(existing.get("hooks", {}), dict):
        raise ValueError("Existing hooks.json must have an object-shaped hooks field")
    updated = json.loads(json.dumps(existing))
    hooks = updated.setdefault("hooks", {})
    for name, groups in hooks_config()["hooks"].items():
        current = hooks.setdefault(name, [])
        if not isinstance(current, list):
            raise ValueError(f"Invalid existing {name} hooks")
        # Updating/reinstalling removes only this utility's own old command.
        retained = []
        for group in current:
            if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
                raise ValueError(f"Invalid existing {name} hook group")
            copied = dict(group)
            copied["hooks"] = [handler for handler in group.get("hooks", [])
                               if not _is_companion_handler(handler)]
            if copied["hooks"] or "hooks" not in group:
                retained.append(copied)
        hooks[name] = retained + groups
    if updated == existing:
        return None
    backup = None
    if path.exists():
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(path.name + ".backup-" + suffix)
        shutil.copy2(path, backup)
    atomic_json(path, updated)
    return backup


def _is_companion_handler(handler):
    if not isinstance(handler, dict):
        return False
    # Match our concrete launcher path/token, never a generic 'codex-hook' word.
    command = handler.get("command", "")
    try:
        tokens = shlex.split(command, posix=sys.platform != "win32")
    except (ValueError, TypeError):
        return False
    return (len(tokens) == 3 and Path(tokens[1].strip('"')).name == "companionctl.py"
            and tokens[2] == "codex-hook")


def codex_hook(descriptor=None):
    """Best-effort, under 350ms IPC; never block or steer the agent's work."""
    if os.environ.get("MORI_CODEX_CLI") != "1" or os.environ.get("MORI_CODEX_STREAM") == "1":
        print("{}")
        return 0
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) <= 1024 * 1024:
            event = CodexAdapter().normalize(json.loads(raw))
            if event is not None:
                send_event(event.to_dict(), descriptor_path=descriptor)
    except Exception:
        # Desktop closed, bridge full, malformed hook: Codex continues normally.
        pass
    # Stop/Interrupt hooks require JSON; this adds no decisions or model context.
    print("{}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Mori Companion integration utilities")
    commands = parser.add_subparsers(dest="command", required=True)
    hook = commands.add_parser("codex-hook", help="Receive official Codex hook JSON on stdin")
    hook.add_argument("--descriptor", type=Path)
    install = commands.add_parser("install-codex-hooks", help="Merge companion hooks with backup")
    install.add_argument("--output", type=Path,
                         default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "hooks.json")
    commands.add_parser("print-codex-hooks", help="Print configuration without installing")
    shell = commands.add_parser("enable-codex", help="Connect ordinary interactive codex to Mori")
    shell.add_argument("--shell", choices=("fish", "bash", "zsh"),
                       help="Default: your login shell")
    event = commands.add_parser("emit", help="Emit a provider-neutral event for testing or plugins")
    event.add_argument("type", choices=[item.value for item in EventType])
    event.add_argument("--source", default="demo")
    event.add_argument("--session", default="demo-session")
    event.add_argument("--task", default="")
    event.add_argument("--summary", default="")
    event.add_argument("--descriptor", type=Path)
    commands.add_parser("paths", help="Print the data and bridge paths (never the token)")
    args = parser.parse_args(argv)
    if args.command == "codex-hook":
        return codex_hook(args.descriptor)
    try:
        if args.command == "install-codex-hooks":
            backup = install_codex_hooks(args.output)
            print(f"Hooks configured: {args.output}")
            if backup:
                print(f"Previous configuration backed up: {backup}")
            print("Restart Codex and review/trust these command hooks in /hooks.")
        elif args.command == "print-codex-hooks":
            print(json.dumps(hooks_config(), ensure_ascii=False, indent=2))
        elif args.command == "enable-codex":
            from .adapters.shell_integration import install_shell

            path = install_shell(args.shell)
            print(f"Interactive codex monitoring configured: {path}")
            print("Open a new terminal, run codex, then review/trust Mori hooks in /hooks.")
        elif args.command == "paths":
            print(f"Data: {state_dir()}\nBridge descriptor: {state_dir() / 'bridge.json'}")
        elif args.command == "emit":
            event = CompanionEvent(source=args.source, session_id=args.session,
                                   type=args.type, task_id=args.task,
                                   summary=args.summary)
            print(json.dumps(send_event(event.to_dict(), descriptor_path=args.descriptor)))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Companion: {error}", file=sys.stderr)
        return 1
    return 0
