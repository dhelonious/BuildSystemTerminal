# Build System Terminal

Run Sublime Text 3 builds in a native terminal instead of the console.

Simply choose the `"terminal_exec"` target in any build system. The command will run in a terminal window which is either:
* `xterm` (Unix)
* `cmd` (Windows)

In addition the output will be put into the default Build Results panel and errors are shown inline. To cancel any terminal build system use `Terminal Build: Cancel`.

This build system is a modification of the default Sublime Text 3 `exec.py` script. However, in contrast to the default build system script no information about the exit code of the command can be obtained.

The transfer of the output of the command from the terminal back to Sublime Text 3 has to be done via files. Those temporary files are stored in the Sublime Text 3 cache path and will be deleted after the transfer is complete. Since all build systems use the same file, it should for now be only possible to run one build at the same time. To clear the cache files manually one may use the *Clear Cache* option in the package settings.

## Features

### Syntax highlighting

BuildSystemTerminal provides some basic syntax highlighting for the Build Results panel. Most of the scopes being used should be defined in any color scheme. However, some special syntax scopes are used:
* `message.success.console`
* `message.error.console`

This feature can be disabled with the boolean key `"syntax"` in the user settings.

### Input prompt

With BuildSystemInput you can use the additional boolean key `"prompt"` in you build systems. If set to `true`, an input panel will be shown, where the command can be edited before execution. This allows for great flexibility an can be used for passing additional arguments to the build system command.

### Terminal geometry

To specify the geometry of the terminal window, you can add definitions for the number of columns and lines to the user settings:

```json
    "geometry": {
        "columns": 80,
        "lines": 20
    }
```

## Requirements

This package requires `tee`, which is a default Unix command. A port for Windows is included, which was taken from [here](http://gnuwin32.sourceforge.net/packages/coreutils.htm). You can also customize the path for tee in the package user settings:

```json
    "tee": {
        "linux": "/usr/bin/tee",
        "osx": "/usr/bin/tee",
        "windows": "C:\\Program Files (x86)\\GnuWin32\\bin\\tee.exe"
    }
```

**Note:** Powershell seems to be a destined choice for Windows, since it has a built-in `tee` command. However, there are several issues with Powershell right now, which makes it unsuitable for the purpose of this package, though, it is used implicitly for setting the terminal geometry correctly.

## Example

Basic Python build system:
```json
    {
        "name": "Python Terminal",
        "selector": "source.python",
        "target": "terminal_exec",
        "cmd": ["python", "$file"],
        "file_regex": "^\\s*File \"(...*?)\", line ([0-9]*)",
        "variants":
        [
            {
                "name": "Cancel",
                "kill": true
            },
            {
                "name": "Prompt",
                "prompt": true,
            }
        ]
    }
```
