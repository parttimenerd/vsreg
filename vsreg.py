#! /usr/bin/python3

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

VSREG_FOLDER = Path(__file__).parent
CUR_FOLDER = Path.cwd()
VSCODE_FOLDER = CUR_FOLDER / ".vscode"

RR_DEFAULT_PORT = 50505


@dataclass
class LaunchConfig:
    data: Dict[str, Any]

    def name(self) -> str:
        return self.data["name"]


class LaunchConfigs:

    def __init__(self, data: Dict[str, Any]):
        self.data = data

    def write(self, path: Path):
        with path.open("w") as f:
            json.dump(self.data, f, indent=2)

    @staticmethod
    def read(path: Path) -> 'LaunchConfigs':
        with path.open("r") as f:
            return LaunchConfigs(json.loads(f.read()))

    @staticmethod
    def empty() -> 'LaunchConfigs':
        return LaunchConfigs({"version": "0.2.0", "configurations": []})

    def __contains__(self, config: LaunchConfig) -> bool:
        return any(c["name"] == config.name() for c in self.data["configurations"])

    # replaces if exists
    def add(self, config: LaunchConfig):
        if config not in self:
            self.data["configurations"].append(config.data)
        else:
            self.data["configurations"] = [config.data if c["name"] == config.name() else c for c in
                                           self.data["configurations"]]


class TaskConfigs:

    def __init__(self, data: Dict[str, Any]):
        self.data = data

    def write(self, path: Path):
        with path.open("w") as f:
            json.dump(self.data, f, indent=2)

    @staticmethod
    def read(path: Path) -> 'TaskConfigs':
        with path.open("r") as f:
            return TaskConfigs(json.loads(f.read()))

    @staticmethod
    def empty() -> 'TaskConfigs':
        return TaskConfigs({"version": "2.0.0", "tasks": []})

    def __contains__(self, label: str) -> bool:
        return any(t.get("label") == label for t in self.data["tasks"])

    def add(self, task: Dict[str, Any]):
        label = task["label"]
        if label not in self:
            self.data["tasks"].append(task)
        else:
            self.data["tasks"] = [task if t.get("label") == label else t for t in self.data["tasks"]]


class SettingsConfigs:

    def __init__(self, data: Dict[str, Any]):
        self.data = data

    def write(self, path: Path):
        with path.open("w") as f:
            json.dump(self.data, f, indent=2)

    @staticmethod
    def read(path: Path) -> 'SettingsConfigs':
        with path.open("r") as f:
            return SettingsConfigs(json.loads(f.read()))

    @staticmethod
    def empty() -> 'SettingsConfigs':
        return SettingsConfigs({})

    def set(self, key: str, value: Any):
        self.data[key] = value


def detect_clangd_platform(cwd: Path) -> str:
    """Return relative path like 'build/linux-x86_64-server-fastdebug'.

    Globs build/*/compile_commands.json under cwd; picks most-recently-modified.
    Raises FileNotFoundError if none found.
    """
    candidates = list(cwd.glob("build/*/compile_commands.json"))
    if not candidates:
        raise FileNotFoundError(f"No compile_commands.json found under {cwd}/build/*/")
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(newest.parent.relative_to(cwd))


@dataclass
class Parsed:
    cwd: str
    env: Dict[str, str]
    program: str
    args: List[str]


def run(label: str, launch_template: Path, task_template: Path, command: str, dry_run: bool, build_task: Optional[str]):
    pass


@dataclass
class CommandResult:
    stdout: str
    env: Dict[str, str]  # additional env vars


def run_command(command: List[str]) -> CommandResult:
    assert "make" in command, "make not found in command"
    env_vars = {parts[0]: parts[1] for env in command[0:command.index("make")] if len(parts := env.split("=", 2)) == 2}
    environ = dict(os.environ)
    environ.update(env_vars)
    result = subprocess.run(shlex.join(command), shell=True, env=environ, capture_output=True)
    out = result.stdout.decode("utf-8") + result.stderr.decode("utf-8")

    return CommandResult(out, env_vars)


