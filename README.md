vsreg
=====

Generate VS Code debug launch configurations for OpenJDK JTREG tests — or any
native command — without writing `launch.json` by hand.

```sh
git clone https://github.com/parttimenerd/vsreg
vsreg/vsreg.py "ASGCT debug" -- make test \
  TEST=jtreg:test/hotspot/jtreg/serviceability/AsyncGetCallTrace \
  JTREG="VERBOSE=all"
```

vsreg runs the command, captures the JTREG rerun block, and writes a
`cppdbg` entry to `.vscode/launch.json`. Hit F5 to start debugging.

> **Note:** Only single tests are supported, not test suites.

## Requirements

- Python 3.10 or newer
- Linux or macOS

## Features

| Feature | Flag / subcommand |
|---|---|
| JTREG test → launch config | `LABEL -- make test …` |
| Any command → launch config | `LABEL --raw -- COMMAND` or `LABEL -- COMMAND` (auto-raw when no `make`) |
| Auto-create build task | `--build-command CMD` |
| Use an existing build task | `--build-task TASK` |
| rr record-and-replay debugging | `--rr [PORT]` |
| clangd compile_commands.json setup | `--clangd [--platform PLATFORM]` |
| Remote debugging (gdbserver / SSH / VS Code Remote SSH) | `remote` subcommand |
| Preview without writing files | `--dry-run` |

---

## Basic usage

### JTREG test

```sh
./vsreg.py "ASGCT debug" -- make test \
  TEST=jtreg:test/hotspot/jtreg/serviceability/AsyncGetCallTrace \
  JTREG="VERBOSE=all"
```

The `--` separates vsreg flags from the command to run. vsreg executes the
command, parses the JTREG rerun block from its output, and writes the config.

### Any other command

```sh
./vsreg.py "Example" -- java Example.java
./vsreg.py "My Agent" --raw -- /path/to/my-binary --some-arg
```

`--raw` skips command execution and builds the config directly from the command
line. It is set automatically when `make` is not in the command.

### Preview without writing

```sh
./vsreg.py "ASGCT debug" --dry-run -- make test TEST=… JTREG="VERBOSE=all"
```

Prints the launch config JSON to stdout without touching `.vscode/launch.json`.

---

## Build task auto-setup

Instead of writing a `tasks.json` entry by hand, pass `--build-command`:

```sh
./vsreg.py "ASGCT debug" \
  --build-command "make images test-image" \
  -- make test TEST=jtreg:… JTREG="VERBOSE=all"
```

This creates a `Build (ASGCT debug)` entry in `.vscode/tasks.json` and sets it
as `preLaunchTask`. Re-running vsreg with the same label replaces the entry.

If you already have a task you want to reuse, pass its label with `--build-task`
instead. When both flags are given, `--build-task` wins as the `preLaunchTask`
label (but the task is still written to `tasks.json`).

---

## rr debugging

