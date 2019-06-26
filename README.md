# Build System Terminal

Run Sublime Text 3 builds in a native terminal instead of the console.

Simply choose the `"terminal_exec"` target in any build system. The command will run in a terminal window which is either:
* `xterm` (Unix)
* `cmd` (Windows)

In addition the output will be put into the default Build Results panel and errors are shown in-line. To cancel any terminal build system use `Terminal Build: Cancel`.

This build system is a modification of the default Sublime Text 3 `exec` script. However, in contrast to the default build system script no information about the exit code of the command can be obtained.

The transfer of the output of the command from the terminal back to Sublime Text 3 has to be done via files. Those temporary files are stored in the Sublime Text 3 cache path and will be deleted after the transfer is complete. To clear the cache files manually one may use the *Clear Cache* option in the package settings.

## Features

### Build Results panel

BuildSystemTerminal provides some basic syntax highlighting for the Build Results panel. Most of the scopes being used should be defined in any colour scheme. However, some special syntax scopes are used:
* `message.success.console`
* `message.error.console`

This feature can be disabled with the boolean key `"panel_highlighting"` in the user settings.

Note that the Build Results panel is hidden by default. You can enable it by setting `"show_panel_on_build"` either in your user settings file or directly in the build system definition. If no errors occur the Build Results panel is closed when the terminal is closed. Use `"hide_panel_without_errors"` to change this behaviour.

### Input prompt

With BuildSystemInput you can use the additional boolean key `"prompt"` in you build systems. If set to `true`, an input panel will be shown, where the command can be edited before execution. This allows for great flexibility an can be used for passing additional arguments to the build system command.

### Terminal geometry

To specify the geometry of the terminal window, you can add definitions for the number of columns and lines to the user settings:

```json
"terminal_geometry": {
    "columns": 80,
    "lines": 20
}
```

## Caveats

### Buffering

If you run any script in the terminal you may find, that there is no output either in the terminal window itself or in the Build Results panel. However, when the script is finished you get all output at once. This is because of [buffering](http://www.pixelbeat.org/programming/stdio_buffering/). Buffering is an issue with which you have to deal if you want to use a terminal. There are hacks and workarounds (e. g. [`unbuffer`](https://linux.die.net/man/1/unbuffer) or [`stdbuf -i0 -o0 -e0`](https://linux.die.net/man/1/stdbuf) on Linux, [`winpty -Xallow-non-tty`](https://github.com/rprichard/winpty/issues/103) or [this workaround](https://gist.github.com/zhangyoufu/be36035e94b8c0dcb1239a3c8b07a3b1) on Windows) to prevent this issue. However, there is no simple solution working on all platforms. Therefore it is advisable to flush outputs manually in your programs. A simple example of flushing in Python looks like this:
```python
print("buffered")
print("flush buffer", flush=True)
```

You could also try to unbuffer your script in the command definition like this:
```json
"linux": {
    "cmd": ["unbuffer", "..."]
},
"windows": {
    "cmd": ["winpty.exe", "-Xallow-non-tty", "-Xplain", "..."]
}
```

If you have any issues with buffering, you may want disable the usage of pipes and tee completely. You can do this by setting the key `"tee"` to false in your build system definition. However, keep in mind that you will lose in-line error highlighting.

## Requirements

This package requires `tee`, which is a default Unix command. A [port for Windows](http://gnuwin32.sourceforge.net/packages/coreutils.htm) is included, which should work without any configuration. The path for tee can also be customized in the package user settings:

```json
"tee_path": {
    "linux": "/usr/bin/tee",
    "osx": "/usr/bin/tee",
    "windows": "C:\\Program Files (x86)\\GnuWin32\\bin\\tee.exe"
}
```

**Note:** Powershell seems to be a destined choice for Windows, since it has a built-in `tee` command. However, there are several issues with Powershell right now, which makes it unsuitable for the purpose of this package. Though, it is used implicitly for setting the terminal geometry correctly.

## Examples

Basic Python build system:
```json
{
    "target": "terminal_exec",
    "name": "Python Terminal",
    "selector": "source.python",
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
            "prompt": true
        },
        {
            "name": "Without Tee",
            "tee": false,
        },
        {
            "name": "Show Panel on Build",
            "show_panel_on_build": true
        }
    ]
}
```

### Alternative to this package

A simple build system with the same basic functionality but without the error processing may look like this:
```json
{
    "name": "Python Terminal",
    "selector": "source.python",
    "cmd": ["python", "$file_name"],
    "file_regex": "^\\s*File \"(...*?)\", line ([0-9]*)",
    "working_dir": "$file_path",
    "linux": {
        "shell_cmd": "xterm -e 'python \"$file_name\"; echo && echo Press ENTER to continue && read line && exit'"
    },
    "windows": {
        "shell_cmd": "start cmd /k \"python \"$file_name\" & pause && exit\""
    },
    "osx": {
        "shell_cmd": "xterm -e 'python \"$file_name\"; echo && echo Press ENTER to continue && read line && exit'"
    },
    "shell": true
}
```

You can also use other terminals. On Linux the Gnome Terminal is a nice choice:
```json
"shell_cmd": "gnome-terminal -e 'bash -c \"python $file_name; echo && echo Press ENTER to continue && read line && exit\"'"
```

