import collections
import os
import shutil
import subprocess
import threading
import time
import signal
import importlib

import sublime
import sublime_plugin

DefaultExec = importlib.import_module("Default.exec")


CACHE_PATH = os.path.abspath(os.path.join(sublime.cache_path(), "BuildSystemTerminal"))


def plugin_loaded():
    # Create log path if it does not exist
    if not os.path.exists(CACHE_PATH):
        os.mkdir(CACHE_PATH)

def log(msg):
    print("[BuildSystemTerminal] {}".format(msg))

def cmd_string(cmd):
    if isinstance(cmd, str):
        return cmd

    shell_cmd = []
    for part in cmd:
        if " " in part:
            shell_cmd.append("\"{}\"".format(part))
        else:
            shell_cmd.append(part)

    return " ".join(shell_cmd)


class Terminal():
    def __init__(self, env, encoding="utf-8"):
        self.proc = None
        self.encoding = encoding

        self.env = os.environ.copy()
        self.env.update(env)
        for key, value in self.env.items():
            self.env[key] = os.path.expandvars(value)

        # TODO: Use hashing for multiple files
        self.logfile = os.path.join(CACHE_PATH, "terminal_exec.log")

        # Remove log file if its already exists
        if os.path.exists(self.logfile):
            os.remove(self.logfile)

    def __del__(self):
        self.terminate()

    def run(self, cmd):

        # Create empty log file
        if not os.path.exists(self.logfile):
            open(self.logfile, "a").close()

        settings = sublime.load_settings("BuildSystemTerminal.sublime-settings")
        tee_path = sublime.expand_variables(
            settings.get("tee_path")[sublime.platform()],
            {"packages": sublime.packages_path()},
        )

        shell_cmd = "{cmd} 2>&1 | \"{tee}\" \"{log}\"".format(
            cmd=cmd,
            tee=tee_path,
            log=self.logfile,
        )

        terminal_geometry = settings.get("terminal_geometry")

        if sublime.platform() == "windows":
            # Use shell=True on Windows, so shell_cmd is passed through
            # with the correct escaping the startupinfo flag is used for hiding
            # the console window

            shell_cmd = "{} & {}".format(
                shell_cmd,
                "waitfor exit" if settings.get("keep_terminal_open") else "pause && exit",
            )
            if terminal_geometry:
                shell_cmd = "powershell -command \"[console]::WindowWidth={cols}; [console]::WindowHeight={rows}; [console]::BufferWidth=[console]::WindowWidth\" & {cmd}".format(
                    cmd=shell_cmd,
                    rows=terminal_geometry["lines"],
                    cols=terminal_geometry["columns"],
                )

            terminal = "cmd"
            terminal_cmd = "start /wait {term} /k \"{cmd}\"".format(term=terminal, cmd=shell_cmd)
            terminal_settings = {
                "startupinfo": subprocess.STARTUPINFO(),
                "shell": True,
            }
            terminal_settings["startupinfo"].dwFlags |= subprocess.STARTF_USESHOWWINDOW

        else:
            # Explicitly use /bin/bash on Unix. On OSX a login shell is used,
            # since the users expected env vars won't be setup otherwise.

            shell_cmd = "{}; {}".format(
                shell_cmd,
                "sleep infinity" if settings.get("keep_terminal_open") else "echo && echo Press ENTER to continue && read && exit"
            )

            terminal = "xterm"
            if terminal_geometry:
                terminal = "{term} -geometry {cols}x{rows}".format(
                    term=terminal,
                    rows=terminal_geometry["lines"],
                    cols=terminal_geometry["columns"],
                )
            terminal_cmd = [
                "/usr/bin/env",
                "bash -l" if sublime.platform() == "osx" else "bash",
                "-c",
                "{term} -e \"{cmd}; read\"".format(term=terminal, cmd=shell_cmd)
            ]
            terminal_settings = {
                "preexec_fn": os.setsid,
                "shell": False,
            }

        self.proc = subprocess.Popen(
            terminal_cmd,
            stdin=subprocess.PIPE,
            env=self.env,
            **terminal_settings
        )

    def terminate(self):
        if self.running:
            if sublime.platform() == "windows":
                # terminate would not kill process opened by the shell cmd.exe,
                # it will only kill cmd.exe leaving the child running
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                subprocess.Popen(
                    "taskkill /PID {:d} /T /F".format(self.proc.pid),
                    startupinfo=startupinfo
                )
            else:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.terminate()

        self.clear_cache()

    def clear_cache(self):
        if os.path.exists(self.logfile):
            try:
                os.remove(self.logfile)
            except PermissionError:
                pass

    @property
    def running(self):
        if self.proc:
            return self.proc.poll() is None
        return False

    @property
    def stdout(self):
        if os.path.exists(self.logfile):
            with open(self.logfile, encoding=self.encoding) as stdout:
                stdout.seek(0,2)
                while self.running:
                    line = stdout.readline()
                    if not line:
                        time.sleep(.1)
                        continue

                    yield line

                self.clear_cache()


