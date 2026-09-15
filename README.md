vsreg
=====

Debugging JTREG tests with VSCode is difficult, you have to write the launch.json file by hand.
But worry no more: This small utility will do this for you :) _And it supports regular commands too._

Just pass the make test command to it (with `JTREG="VERBOSE=all"`) with a test label,
and vsreg updates the `launch.json` file for you:

```sh
git clone https://github.com/parttimenerd/vsreg
vsreg/vsreg.py "ASGCT debug" -- make test TEST=jtreg:test/hotspot/jtreg/serviceability/AsyncGetCallTrace JTREG="VERBOSE=all"
```

... and you can start debugging with VSCode, recompile your tests with `make images test-image`.
You can add a task to your `tasks.json` file and pass the label to the `--build-task` option:

```json
{
  "label": "Make test-image",
  "type": "shell",
  "options": {
      "cwd": "${workspaceFolder}"
  },
  "command": "/usr/bin/gmake",
  "args": ["images", "test-image"],
  "problemMatcher": ["$gcc"]
}
```

To try vsreg without modifying your `launch.json` file, you can use the `--dry-run` option:

```sh
./vsreg.py "ASGCT debug" --dry-run -- make test TEST=jtreg:test/hotspot/jtreg/serviceability/AsyncGetCallTrace JTREG="VERBOSE=all"
```

For regular commands like `java Example.java`, just pass the command to `vsreg`:

```sh
./vsreg.py "Example" -- java Example.java
```

This is helpful for debugging native code like Java agents or even code which is unrelated to the OpenJDK.

## Templates

The tool fills the passed template (default `default`) which can be configured with the `--template` option.
The default template looks like this:

```json
{
  "name": "$NAME",
  "type": "cppdbg",
  "request": "launch",
  "program": "",
  "args": [],
  "stopAtEntry": false,
  "cwd": "",
  "environment": [],
  "externalConsole": false,
  "linux": {
    "MIMode": "gdb",
    "targetArchitecture": "$ARCH",
    "miDebuggerPath": "/usr/bin/gdb",
    "setupCommands": [
      {
        "description": "Enable pretty-printing for gdb",
        "text": "-enable-pretty-printing",
        "ignoreFailures": true
      },
      {
        "description": "The new process is debugged after a fork. The parent process runs unimpeded.",
        "text": "-gdb-set follow-fork-mode child",
        "ignoreFailures": true
      }
    ],
  },
  /* ... */
  "preLaunchTask": ""
}
```

_Use the `gdb_without_signal.json` template to get ignore `SIGUSR1`, `SIGUSR2`, and `SIGSEGV` signals._

Please be aware that only single tests are supported, not test suites.

You learn a tiny bit more on this tool in my blog post
[Debugging OpenJDK Tests in VSCode Without Losing Your Mind](https://mostlynerdless.de/blog/2023/06/21/debugging-openjdk-tests-in-vscode-without-losing-your-mind/)
in which I introduced this tool.

## Build command auto-setup

Instead of manually creating a `tasks.json` entry and passing `--build-task`, you can let vsreg create the task automatically with `--build-command`:

```sh
./vsreg.py "ASGCT debug" --build-command "make images test-image" -- make test TEST=jtreg:test/hotspot/jtreg/serviceability/AsyncGetCallTrace JTREG="VERBOSE=all"
```

This creates a `Build (ASGCT debug)` task in `.vscode/tasks.json` and sets it as the `preLaunchTask` automatically. If you pass both `--build-command` and `--build-task`, `--build-task` wins as the `preLaunchTask` label (but the task is still written).

## rr debugging

[rr](https://rr-project.org/) lets you record and deterministically replay program execution. To create a launch configuration that connects GDB to an `rr replay` gdbserver:

```sh
./vsreg.py "ASGCT rr" --rr -- make test TEST=jtreg:test/hotspot/jtreg/serviceability/AsyncGetCallTrace JTREG="VERBOSE=all"
```

This creates:
- A `launch.json` entry using the `rr` template (GDB connecting to `localhost:50505`)
- A `tasks.json` background task `rr replay (ASGCT rr)` that runs `rr replay -s 50505 -k` before the debug session starts

Use a custom port with `--rr 12345`. The `rr` template uses `miDebuggerServerAddress` so GDB attaches to the replayed process.

## clangd setup

Run vsreg with `--clangd` to write `clangd.arguments` into `.vscode/settings.json`, pointing clangd at the correct `compile_commands.json` for your build:

```sh
./vsreg.py --clangd
```

vsreg auto-detects the platform by finding `build/*/compile_commands.json` (picks the most-recently-modified one). Override with `--platform`:

```sh
./vsreg.py --clangd --platform linux-x86_64-server-fastdebug
```

You can combine with a launch config in one shot:

```sh
./vsreg.py "ASGCT debug" --clangd -- make test TEST=jtreg:...
```

## Remote debugging

Use the `remote` subcommand to create a launch config that connects to a remote `gdbserver`, or to an rr replay server forwarded over SSH.

**Direct gdbserver connection:**

```sh
./vsreg.py remote "ASGCT remote" \
  --host build-server --port 1234 \
  --remote-path /home/user/jdk/build/linux-x86_64-server-fastdebug/images/jdk/bin/java
```

**SSH tunnel (auto-creates the tunnel task in `tasks.json`):**

```sh
./vsreg.py remote "ASGCT ssh" --flavor ssh \
  --host build-server --user jbech --port 1234 \
  --remote-path /home/user/jdk/build/.../bin/java
```

**VS Code Remote SSH workspace:**

```sh
./vsreg.py remote "ASGCT vscode-remote" --flavor vscode-remote \
  --host build-server --user jbech --port 22 \
  --remote-path /home/user/jdk/build/.../bin/java
```

Options
-------
```
usage: vsreg.py [-h] [-t TEMPLATE] [-d] [-r] [-b TASK] [--build-command CMD]
                [--rr [PORT]] [--clangd] [--platform PLATFORM]
                [LABEL] [COMMAND ...]

       vsreg.py remote LABEL --host HOST --port PORT --remote-path PATH
                [--flavor {gdbserver,ssh,vscode-remote}] [--user USER]
                [--build-task TASK] [-d]

positional arguments:
  LABEL                 Label of the config (optional when --clangd used alone)
  COMMAND               Command to run

options:
  -h, --help            show this help message and exit
  -t TEMPLATE, --template TEMPLATE
  -d, --dry-run         Only print the config(s), don't write files
  -r, --raw             Use raw command without execution
  -b TASK, --build-task TASK
                        Existing tasks.json task label to run before debugging
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
                        gdbserver: GDB connects directly to remote
                        ssh: SSH tunnel + GDB connects to localhost
                        vscode-remote: standard cppdbg for VS Code Remote SSH workspace
  --user USER           SSH username (required for ssh flavor)
  --build-task TASK     Task label to run before the debug session
```

*The tool is inspired by [bear](https://github.com/rizsotto/Bear)*

Requirements
------------
- Python 3.10 or newer
- Linux (macOS support is coming)

Contributing
------------
I'm happy for any contributions, like new templates, just open an issue or a pull request :)


License
-------
MIT, Copyright 2023 SAP SE or an SAP affiliate company, Johannes Bechberger and vsreg contributors

This project is a prototype of the [SapMachine](https://sapmachine.io) team at [SAP SE](https://sap.com).