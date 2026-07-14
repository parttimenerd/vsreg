"""
VSCode integration tests using a real code serve-web instance + Playwright.

These tests:
  1. Run vsreg.py to write .vscode/launch.json into a temp workspace
  2. Start `code serve-web` pointing at that workspace
  3. Drive the VS Code web UI via Playwright to verify the debug configurations
     appear in the Run & Debug panel — i.e. VS Code actually parses and shows them.

Requirements:
  - VS Code CLI (`code`) installed and on PATH
  - `playwright` Python package installed  (`pip install playwright`)
  - Chromium downloaded (`python -m playwright install chromium`)

Skip markers:
  - Tests are skipped if `code` or Playwright's Chromium are not available.
"""

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

VSREG = Path(__file__).parent / "vsreg.py"
JAVA_BIN = shutil.which("java") or "/usr/lib/jvm/java-21/bin/java"

# ---------------------------------------------------------------------------
# Availability guards
# ---------------------------------------------------------------------------

def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:
        return False


CODE_AVAILABLE = shutil.which("code") is not None
PLAYWRIGHT_AVAILABLE = CODE_AVAILABLE and _playwright_available()

skip_no_code = unittest.skipUnless(CODE_AVAILABLE, "VS Code CLI (`code`) not on PATH")
skip_no_playwright = unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright/Chromium not available")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_jtreg_output(cwd: str, java: str, env: dict, args: list[str]) -> str:
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