[rr](https://rr-project.org/) records program execution and lets you replay it
deterministically. To create a launch config that attaches GDB to `rr replay`:

```sh
./vsreg.py "ASGCT rr" --rr -- make test \
  TEST=jtreg:test/hotspot/jtreg/serviceability/AsyncGetCallTrace \
  JTREG="VERBOSE=all"
```

This creates:
- A `launch.json` entry using the `rr` template — GDB connects to
  `localhost:50505` via `miDebuggerServerAddress`.
- A background `tasks.json` entry `rr replay (ASGCT rr)` that runs
  `rr replay -s 50505 -k` as the `preLaunchTask`.

Use a custom port with `--rr 12345`.

**Typical workflow:**

1. Record: `rr record make test TEST=jtreg:… JTREG="VERBOSE=all"`
2. Run vsreg: `./vsreg.py "ASGCT rr" --rr -- make test TEST=jtreg:… JTREG="VERBOSE=all"`
3. Hit F5 — VS Code starts `rr replay` and attaches GDB automatically.

---

## clangd setup

Write `clangd.arguments` into `.vscode/settings.json` so clangd finds the
right `compile_commands.json` for your build:

```sh
./vsreg.py --clangd
```

vsreg picks the most-recently-modified `build/*/compile_commands.json`
automatically. Override the platform with `--platform`:

```sh
./vsreg.py --clangd --platform linux-x86_64-server-fastdebug
```

Combine with a launch config in one shot:

```sh
./vsreg.py "ASGCT debug" --clangd -- make test TEST=jtreg:… JTREG="VERBOSE=all"
```

`--clangd` can be used standalone — no `LABEL` or command required.

---

## Remote debugging

The `remote` subcommand creates a launch config that connects to a remote
process. Three flavors are available:

### Direct gdbserver

GDB connects directly to a `gdbserver` (or `rr replay`) on the remote host:

```sh
./vsreg.py remote "ASGCT remote" \
  --host build-server --port 1234 \
  --remote-path /home/user/jdk/build/linux-x86_64-server-fastdebug/images/jdk/bin/java
```

### SSH tunnel

GDB connects to `localhost:PORT`; vsreg creates a background task that forwards
the port over SSH before the session starts:

```sh
./vsreg.py remote "ASGCT ssh" --flavor ssh \
  --host build-server --user jbech --port 1234 \
  --remote-path /home/user/jdk/build/linux-x86_64-server-fastdebug/images/jdk/bin/java
```

The tunnel task runs `ssh -L 1234:localhost:1234 jbech@build-server -N` in the
background and is set as `preLaunchTask` automatically.

### VS Code Remote SSH workspace

When you are already connected to the remote host via VS Code Remote SSH, use
the `vscode-remote` flavor. It generates a standard `cppdbg` config (no
`miDebuggerServerAddress`) and writes `remote.SSH.serverInstallPath` into
`settings.json`:

```sh
./vsreg.py remote "ASGCT vscode-remote" --flavor vscode-remote \
  --host build-server --user jbech --port 22 \
  --remote-path /home/user/jdk/build/linux-x86_64-server-fastdebug/images/jdk/bin/java
```

---

## Templates

vsreg fills a JSON template for each launch config. Select one with
`--template`:

| Template | Description |
|---|---|
| `default` | GDB on Linux/Windows, lldb on macOS. Loads `jvm_sigsegv.py` to pass safepoint SIGSEGVs to the JVM and stop on real crashes. |
| `gdb_without_signals` | Like `default` but ignores `SIGUSR1`, `SIGUSR2`, and `SIGSEGV` entirely — useful when `jvm_sigsegv.py` is too slow. |
| `lldb_only` | lldb-only config with SIGSEGV pass-through. |
| `rr` | GDB connecting to `localhost:$RR_PORT` via `miDebuggerServerAddress`. Used automatically by `--rr`. |
| `remote` | GDB connecting to `$REMOTE_HOST:$REMOTE_PORT`. Used automatically by the `remote` subcommand. |

You can also pass the path to any `.json` file:

```sh
./vsreg.py "My Debug" --template /path/to/my-template.json -- java Main
```

Token substitutions available in templates: `$NAME`, `$ARCH`, `$VSREG_DIR`,
`$RR_PORT`, `$REMOTE_HOST`, `$REMOTE_PORT`.

---

## Options

```
usage: vsreg.py [-h] [-t TEMPLATE] [-d] [-r] [-b TASK] [--build-command CMD]
                [--rr [PORT]] [--clangd] [--platform PLATFORM]
                [LABEL] [COMMAND ...]

       vsreg.py remote LABEL --host HOST --port PORT --remote-path PATH
                [--flavor {gdbserver,ssh,vscode-remote}] [--user USER]
                [--build-task TASK] [-d]

positional arguments:
  LABEL                 Label of the config (optional when --clangd used alone)
  COMMAND               Command to run (use -- to separate from vsreg flags)

options:
  -h, --help            show this help message and exit
  -t TEMPLATE, --template TEMPLATE
                        Template name or path to a .json file (default: default)
  -d, --dry-run         Print config(s) to stdout without writing files
  -r, --raw             Build config from command line without running the command
  -b TASK, --build-task TASK
                        Existing tasks.json task label to use as preLaunchTask
  --build-command CMD   Shell command; auto-creates a tasks.json entry as preLaunchTask
  --rr [PORT]           Use rr replay (default port 50505); auto-selects rr template
  --clangd              Write clangd.arguments into .vscode/settings.json
  --platform PLATFORM   Platform dir (e.g. linux-x86_64-server-fastdebug);
                        auto-detected from build/*/compile_commands.json if omitted

remote subcommand options:
  --host HOST           Remote host name or IP
  --port PORT           gdbserver or SSH port
  --remote-path PATH    Absolute path to the binary on the remote host
  --flavor {gdbserver,ssh,vscode-remote}
                        gdbserver: GDB connects directly to remote host
                        ssh: SSH tunnel + GDB connects to localhost
                        vscode-remote: standard cppdbg for VS Code Remote SSH workspace
  --user USER           SSH username (required for ssh flavor)
  --build-task TASK     Task label to run before the debug session
```

---

*Inspired by [bear](https://github.com/rizsotto/Bear). Learn more in the blog
post [Debugging OpenJDK Tests in VSCode Without Losing Your Mind](https://mostlynerdless.de/blog/2023/06/21/debugging-openjdk-tests-in-vscode-without-losing-your-mind/).*

## Contributing

Happy to accept contributions — new templates, bug fixes, improvements. Open an
issue or a pull request.

## License

MIT, Copyright 2023 SAP SE or an SAP affiliate company, Johannes Bechberger
and vsreg contributors.

This project is a prototype of the [SapMachine](https://sapmachine.io) team at
[SAP SE](https://sap.com).
