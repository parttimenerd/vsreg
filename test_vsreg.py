import json
import os
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))

from vsreg import (
    CommandResult,
    LaunchConfig,
    LaunchConfigs,
    Parsed,
    create_launch_config,
    create_raw_launch_config,
    load_template,
    parse,
    parse_raw_command,
    replace,
    run_command,
)

VSREG = Path(__file__).parent / "vsreg.py"
JAVA_BIN = shutil.which("java") or "/usr/lib/jvm/java-21/bin/java"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_jtreg_output(cwd: str, java: str, env: dict, args: list[str]) -> str:
    """Build a minimal jtreg rerun block as emitted to stderr by make test."""
    env_block = "".join(f"{k}={v} \\\n" for k, v in env.items())
    args_block = "".join(f" {a} \\\n" for a in args[:-1])
    args_block += f" {args[-1]}\n" if args else ""
    return (
        "Test results: passed: 1\n"
        "rerun:\n"
        f"cd {cwd} && \\\n"
        f"{env_block}"
        f" {java} \\\n"
        f"{args_block}"
        "\n"
        "Finished running tests\n"
    )


class TempWorkspace:
    """Context manager: temp dir whose .vscode/ we inspect after running vsreg."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp())
        return self

    def __exit__(self, *_):
        shutil.rmtree(self.dir, ignore_errors=True)

    @property
    def launch_json(self) -> Path:
        return self.dir / ".vscode" / "launch.json"

    def read_launch(self) -> dict:
        return json.loads(self.launch_json.read_text())

    def configs(self) -> list[dict]:
        return self.read_launch()["configurations"]

    def write_fake_make(self, stderr_output: str) -> Path:
        """Write a 'make' script that prints stderr_output to stderr and exits 0.

        The script is placed in a bin/ subdirectory so that run_vsreg() can
        prepend it to PATH, satisfying vsreg's "make" in command check.
        """
        bin_dir = self.dir / "bin"
        bin_dir.mkdir(exist_ok=True)
        script = bin_dir / "make"
        script.write_text(f'#!/bin/sh\ncat >&2 <<\'EOF\'\n{stderr_output}EOF\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return bin_dir

    def run_vsreg(self, *args: str, fake_make_bin: Path | None = None, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        if fake_make_bin:
            env["PATH"] = str(fake_make_bin) + os.pathsep + env.get("PATH", "")
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(VSREG), *args],
            capture_output=True,
            text=True,
            cwd=self.dir,
            env=env,
        )


# ---------------------------------------------------------------------------
# parse()
# ---------------------------------------------------------------------------

class TestParse(unittest.TestCase):

    def _parsed(self, cwd, java, env, args):
        output = make_jtreg_output(cwd, java, env, args)
        return parse(CommandResult(stdout=output, env={}))

    def test_basic(self):
        cwd = str(Path.cwd())
        p = self._parsed(cwd, JAVA_BIN, {"DISPLAY": ":0", "TEST_FLAG": "1"}, ["-cp", "test.jar", "com.example.Test"])
        self.assertEqual(p.cwd, cwd)
        self.assertEqual(p.program, JAVA_BIN)
        self.assertEqual(p.env["DISPLAY"], ":0")
        self.assertEqual(p.env["TEST_FLAG"], "1")
        self.assertEqual(p.args, ["-cp", "test.jar", "com.example.Test"])

    def test_no_env(self):
        cwd = str(Path.cwd())
        p = self._parsed(cwd, JAVA_BIN, {}, ["-version"])
        self.assertEqual(p.program, JAVA_BIN)
        self.assertEqual(p.args, ["-version"])

    def test_extra_command_env_merged(self):
        output = make_jtreg_output(str(Path.cwd()), JAVA_BIN, {}, ["-version"])
        p = parse(CommandResult(stdout=output, env={"MY_VAR": "hello"}))
        self.assertEqual(p.env["MY_VAR"], "hello")

    def test_command_env_overrides_rerun_env(self):
        """Env vars from the make command line take priority over those in the rerun block."""
        output = make_jtreg_output(str(Path.cwd()), JAVA_BIN, {"OVERRIDE": "from_rerun"}, ["-version"])
        p = parse(CommandResult(stdout=output, env={"OVERRIDE": "from_command"}))
        self.assertEqual(p.env["OVERRIDE"], "from_command")

    def test_missing_rerun_raises(self):
        with self.assertRaises(AssertionError):
            parse(CommandResult(stdout="no rerun block here", env={}))

    def test_takes_last_rerun_block(self):
        cwd = str(Path.cwd())
        first = make_jtreg_output(cwd, JAVA_BIN, {"A": "1"}, ["-version"])
        second = make_jtreg_output(cwd, JAVA_BIN, {"A": "2"}, ["-ea"])
        p = parse(CommandResult(stdout=first + second, env={}))
        self.assertEqual(p.args, ["-ea"])

    def test_env_value_with_equals(self):
        """Env values that themselves contain '=' must be preserved whole."""
        output = make_jtreg_output(str(Path.cwd()), JAVA_BIN, {"OPTS": "-Da=b=c"}, ["-version"])
        p = parse(CommandResult(stdout=output, env={}))
        self.assertEqual(p.env["OPTS"], "-Da=b=c")

    def test_multi_word_final_arg_via_shlex(self):
        """Last arg line is parsed with shlex, so quoted spaces work."""
        output = make_jtreg_output(str(Path.cwd()), JAVA_BIN, {}, ['-Dfoo=bar baz'])
        p = parse(CommandResult(stdout=output, env={}))
        self.assertIn("-Dfoo=bar", p.args[0])

    def test_invalid_cwd_raises(self):
        output = make_jtreg_output("/nonexistent/path/xyz", JAVA_BIN, {}, ["-version"])
        with self.assertRaises((AssertionError, StopIteration)):
            parse(CommandResult(stdout=output, env={}))


# ---------------------------------------------------------------------------
# replace()
# ---------------------------------------------------------------------------

class TestReplace(unittest.TestCase):

    def test_string(self):
        self.assertEqual(replace("hello $NAME", "$NAME", "world"), "hello world")

    def test_list(self):
        self.assertEqual(replace(["$NAME", "ok"], "$NAME", "x"), ["x", "ok"])

    def test_dict(self):
        self.assertEqual(replace({"a": "$NAME"}, "$NAME", "v"), {"a": "v"})

    def test_nested(self):
        obj = {"outer": {"inner": ["$NAME", "$NAME"]}}
        result = replace(obj, "$NAME", "z")
        self.assertEqual(result["outer"]["inner"], ["z", "z"])

    def test_non_string_passthrough(self):
        self.assertEqual(replace(42, "$X", "y"), 42)
        self.assertIs(replace(None, "$X", "y"), None)

    def test_no_match_unchanged(self):
        self.assertEqual(replace("hello", "$NONE", "x"), "hello")


# ---------------------------------------------------------------------------
# load_template()
# ---------------------------------------------------------------------------

class TestLoadTemplate(unittest.TestCase):

    def test_builtin_default(self):
        tmpl = load_template("default")

    def test_default_template_has_sigsegv_gdb(self):
        tmpl = load_template("default")
        linux_cmds = [c["text"] for c in tmpl["linux"]["setupCommands"]]
        self.assertTrue(any("jvm_sigsegv.py" in cmd for cmd in linux_cmds))

    def test_default_template_has_sigsegv_lldb_osx(self):
        tmpl = load_template("default")
        osx_cmds = [c["text"] for c in tmpl["osx"]["setupCommands"]]
        self.assertIn("process handle SIGSEGV -n false -p true -s false", osx_cmds)

    def test_default_template_has_sigsegv_windows_gdb(self):
        tmpl = load_template("default")
        win_cmds = [c["text"] for c in tmpl["windows"]["setupCommands"]]
        self.assertTrue(any("jvm_sigsegv.py" in cmd for cmd in win_cmds))

    def test_lldb_only_template_has_sigsegv(self):
        tmpl = load_template("lldb_only")
        cmds = [c["text"] for c in tmpl["setupCommands"]]
        self.assertIn("process handle SIGSEGV -n false -p true -s false", cmds)

    def test_vsreg_dir_token_substituted(self):
        from vsreg import Parsed, VSREG_FOLDER
        parsed = Parsed(cwd=str(Path.cwd()), env={}, program=JAVA_BIN, args=[])
        cfg = create_launch_config("x", parsed, "default", None)
        cfg_str = json.dumps(cfg.data)
        self.assertNotIn("$VSREG_DIR", cfg_str)
        self.assertIn(str(VSREG_FOLDER), cfg_str)

    def test_builtin_lldb_only(self):
        tmpl = load_template("lldb_only")
        self.assertEqual(tmpl.get("MIMode"), "lldb")

    def test_missing_raises(self):
        with self.assertRaises(AssertionError):
            load_template("does_not_exist_at_all")

    def test_custom_json_path(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump({"name": "$NAME", "custom": True}, f)
            path = f.name
        try:
            tmpl = load_template(path)
            self.assertTrue(tmpl["custom"])
        finally:
            Path(path).unlink()


# ---------------------------------------------------------------------------
# run_command()
# ---------------------------------------------------------------------------

class TestRunCommand(unittest.TestCase):

    def test_env_vars_extracted(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"", stderr=b"")
            result = run_command(["FOO=bar", "BAZ=qux", "make", "test"])
        self.assertEqual(result.env["FOO"], "bar")
        self.assertEqual(result.env["BAZ"], "qux")

    def test_env_vars_not_in_env_after_make(self):
        """Only tokens before 'make' are treated as env vars."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"", stderr=b"")
            result = run_command(["make", "TEST=something"])
        self.assertNotIn("TEST", result.env)

    def test_stdout_and_stderr_combined(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"from stdout\n", stderr=b"from stderr\n")
            result = run_command(["make", "test"])
        self.assertIn("from stdout", result.stdout)
        self.assertIn("from stderr", result.stdout)

    def test_no_make_raises(self):
        with self.assertRaises(AssertionError):
            run_command(["echo", "hello"])


