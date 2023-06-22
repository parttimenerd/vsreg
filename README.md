vsreg
=====

Debugging JTREG tests with VSCode is difficult, you have to write the launch.json file by hand.
But worry no more: This small utility will do this for you :)

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

The tool fills the passed template (default `gdb`) which can be configured with the `--template` option.
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
  "MIMode": "gdb",
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
  "preLaunchTask": ""
}
```

Please be aware that only single tests are supported, not test suites.

You learn a tiny bit more on this tool in my blog post
[Debugging OpenJDK Tests in VSCode Without Losing Your Mind](https://mostlynerdless.de/blog/2023/06/21/debugging-openjdk-tests-in-vscode-without-losing-your-mind/)
in which I introduced this tool.

Options
-------
```shell
usage: vsreg.py [-h] [-t TEMPLATE] [-d] [-b TASK] LABEL COMMAND [COMMAND ...]

Create a debug launch config for a JTREG test run

positional arguments:
  LABEL                 Label of the config
  COMMAND               Command to run

options:
  -h, --help            show this help message and exit
  -t TEMPLATE, --template TEMPLATE
                        Template to use for the launch config, or name of file
                        without suffix in vsreg/template folder
  -d, --dry-run         Only print the launch config
  -b TASK, --build-task TASK
                        Task to run before the command
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