def parse(command_out: CommandResult) -> Parsed:
    parts = command_out.stdout.split("rerun:\ncd")
    assert len(parts) >= 2, "Unexpected output format"
    parts = parts[-1].split("\n\n")
    assert len(parts) >= 2, "Unexpected output format"
    lines = parts[0].split("\n")
    assert lines[0].startswith(" /") and lines[0].endswith(" && \\"), "Unexpected output format: " + lines[0]
    cwd = lines[0][1:-5]
    assert Path(cwd).exists(), f"Invalid cwd: {cwd}"
    lines = lines[1:]
    env_lines_length = next(i for i, line in enumerate(lines) if line.startswith(" "))
    env_lines = lines[:env_lines_length]
    env: Dict[str, str] = {}
    for line in env_lines:
        line = line[:-2]
        assert re.match(r"^[A-Z_]+=.*$", line), f"Unexpected env line: {line}"
        key, value = line.split("=", 1)
        env[key] = value
    java_lines = lines[env_lines_length:]
    java = java_lines[0].strip()[:-2]
    assert java.endswith("java"), f"Unexpected java command: {java}"
    assert Path(java).exists(), f"Invalid java command: {java}"
    java_args = java_lines[1:]
    args: List[str] = []
    for arg in java_args[:-1]:
        arg = arg.strip()
        assert arg.endswith(" \\"), f"Unexpected java arg: {arg}"
        args.append(arg[:-2])
    args.extend(shlex.split(java_args[-1].strip()))

    return Parsed(cwd, {**env, **command_out.env}, java, args)


def load_template(template: str) -> Dict[str, Any]:
    file = Path(template if template.endswith(".json") else VSREG_FOLDER / "templates" / (template + ".json"))
    assert file.exists(), f"Template file does not exist: {file}"
    return json.loads(file.read_text())


def replace(obj: Any, token: str, replacement: str) -> Any:
    if isinstance(obj, str):
        return obj.replace(token, replacement)
    if isinstance(obj, list):
        return [replace(v, token, replacement) for v in obj]
    if isinstance(obj, dict):
        return {k: replace(v, token, replacement) for k, v in obj.items()}
    return obj


def create_launch_config(label: str, parsed: Parsed, template: str, build_task: Optional[str],
                         jtreg: bool = True, rr_port: Optional[int] = None) -> LaunchConfig:
    template_json = load_template(template)
    if "$NAME" in template_json["name"]:
        template_json["name"] = template_json["name"].replace("$NAME", label)
    else:
        template_json["name"] = label
    template_json = replace(template_json, "$NAME", label)
    template_json = replace(template_json, "$ARCH", platform.machine().lower())
    template_json = replace(template_json, "$VSREG_DIR", str(VSREG_FOLDER))
    if rr_port is not None:
        template_json = replace(template_json, "$RR_PORT", str(rr_port))
    template_json["cwd"] = parsed.cwd
    template_json["environment"] = [{"name": name, "value": value} for name, value in
                                    sorted(parsed.env.items(), key=lambda x: x[0])]
    template_json["program"] = parsed.program
    template_json["args"] = (["-XX:+UnlockDiagnosticVMOptions", "-XX:+WhiteBoxAPI"] if jtreg else []) + parsed.args
    if build_task:
        template_json["preLaunchTask"] = build_task
    return LaunchConfig(template_json)


def parse_raw_command(cmd: List[str]) -> Parsed:
    cwd = Path.cwd()
    split_idx = next((i for i, arg in enumerate(cmd) if not re.match(r"^[A-Z_]+=", arg)), len(cmd))
    env_args = cmd[:split_idx]
    program_args = cmd[split_idx:]
    env: Dict[str, str] = {parts[0]: shlex.split(parts[1])[0] for arg in env_args if
                           len(parts := arg.split("=", 2)) == 2}
    env.update(os.environ)
    program, *args = program_args
    if not program.startswith("/") and not program.startswith("."):
        program = shutil.which(program, path=env["PATH"])
    return Parsed(str(cwd), env, program, args)


def create_raw_launch_config(label: str, cmd: List[str], template: str, build_task: Optional[str],
                             rr_port: Optional[int] = None) -> LaunchConfig:
    parsed = parse_raw_command(cmd)
    return create_launch_config(label, parsed, template, build_task, jtreg=False, rr_port=rr_port)


def rr_replay_task(label: str, port: int) -> Dict[str, Any]:
    return {
        "label": label,
        "type": "shell",
        "command": f"rr replay -s {port} -k",
        "isBackground": True,
        "problemMatcher": {
            "pattern": {"regexp": "^$"},
            "background": {
                "activeOnStart": True,
                "beginsPattern": ".",
                "endsPattern": "^\\[rr\\]",
            },
        },
    }