# ---------------------------------------------------------------------------
# parse_raw_command()
# ---------------------------------------------------------------------------

class TestParseRawCommand(unittest.TestCase):

    def test_simple_command_absolute(self):
        python = shutil.which("python3") or "/usr/bin/python3"
        p = parse_raw_command([python, "-c", "pass"])
        self.assertEqual(p.program, python)
        self.assertEqual(p.args, ["-c", "pass"])

    def test_env_prefix_extracted(self):
        python = shutil.which("python3") or "/usr/bin/python3"
        p = parse_raw_command(["MY_VAR=hello", python, "--version"])
        self.assertEqual(p.env.get("MY_VAR"), "hello")

    def test_command_in_path_resolved(self):
        p = parse_raw_command(["python3", "--version"])
        self.assertTrue(Path(p.program).is_absolute())

    def test_absolute_path_not_re_resolved(self):
        python = shutil.which("python3") or "/usr/bin/python3"
        p = parse_raw_command([python])
        self.assertEqual(p.program, python)

    def test_cwd_is_current(self):
        python = shutil.which("python3") or "/usr/bin/python3"
        p = parse_raw_command([python])
        self.assertEqual(p.cwd, str(Path.cwd()))

    def test_env_includes_os_environ(self):
        python = shutil.which("python3") or "/usr/bin/python3"
        p = parse_raw_command([python])
        self.assertIn("PATH", p.env)


