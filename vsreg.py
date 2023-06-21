#! /usr/bin/python3

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from pprint import pprint
from typing import Any, Dict, List, Optional

VSREG_FOLDER = Path(__file__).parent
CUR_FOLDER = Path.cwd()
VSCODE_FOLDER = CUR_FOLDER / ".vscode"


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
    out = subprocess.run(shlex.join(command), shell=True, env=environ, capture_output=True).stdout.decode("utf-8")

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
        assert re.match(r"^[A-Z_]+=.+$", line), f"Unexpected env line: {line}"
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


def create_launch_config(label: str, parsed: Parsed, template: str, build_task: Optional[str]) -> LaunchConfig:
    template_json = load_template(template)
    if "$NAME" in template_json["name"]:
        template_json["name"] = template_json["name"].replace("$NAME", label)
    else:
        template_json["name"] = label
    template_json["cwd"] = parsed.cwd
    template_json["environment"] = [{"name": name, "value": value} for name, value in
                                    sorted(parsed.env.items(), key=lambda x: x[0])]
    template_json["program"] = parsed.program
    template_json["args"] = ["-XX:+UnlockDiagnosticVMOptions", "-XX:+WhiteBoxAPI"] + parsed.args
    if build_task:
        template_json["preLaunchTask"] = build_task
    return LaunchConfig(template_json)


if __name__ == '__main__':
    # source https://groups.google.com/g/argparse-users/c/LazV_tEQvQw/m/xJhBOm1qS5IJ
    class MyParser(argparse.ArgumentParser):
        def error(self, message):
            sys.stderr.write('error: %s\n' % message)
            self.print_help()
            sys.exit(2)


    parser = MyParser(description='Create a debug launch config for a JTREG test run')
    parser.add_argument('label', metavar='LABEL', type=str, help='Label of the config')
    parser.add_argument('-t', '--template', metavar='TEMPLATE', type=str,
                        help='Template to use for the launch config, or name of file without suffix in vsreg/template folder',
                        required=False, default='gdb')
    parser.add_argument('-d', '--dry-run', action='store_true', help='Only print the launch config', required=False)
    parser.add_argument('-b', '--build-task', metavar='TASK', type=str, help='Task to run before the command',
                        required=False)
    parser.add_argument('command', metavar='COMMAND', type=str, nargs='+', help='Command to run')
    args = parser.parse_args()
    parsed = parse(run_command(args.command))
    launch_config = create_launch_config(args.label, parsed, args.template, args.build_task)
    if args.dry_run:
        print(json.dumps(launch_config.data, indent=2))
    else:
        if not VSCODE_FOLDER.exists():
            VSCODE_FOLDER.mkdir(parents=True)
        file = VSCODE_FOLDER / "launch.json"
        launch = LaunchConfigs.read(file) if file.exists() else LaunchConfigs.empty()
        if launch_config in launch:
            print(f"Replacing launch config {launch_config.name()}")
        else:
            print(f"Adding launch config {launch_config.name()}")
        launch.add(launch_config)
        launch.write(file)
