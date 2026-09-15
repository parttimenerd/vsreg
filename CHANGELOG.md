# Changelog

All notable changes to vsreg are documented here.

## [Unreleased]

### Added

- **`--rr [PORT]`** flag — generates a launch config using the `rr` template
  (`miDebuggerServerAddress: localhost:PORT`) and automatically creates a
  background `rr replay (LABEL)` task in `.vscode/tasks.json` set as
  `preLaunchTask`. Default port is 50505; override with e.g. `--rr 12345`.
  New template: `templates/rr.json`.

- **`--build-command CMD`** flag — creates a `Build (LABEL)` entry in
  `.vscode/tasks.json` from the given shell command (e.g.
  `--build-command "make images test-image"`) and sets it as `preLaunchTask`
  automatically. Combining with `--build-task` lets `--build-task` win as the
  `preLaunchTask` label while still writing the task entry.

- **`--clangd`** flag — writes `clangd.arguments: ["--compile-commands-dir=<platform>"]`
  into `.vscode/settings.json`, merging with any existing content.
  Auto-detects the platform from the most-recently-modified
  `build/*/compile_commands.json`. Override with `--platform PLATFORM`.
  Can be used standalone (no `LABEL`/`COMMAND` required) or combined with a
  launch config invocation.

- **`remote` subcommand** — generates remote debug configs for three flavors:
  - `gdbserver` (default): cppdbg config with `miDebuggerServerAddress: HOST:PORT`.
  - `ssh`: cppdbg config pointing at `localhost:PORT`; auto-creates a background
    SSH-tunnel task (`ssh -L PORT:localhost:PORT USER@HOST -N`) in `tasks.json`.
  - `vscode-remote`: standard cppdbg config (no `miDebuggerServerAddress`) for
    use inside a VS Code Remote SSH workspace; writes
    `remote.SSH.serverInstallPath` into `settings.json`.
  New template: `templates/remote.json`.

  Usage:
  ```sh
  ./vsreg.py remote LABEL --host HOST --port PORT --remote-path PATH \
    [--flavor {gdbserver,ssh,vscode-remote}] [--user USER] [--build-task TASK] [-d]
  ```

### Changed

- `LABEL` and `COMMAND` are now optional when `--clangd` is used as a
  standalone pass (no launch config needed).

---

## Earlier releases

### 2026-07-14

- Added CI (`.github/workflows/ci.yml`) running unit tests on Python 3.10–3.13
  and end-to-end VS Code harness tests via Playwright.
- Added `test_vsreg.py` (unit tests) and `test_vscode_harness.py` (e2e tests).
- Added `jvm_sigsegv.py`: GDB Python script that intercepts SIGSEGV and only
  passes it to the JVM when the fault address is on the safepoint polling page;
  stops on all other segfaults. Templates updated to use it via `$VSREG_DIR`.

### 2025-02-10

- Added `templates/gdb_without_signals.json` template that ignores `SIGUSR1`,
  `SIGUSR2`, and `SIGSEGV` signals (useful for JVM debugging).

### 2023 (initial releases)

- Initial release: `vsreg.py` generates `.vscode/launch.json` entries from
  `make test … JTREG="VERBOSE=all"` output.
- Added `--raw` / auto-raw mode for non-`make` commands.
- Added `--build-task` flag.
- Added `--template` flag with `default` and `lldb_only` templates.
- Added macOS (lldb) support.
- Improved README and blog post.