# ---------------------------------------------------------------------------
# create_launch_config()
# ---------------------------------------------------------------------------

class TestCreateLaunchConfig(unittest.TestCase):

    def setUp(self):
        self.parsed = Parsed(
            cwd=str(Path.cwd()),
            env={"MY_ENV": "value"},
            program=JAVA_BIN,
            args=["-cp", "test.jar", "Main"],
        )

    def test_name_substituted(self):
        cfg = create_launch_config("My Test", self.parsed, "default", None)
        self.assertEqual(cfg.name(), "My Test")

    def test_program_set(self):
        cfg = create_launch_config("x", self.parsed, "default", None)
        self.assertEqual(cfg.data["program"], JAVA_BIN)

    def test_cwd_set(self):
        cfg = create_launch_config("x", self.parsed, "default", None)
        self.assertEqual(cfg.data["cwd"], str(Path.cwd()))

    def test_environment_set(self):
        cfg = create_launch_config("x", self.parsed, "default", None)
        env_map = {e["name"]: e["value"] for e in cfg.data["environment"]}
        self.assertEqual(env_map["MY_ENV"], "value")

    def test_environment_sorted_by_name(self):
        parsed = Parsed(str(Path.cwd()), {"ZZZ": "1", "AAA": "2"}, JAVA_BIN, [])
        cfg = create_launch_config("x", parsed, "default", None)
        names = [e["name"] for e in cfg.data["environment"]]
        self.assertEqual(names, sorted(names))

    def test_prelaunchtask_set(self):
        cfg = create_launch_config("x", self.parsed, "default", "build")
        self.assertEqual(cfg.data["preLaunchTask"], "build")

    def test_prelaunchtask_empty_when_none(self):
        cfg = create_launch_config("x", self.parsed, "default", None)
        self.assertEqual(cfg.data.get("preLaunchTask", ""), "")

    def test_jtreg_whitebox_args_prepended(self):
        cfg = create_launch_config("x", self.parsed, "default", None, jtreg=True)
        self.assertEqual(cfg.data["args"][0], "-XX:+UnlockDiagnosticVMOptions")
        self.assertEqual(cfg.data["args"][1], "-XX:+WhiteBoxAPI")

    def test_no_jtreg_args_when_disabled(self):
        cfg = create_launch_config("x", self.parsed, "default", None, jtreg=False)
        self.assertNotIn("-XX:+WhiteBoxAPI", cfg.data["args"])

    def test_arch_token_replaced(self):
        cfg = create_launch_config("x", self.parsed, "default", None)
        cfg_str = json.dumps(cfg.data)
        self.assertNotIn("$ARCH", cfg_str)
        self.assertIn(platform.machine().lower(), cfg_str)

    def test_name_token_replaced_in_body(self):
        """$NAME tokens elsewhere in the template (not just the name field) are replaced."""
        cfg = create_launch_config("MyLabel", self.parsed, "default", None)
        cfg_str = json.dumps(cfg.data)
        self.assertNotIn("$NAME", cfg_str)

    def test_lldb_template(self):
        cfg = create_launch_config("x", self.parsed, "lldb_only", None)
        self.assertEqual(cfg.data.get("MIMode"), "lldb")