def build_command_task(label: str, command: str) -> Dict[str, Any]:
    parts = shlex.split(command)
    program, *task_args = parts
    return {
        "label": label,
        "type": "shell",
        "command": program,
        "args": task_args,
        "problemMatcher": ["$gcc"],
    }


@dataclass
class RemoteConfig:
    launch: LaunchConfig
    tasks: List[Dict[str, Any]]
    settings: Optional[Dict[str, Any]]  # extra settings.json keys, or None


def ssh_tunnel_task(label: str, host: str, port: int, user: str) -> Dict[str, Any]:
    return {
        "label": label,
        "type": "shell",
        "command": f"ssh -L {port}:localhost:{port} {user}@{host} -N",
        "isBackground": True,
        "problemMatcher": {
            "pattern": {"regexp": "^$"},
            "background": {
                "activeOnStart": True,
                "beginsPattern": ".",
                "endsPattern": "^$",
            },
        },
    }


def create_remote_launch_config(
    label: str,
    host: str,
    port: int,
    remote_path: str,
    flavor: str,
    user: Optional[str],
    build_task: Optional[str],
) -> RemoteConfig:
    tasks: List[Dict[str, Any]] = []
    extra_settings: Optional[Dict[str, Any]] = None

    if flavor == "vscode-remote":
        template_json = load_template("default")
        template_json["name"] = label
        template_json = replace(template_json, "$NAME", label)
        template_json = replace(template_json, "$ARCH", platform.machine().lower())
        template_json = replace(template_json, "$VSREG_DIR", str(VSREG_FOLDER))
        template_json["program"] = remote_path
        template_json["cwd"] = str(Path(remote_path).parent)
        template_json["args"] = []
        template_json["environment"] = []
        if build_task:
            template_json["preLaunchTask"] = build_task
        extra_settings = {"remote.SSH.serverInstallPath": {host: "/tmp/vscode-server"}}
        return RemoteConfig(LaunchConfig(template_json), tasks, extra_settings)

    # gdbserver or ssh: both use remote.json template
    template_json = load_template("remote")
    template_json["name"] = label
    template_json = replace(template_json, "$NAME", label)
    template_json = replace(template_json, "$ARCH", platform.machine().lower())
    template_json = replace(template_json, "$VSREG_DIR", str(VSREG_FOLDER))

    if flavor == "ssh":
        # GDB connects to localhost; SSH tunnel forwards the port
        template_json = replace(template_json, "$REMOTE_HOST", "localhost")
        template_json = replace(template_json, "$REMOTE_PORT", str(port))
        tunnel_label = f"ssh tunnel ({label})"
        tasks.append(ssh_tunnel_task(tunnel_label, host, port, user or ""))
        if not build_task:
            template_json["preLaunchTask"] = tunnel_label
    else:
        # gdbserver: GDB connects directly to remote host
        template_json = replace(template_json, "$REMOTE_HOST", host)
        template_json = replace(template_json, "$REMOTE_PORT", str(port))

    template_json["program"] = remote_path
    template_json["cwd"] = str(Path(remote_path).parent)
    template_json["args"] = []
    template_json["environment"] = []
    if build_task:
        template_json["preLaunchTask"] = build_task

    return RemoteConfig(LaunchConfig(template_json), tasks, extra_settings)