class VSCodeServer:
    """Context manager that starts/stops `code serve-web` for a workspace."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.port = _free_port()
        self._data_dir = None
        self._proc = None

    def __enter__(self):
        self._data_dir = Path(tempfile.mkdtemp(prefix="vsreg-data-", dir="/tmp"))
        self._proc = subprocess.Popen(
            [
                "code", "serve-web",
                "--without-connection-token",
                "--port", str(self.port),
                "--default-folder", str(self.workspace),
                "--server-data-dir", str(self._data_dir),
                "--accept-server-license-terms",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_ready()
        return self

    def __exit__(self, *_):
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._data_dir and self._data_dir.exists():
            shutil.rmtree(self._data_dir, ignore_errors=True)

    def _wait_ready(self, timeout: float = 20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError(f"VS Code server on port {self.port} did not start in {timeout}s")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _wait_for_workbench(page, timeout_ms: int = 40_000):
    """Wait for the VS Code workbench to finish loading."""
    page.wait_for_selector(".monaco-workbench", timeout=timeout_ms)
    # VS Code loads async — wait for the activity bar to stabilise
    page.wait_for_function(
        "() => !!document.querySelector('[aria-label*=\"Run and Debug\"]')",
        timeout=timeout_ms,
    )


def _open_run_panel(page, timeout_ms: int = 30_000):
    """Click the Run & Debug activity-bar icon and wait for configs to load."""
    page.locator('[aria-label*="Run and Debug"]').first.click()
    # VS Code reads launch.json asynchronously; wait until the dropdown shows
    # a real config name (not the placeholder "No Configurations").
    page.wait_for_function(
        """() => {
            const el = document.querySelector('[aria-label^="Debug Launch Configurations:"]');
            return el && !el.getAttribute("aria-label").includes("No Configurations");
        }""",
        timeout=timeout_ms,
    )


def _config_names_in_panel(page) -> list[str]:
    """Return all configuration names visible in the Run & Debug dropdown."""
    labels = page.evaluate(
        """() => [...document.querySelectorAll('[aria-label^="Debug Launch Configurations:"]')]
                .map(e => e.getAttribute("aria-label").replace("Debug Launch Configurations: ", ""))"""
    )
    return labels


def _run_vsreg(workspace: Path, *args: str, fake_make_bin: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if fake_make_bin:
        env["PATH"] = str(fake_make_bin) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        [sys.executable, str(VSREG), *args],
        capture_output=True, text=True, cwd=workspace, env=env,
    )


def _write_fake_make(bin_dir: Path, stderr_output: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "make"
    script.write_text(f"#!/bin/sh\ncat >&2 <<'EOF'\n{stderr_output}EOF\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@skip_no_playwright
class TestVSCodeHarness(unittest.TestCase):
    """Verify that vsreg-generated launch.json appears correctly in VS Code."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="vsreg-ws-", dir="/tmp"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _browser_and_page(self, pw):
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        return browser, page

    # -- raw-mode (no make) tests --

    def test_raw_config_appears_in_vscode(self):
        """A config created with --raw shows in the VS Code Run & Debug panel."""
        from playwright.sync_api import sync_playwright

        python = shutil.which("python3")
        _run_vsreg(self._tmp, "My Raw Config", "--raw", "--", python, "-c", "pass")

        with VSCodeServer(self._tmp) as server:
            with sync_playwright() as pw:
                browser, page = self._browser_and_page(pw)
                try:
                    page.goto(server.url, timeout=15000)
                    _wait_for_workbench(page)
                    _open_run_panel(page)
                    names = _config_names_in_panel(page)
                    self.assertIn("My Raw Config", names)
                finally:
                    browser.close()

    def test_raw_config_name_in_panel_title(self):
        """The config name appears verbatim in the dropdown aria-label."""
        from playwright.sync_api import sync_playwright

        python = shutil.which("python3")
        _run_vsreg(self._tmp, "ASGCT debug", "--raw", "--", python)

        with VSCodeServer(self._tmp) as server:
            with sync_playwright() as pw:
                browser, page = self._browser_and_page(pw)
                try:
                    page.goto(server.url, timeout=15000)
                    _wait_for_workbench(page)
                    _open_run_panel(page)
                    # The dropdown label reads "Debug Launch Configurations: <name>"
                    label = page.locator('[aria-label^="Debug Launch Configurations:"]').first
                    self.assertIn("ASGCT debug", label.get_attribute("aria-label"))
                finally:
                    browser.close()

    def test_multiple_configs_all_appear(self):
        """Adding two configs via vsreg results in both being choosable in VS Code."""
        from playwright.sync_api import sync_playwright

        python = shutil.which("python3")
        _run_vsreg(self._tmp, "Config Alpha", "--raw", "--", python)
        _run_vsreg(self._tmp, "Config Beta", "--raw", "--", python)

        with VSCodeServer(self._tmp) as server:
            with sync_playwright() as pw:
                browser, page = self._browser_and_page(pw)
                try:
                    page.goto(server.url, timeout=15000)
                    _wait_for_workbench(page)
                    _open_run_panel(page)
                    # Open the dropdown to reveal all config options
                    page.locator('[aria-label^="Debug Launch Configurations:"]').first.click()
                    page.wait_for_selector('[role="menuitemcheckbox"]', timeout=5000)
                    options = page.evaluate(
                        "() => [...document.querySelectorAll('[role=\"menuitemcheckbox\"]')].map(e => e.getAttribute('aria-label'))"
                    )
                    self.assertIn("Config Alpha", options)
                    self.assertIn("Config Beta", options)
                finally:
                    browser.close()

    def test_replace_config_reflected_in_vscode(self):
        """Running vsreg twice with the same label replaces the config, not duplicates it."""
        from playwright.sync_api import sync_playwright

        python = shutil.which("python3")
        _run_vsreg(self._tmp, "My Config", "--raw", "--", python, "-c", "pass")
        _run_vsreg(self._tmp, "My Config", "--raw", "--", python, "-c", "pass")

        # Verify launch.json has only one entry
        launch = json.loads((self._tmp / ".vscode" / "launch.json").read_text())
        self.assertEqual(len(launch["configurations"]), 1)

        with VSCodeServer(self._tmp) as server:
            with sync_playwright() as pw:
                browser, page = self._browser_and_page(pw)
                try:
                    page.goto(server.url, timeout=15000)
                    _wait_for_workbench(page)
                    _open_run_panel(page)
                    names = _config_names_in_panel(page)
                    self.assertEqual(names.count("My Config"), 1)
                finally:
                    browser.close()

    # -- make/jtreg mode tests --

    def test_jtreg_config_appears_in_vscode(self):
        """A config created from a jtreg make run shows in the VS Code debug panel."""
        from playwright.sync_api import sync_playwright

        bin_dir = self._tmp / "bin"
        jtreg_out = make_jtreg_output(str(self._tmp), JAVA_BIN, {}, ["-version"])
        _write_fake_make(bin_dir, jtreg_out)
        result = _run_vsreg(self._tmp, "ASGCT debug", "--", "make", "test", fake_make_bin=bin_dir)
        self.assertEqual(result.returncode, 0, result.stderr)

        with VSCodeServer(self._tmp) as server:
            with sync_playwright() as pw:
                browser, page = self._browser_and_page(pw)
                try:
                    page.goto(server.url, timeout=15000)
                    _wait_for_workbench(page)
                    _open_run_panel(page)
                    names = _config_names_in_panel(page)
                    self.assertIn("ASGCT debug", names)
                finally:
                    browser.close()

    def test_jtreg_config_has_correct_program(self):
        """The program path in launch.json written by vsreg matches JAVA_BIN."""
        from playwright.sync_api import sync_playwright

        bin_dir = self._tmp / "bin"
        jtreg_out = make_jtreg_output(str(self._tmp), JAVA_BIN, {}, ["-version"])
        _write_fake_make(bin_dir, jtreg_out)
        _run_vsreg(self._tmp, "x", "--", "make", "test", fake_make_bin=bin_dir)

        launch = json.loads((self._tmp / ".vscode" / "launch.json").read_text())
        self.assertEqual(launch["configurations"][0]["program"], JAVA_BIN)

    def test_open_launch_json_link_visible(self):
        """The 'Open launch.json' gear icon is present in the Run & Debug panel."""
        from playwright.sync_api import sync_playwright

        python = shutil.which("python3")
        _run_vsreg(self._tmp, "x", "--raw", "--", python)

        with VSCodeServer(self._tmp) as server:
            with sync_playwright() as pw:
                browser, page = self._browser_and_page(pw)
                try:
                    page.goto(server.url, timeout=15000)
                    _wait_for_workbench(page)
                    _open_run_panel(page)
                    gear = page.locator('[aria-label="Open \'launch.json\'"]')
                    self.assertTrue(gear.count() > 0)
                finally:
                    browser.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
