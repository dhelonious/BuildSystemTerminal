# Build System Terminal

Run Sublime Text 3 builds in a native terminal instead of the console.

Simply choose the `"terminal_exec"` target in any build system. The command will be run in a terminal window which is either:
* `xterm` (Unix)
* `cmd` (Windows)
In addition the output will be put into the default Build Results panel and errors are shown inline.

This build system is a modification of the default Sublime Text 3 `exec.py` script. However, there are some drawbacks:
* No information about the exit code of the command is obtained.

The transfer of the output of the command from the terminal back to Sublime Text 3 has to be done via files. Those temporary files are stored in the Sublime Text 3 cache path and will be deleted after the transfer is complete. However, if the build terminal is closed or interrupted during the build process, the file stream remains open. The build system will not run until Sublime Text 3 was restarted.

## Requirements

This package require `tee` to work, which is a default Unix command. A port for Windows can be found [here](http://gnuwin32.sourceforge.net/packages/coreutils.htm). Don't forget to add the executable path (default `C:\Program Files (x86)\GnuWin32\bin`) to your path environment variable.

## Example

Basic `python` build system:
```json
    {
        "name": "Python Terminal",
        "target": "terminal_exec",
        "selector": "source.python",
        "cmd": ["python", "$file"],
        "file_regex": "^\\s*File \"(...*?)\", line ([0-9]*)"
    }
```

## TODO

1. Make `tee` path configurable
2. Find a reliable way to close the file handle 