if __name__ == '__main__':
    # source https://groups.google.com/g/argparse-users/c/LazV_tEQvQw/m/xJhBOm1qS5IJ
    class MyParser(argparse.ArgumentParser):
        def error(self, message):
            sys.stderr.write('error: %s\n' % message)
            self.print_help()
            sys.exit(2)


    parser = MyParser(description='Create a debug launch config for a JTREG test run or a command execution')
    parser.add_argument('label', metavar='LABEL', type=str, nargs='?', default=None,
                        help='Label of the config (optional when --clangd used alone)')
    parser.add_argument('-t', '--template', metavar='TEMPLATE', type=str,
                        help='Template to use for the launch config, or name of file without suffix in vsreg/template folder',
                        required=False, default='default')
    parser.add_argument('-d', '--dry-run', action='store_true', help='Only print the launch config', required=False)
    parser.add_argument('-r', '--raw', action='store_true',
                        help='Use raw command without execution, chosen automatically if "make" not found in command',
                        required=False)
    parser.add_argument('-b', '--build-task', metavar='TASK', type=str, help='Task to run before the command',
                        required=False)
    parser.add_argument('--build-command', metavar='CMD', type=str,
                        help='Shell command for a build task; creates a tasks.json entry and sets it as preLaunchTask',
                        required=False)
    parser.add_argument('--rr', metavar='PORT', nargs='?', const=RR_DEFAULT_PORT, type=int,
                        help=f'Use rr replay; optionally specify port (default {RR_DEFAULT_PORT}). '
                             'Sets template to rr and creates a preLaunchTask that starts rr replay.',
                        required=False)
    parser.add_argument('--clangd', action='store_true',
                        help='Write clangd.arguments into .vscode/settings.json',
                        required=False)
    parser.add_argument('--platform', metavar='PLATFORM', type=str,
                        help='Platform dir name (e.g. linux-x86_64-server-fastdebug); '
                             'auto-detected from build/*/compile_commands.json if omitted',
                        required=False)
    parser.add_argument('command', metavar='COMMAND', type=str, nargs='*', help='Command to run')
    args = parser.parse_args()

    if not args.clangd and (args.label is None or not args.command):
        parser.error("LABEL and COMMAND are required unless --clangd is used alone")

    # --- clangd settings ---
    clangd_settings: Optional[Dict[str, Any]] = None
    if args.clangd:
        if args.platform:
            platform_dir = f"build/{args.platform}"
        else:
            try:
                platform_dir = detect_clangd_platform(CUR_FOLDER)
            except FileNotFoundError as e:
                sys.stderr.write(f"error: {e}\n")
                sys.exit(1)
        clangd_settings = {"clangd.arguments": [f"--compile-commands-dir={platform_dir}"]}

    # --- launch config (only when LABEL+COMMAND present) ---
    launch_config = None
    if args.label and args.command:
        build_task = args.build_task
        rr_port = args.rr

        if rr_port is not None and args.template == 'default':
            args.template = 'rr'

        if args.raw or "make" not in args.command:
            launch_config = create_raw_launch_config(args.label, args.command, args.template, build_task, rr_port=rr_port)
        else:
            parsed = parse(run_command(args.command))
            launch_config = create_launch_config(args.label, parsed, args.template, build_task, rr_port=rr_port)

    if args.dry_run:
        if clangd_settings:
            print(json.dumps(clangd_settings, indent=2))
        if launch_config:
            print(json.dumps(launch_config.data, indent=2))
    else:
        if not VSCODE_FOLDER.exists():
            VSCODE_FOLDER.mkdir(parents=True)

        if clangd_settings:
            settings_file = VSCODE_FOLDER / "settings.json"
            settings = SettingsConfigs.read(settings_file) if settings_file.exists() else SettingsConfigs.empty()
            for key, value in clangd_settings.items():
                settings.set(key, value)
            settings.write(settings_file)

        if launch_config:
            build_task = args.build_task
            rr_port = args.rr

            tasks_to_add: List[Dict[str, Any]] = []
            if args.build_command:
                bc_label = f"Build ({args.label})"
                tasks_to_add.append(build_command_task(bc_label, args.build_command))
                if not build_task:
                    build_task = bc_label
                    launch_config.data["preLaunchTask"] = build_task
            if rr_port is not None:
                rr_label = f"rr replay ({args.label})"
                tasks_to_add.append(rr_replay_task(rr_label, rr_port))
                if not launch_config.data.get("preLaunchTask"):
                    launch_config.data["preLaunchTask"] = rr_label

            if tasks_to_add:
                tasks_file = VSCODE_FOLDER / "tasks.json"
                tasks = TaskConfigs.read(tasks_file) if tasks_file.exists() else TaskConfigs.empty()
                for t in tasks_to_add:
                    tasks.add(t)
                tasks.write(tasks_file)

            file = VSCODE_FOLDER / "launch.json"
            launch = LaunchConfigs.read(file) if file.exists() else LaunchConfigs.empty()
            if launch_config in launch:
                print(f"Replacing launch config {launch_config.name()}")
            else:
                print(f"Adding launch config {launch_config.name()}")
            launch.add(launch_config)
            launch.write(file)