# ---------------------------------------------------------------------------
# LaunchConfigs
# ---------------------------------------------------------------------------

class TestLaunchConfigs(unittest.TestCase):

    def test_empty_has_no_configurations(self):
        lc = LaunchConfigs.empty()
        self.assertEqual(lc.data["configurations"], [])
        self.assertEqual(lc.data["version"], "0.2.0")

    def test_contains(self):
        lc = LaunchConfigs.empty()
        lc.add(LaunchConfig({"name": "alpha"}))
        self.assertIn(LaunchConfig({"name": "alpha"}), lc)
        self.assertNotIn(LaunchConfig({"name": "beta"}), lc)

    def test_add_multiple_coexist(self):
        lc = LaunchConfigs.empty()
        lc.add(LaunchConfig({"name": "a"}))
        lc.add(LaunchConfig({"name": "b"}))
        self.assertEqual(len(lc.data["configurations"]), 2)

    def test_add_replaces_by_name(self):
        lc = LaunchConfigs.empty()
        lc.add(LaunchConfig({"name": "test", "program": "java"}))
        lc.add(LaunchConfig({"name": "test", "program": "python"}))
        self.assertEqual(len(lc.data["configurations"]), 1)
        self.assertEqual(lc.data["configurations"][0]["program"], "python")

    def test_replace_preserves_order(self):
        lc = LaunchConfigs.empty()
        lc.add(LaunchConfig({"name": "first"}))
        lc.add(LaunchConfig({"name": "second"}))
        lc.add(LaunchConfig({"name": "first", "v": 2}))
        names = [c["name"] for c in lc.data["configurations"]]
        self.assertEqual(names, ["first", "second"])

    def test_write_and_read_roundtrip(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            lc = LaunchConfigs.empty()
            lc.add(LaunchConfig({"name": "x", "val": 1}))
            lc.write(path)
            lc2 = LaunchConfigs.read(path)
            self.assertEqual(lc2.data["configurations"][0]["name"], "x")
            self.assertEqual(lc2.data["configurations"][0]["val"], 1)
        finally:
            path.unlink()

    def test_written_file_is_valid_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            LaunchConfigs.empty().write(path)
            json.loads(path.read_text())  # must not raise
        finally:
            path.unlink()


# ---------------------------------------------------------------------------
# CLI / VSCode harness integration tests
# ---------------------------------------------------------------------------

class TestCLIHarness(unittest.TestCase):
    """Run vsreg.py as a subprocess; verify .vscode/launch.json output."""

    def _raw_result(self, ws: TempWorkspace, label: str, cmd: list[str], extra_args: list[str] = ()) -> dict:
        """Run vsreg in --raw mode and return the first launch config."""
        result = ws.run_vsreg(label, "--raw", *extra_args, "--", *cmd)
        self.assertEqual(result.returncode, 0, result.stderr)
        return ws.configs()[0]

    # -- raw mode (no make invocation) --

    def test_raw_creates_launch_json(self):
        with TempWorkspace() as ws:
            python = shutil.which("python3")
            self._raw_result(ws, "MyLabel", [python, "-c", "pass"])
            self.assertTrue(ws.launch_json.exists())

    def test_raw_config_name(self):
        with TempWorkspace() as ws:
            python = shutil.which("python3")
            cfg = self._raw_result(ws, "My Debug", [python, "-c", "pass"])
            self.assertEqual(cfg["name"], "My Debug")

    def test_raw_config_program(self):
        with TempWorkspace() as ws:
            python = shutil.which("python3")
            cfg = self._raw_result(ws, "x", [python, "-c", "pass"])
            self.assertEqual(cfg["program"], python)

    def test_raw_config_args(self):
        with TempWorkspace() as ws:
            python = shutil.which("python3")
            cfg = self._raw_result(ws, "x", [python, "-c", "print(1)"])
            self.assertIn("-c", cfg["args"])

    def test_raw_build_task(self):
        with TempWorkspace() as ws:
            python = shutil.which("python3")
            cfg = self._raw_result(ws, "x", [python], ["--build-task", "compile"])
            self.assertEqual(cfg["preLaunchTask"], "compile")

    def test_raw_lldb_template(self):
        with TempWorkspace() as ws:
            python = shutil.which("python3")
            cfg = self._raw_result(ws, "x", [python], ["--template", "lldb_only"])
            self.assertEqual(cfg.get("MIMode"), "lldb")

    def test_raw_adds_second_config(self):
        with TempWorkspace() as ws:
            python = shutil.which("python3")
            ws.run_vsreg("first", "--raw", "--", python, "-c", "pass")
            ws.run_vsreg("second", "--raw", "--", python, "-c", "pass")
            self.assertEqual(len(ws.configs()), 2)

    def test_raw_replaces_existing_config(self):
        with TempWorkspace() as ws:
            python = shutil.which("python3")
            ws.run_vsreg("my-cfg", "--raw", "--", python, "-c", "pass")
            ws.run_vsreg("my-cfg", "--raw", "--", python, "-c", "pass")
            self.assertEqual(len(ws.configs()), 1)

    def test_dry_run_does_not_write_file(self):
        with TempWorkspace() as ws:
            python = shutil.which("python3")
            result = ws.run_vsreg("x", "--dry-run", "--raw", "--", python)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(ws.launch_json.exists())

    def test_dry_run_prints_valid_json(self):
        with TempWorkspace() as ws:
            python = shutil.which("python3")
            result = ws.run_vsreg("x", "--dry-run", "--raw", "--", python)
            parsed = json.loads(result.stdout)
            self.assertIn("name", parsed)

    def test_arch_substituted_in_output(self):
        with TempWorkspace() as ws:
            python = shutil.which("python3")
            cfg = self._raw_result(ws, "x", [python])
            cfg_str = json.dumps(cfg)
            self.assertNotIn("$ARCH", cfg_str)

    # -- make/jtreg mode (fake make script) --

    def test_make_mode_creates_launch_json(self):
        with TempWorkspace() as ws:
            jtreg_out = make_jtreg_output(str(ws.dir), JAVA_BIN, {}, ["-version"])
            bin_dir = ws.write_fake_make(jtreg_out)
            result = ws.run_vsreg("ASGCT debug", "--", "make", "test", fake_make_bin=bin_dir)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(ws.launch_json.exists())

    def test_make_mode_config_name(self):
        with TempWorkspace() as ws:
            jtreg_out = make_jtreg_output(str(ws.dir), JAVA_BIN, {}, ["-version"])
            bin_dir = ws.write_fake_make(jtreg_out)
            ws.run_vsreg("ASGCT debug", "--", "make", "test", fake_make_bin=bin_dir)
            cfg = ws.configs()[0]
            self.assertEqual(cfg["name"], "ASGCT debug")

    def test_make_mode_program(self):
        with TempWorkspace() as ws:
            jtreg_out = make_jtreg_output(str(ws.dir), JAVA_BIN, {}, ["-version"])
            bin_dir = ws.write_fake_make(jtreg_out)
            ws.run_vsreg("x", "--", "make", "test", fake_make_bin=bin_dir)
            cfg = ws.configs()[0]
            self.assertEqual(cfg["program"], JAVA_BIN)

    def test_make_mode_env_in_config(self):
        with TempWorkspace() as ws:
            jtreg_out = make_jtreg_output(str(ws.dir), JAVA_BIN, {"TEST_VAR": "hello"}, ["-version"])
            bin_dir = ws.write_fake_make(jtreg_out)
            ws.run_vsreg("x", "--", "make", "test", fake_make_bin=bin_dir)
            cfg = ws.configs()[0]
            env_map = {e["name"]: e["value"] for e in cfg["environment"]}
            self.assertEqual(env_map.get("TEST_VAR"), "hello")

    def test_make_mode_whitebox_args_present(self):
        with TempWorkspace() as ws:
            jtreg_out = make_jtreg_output(str(ws.dir), JAVA_BIN, {}, ["-version"])
            bin_dir = ws.write_fake_make(jtreg_out)
            ws.run_vsreg("x", "--", "make", "test", fake_make_bin=bin_dir)
            cfg = ws.configs()[0]
            self.assertIn("-XX:+WhiteBoxAPI", cfg["args"])

    def test_make_mode_build_task(self):
        with TempWorkspace() as ws:
            jtreg_out = make_jtreg_output(str(ws.dir), JAVA_BIN, {}, ["-version"])
            bin_dir = ws.write_fake_make(jtreg_out)
            ws.run_vsreg("x", "--build-task", "Make test-image", "--", "make", "test", fake_make_bin=bin_dir)
            cfg = ws.configs()[0]
            self.assertEqual(cfg["preLaunchTask"], "Make test-image")

    def test_make_mode_dry_run(self):
        with TempWorkspace() as ws:
            jtreg_out = make_jtreg_output(str(ws.dir), JAVA_BIN, {}, ["-version"])
            bin_dir = ws.write_fake_make(jtreg_out)
            result = ws.run_vsreg("x", "--dry-run", "--", "make", "test", fake_make_bin=bin_dir)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(ws.launch_json.exists())
            parsed = json.loads(result.stdout)
            self.assertEqual(parsed["name"], "x")


if __name__ == "__main__":
    unittest.main()