class AsyncTerminalProcess():
    """Encapsulates subprocess.Popen

    Forwarding of stdout to a supplied TerminalProcessListener (on a separate
    thread).
    """

    def __init__(self, cmd, env, listener, path=""):

        self.listener = listener
        self.killed = False
        self.start_time = time.time()

        # Set temporary PATH to locate executable in cmd
        if path:
            old_path = os.environ["PATH"]
            # The user decides in the build system whether he wants to append $PATH
            # or tuck it at the front: "$PATH;C:\\new\\path", "C:\\new\\path;$PATH"
            os.environ["PATH"] = os.path.expandvars(path)

        self.terminal = Terminal(env, encoding=self.listener.encoding)
        self.terminal.run(cmd)

        if path:
            os.environ["PATH"] = old_path

        threading.Thread(target=self._process_output).start()

    def kill(self):
        if not self.killed:
            self.killed = True
            self.terminal.terminate()
            self.listener = None

    def poll(self):
        return self.terminal.running

    def _process_output(self):
        for data in self.terminal.stdout:
            if data:
                if self.listener:
                    self.listener.on_data(self, data)
        if self.listener:
            self.listener.on_finished(self)


class TerminalExecCommand(DefaultExec.ExecCommand):
    BLOCK_SIZE = 2**14
    text_queue = collections.deque()
    text_queue_proc = None
    text_queue_lock = threading.Lock()

    proc = None

    errs_by_file = {}
    phantom_sets_by_buffer = {}
    show_errors_inline = True

    def run(
            self,
            cmd=None,
            shell_cmd=None,
            file_regex="",
            line_regex="",
            working_dir="",
            encoding="utf-8",
            env={},
            quiet=False,
            kill=False,
            update_phantoms_only=False,
            hide_phantoms_only=False,
            word_wrap=True,
            syntax="Packages/Text/Plain text.tmLanguage",
            show_panel_on_build=False,
            prompt=False,
            # Catches "path" and "shell"
            **kwargs):

        if update_phantoms_only:
            if self.show_errors_inline:
                self.update_phantoms()
            return
        if hide_phantoms_only:
            self.hide_phantoms()
            return

        # clear the text_queue
        with self.text_queue_lock:
            self.text_queue.clear()
            self.text_queue_proc = None

        if kill:
            if self.proc:
                self.proc.kill()
                self.proc = None
                self.append_string(None, "[Cancelled]")
            return

        if not shell_cmd and not cmd:
            raise ValueError("shell_cmd or cmd is required")

        if shell_cmd and not isinstance(shell_cmd, str):
            raise ValueError("shell_cmd must be a string")

        if not hasattr(self, "output_view"):
            # Try not to call get_output_panel until the regexes are assigned
            self.output_view = self.window.create_output_panel("exec")

        # Default the to the current files directory if no working directory was given
        if working_dir == "" and self.window.active_view() and self.window.active_view().file_name():
            working_dir = os.path.dirname(self.window.active_view().file_name())

        self.output_view.settings().set("result_file_regex", file_regex)
        self.output_view.settings().set("result_line_regex", line_regex)
        self.output_view.settings().set("result_base_dir", working_dir)
        self.output_view.settings().set("word_wrap", word_wrap)
        self.output_view.settings().set("line_numbers", False)
        self.output_view.settings().set("gutter", False)
        self.output_view.settings().set("scroll_past_end", False)

        settings = sublime.load_settings("BuildSystemTerminal.sublime-settings")
        self.output_view.assign_syntax(
            "Packages/BuildSystemTerminal/Console.sublime-syntax"
            if settings.get("syntax") else syntax
        )

        # Call create_output_panel a second time after assigning the above
        # settings, so that it'll be picked up as a result buffer
        self.window.create_output_panel("exec")

        self.encoding = encoding
        self.quiet = quiet

        self.proc = None
        if not self.quiet:
            print("Running {}".format(cmd_string(cmd) if cmd else shell_cmd))
            sublime.status_message("Building")

        if show_panel_on_build or settings.get("show_panel_on_build"):
            self.window.run_command("show_panel", {"panel": "output.exec"})

        self.hide_phantoms()
        self.show_errors_inline = sublime.load_settings("Preferences.sublime-settings").get("show_errors_inline", True)

        merged_env = env.copy()
        if self.window.active_view():
            user_env = self.window.active_view().settings().get("build_env")
            if user_env:
                merged_env.update(user_env)

        # Change to the working dir, rather than spawning the process with it,
        # so that emitted working dir relative path names make sense
        if working_dir != "":
            os.chdir(working_dir)

        self.debug_text = ""
        if shell_cmd:
            self.debug_text += "[shell_cmd: " + shell_cmd + "]\n"
        else:
            self.debug_text += "[cmd: " + str(cmd) + "]\n"
        self.debug_text += "[dir: " + str(os.getcwd()) + "]\n"
        if "PATH" in merged_env:
            self.debug_text += "[path: " + str(merged_env["PATH"]) + "]"
        else:
            self.debug_text += "[path: " + str(os.environ["PATH"]) + "]"

        if prompt:
            self.window.show_input_panel(
                "$",
                cmd_string(cmd) if cmd else shell_cmd,
                lambda cmd: self._start_process(cmd, merged_env, **kwargs),
                None,
                None
            )
        else:
            self._start_process(cmd_string(cmd) if cmd else shell_cmd, merged_env, **kwargs)

    def _start_process(self, cmd, env, **kwargs):
        try:
            # Forward kwargs to AsyncTerminalProcess
            self.proc = AsyncTerminalProcess(cmd, env, self, **kwargs)

            with self.text_queue_lock:
                self.text_queue_proc = self.proc

        except Exception as e:
            log("An error occured while running {}. See the Build Results panel for details.".format(cmd))
            self.append_string(None, str(e) + "\n")
            self.append_string(None, self.debug_text + "\n")
            if not self.quiet:
                self.append_string(None, "[Finished]")

    def finish(self, proc):
        if proc != self.proc:
            return

        elapsed = time.time() - proc.start_time
        errs = self.output_view.find_all_results()
        if len(errs) == 0:
            sublime.status_message("Build finished")
            if not self.quiet:
                self.append_string(proc, "[Finished in {:.1f}]".format(elapsed))
            if sublime.load_settings("BuildSystemTerminal.sublime-settings").get("hide_panel_without_errors"):
                self.window.run_command("hide_panel", {"panel": "output.exec"})
        else:
            sublime.status_message("Build finished with {} errors".format(len(errs)))
            if not self.quiet:
                self.append_string(proc, "[Finished in {:.1f} with {} errors]\n".format(elapsed, len(errs)))
                self.append_string(proc, self.debug_text)


class TerminalExecEventListener(DefaultExec.ExecEventListener):
    def on_load(self, view):
        w = view.window()
        if w is not None:
            w.run_command("terminal_exec", {"update_phantoms_only": True})


class ClearTerminalExecCacheCommand(sublime_plugin.WindowCommand):
    def run(self):
        try:
            shutil.rmtree(CACHE_PATH)
            log("Cache cleared")
        except Exception as e:
            log(e)
