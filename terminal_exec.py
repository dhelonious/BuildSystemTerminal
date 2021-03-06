# encoding: utf-8
# pylint: disable=W0201

import collections
import os
import shutil
import shlex
import subprocess
import threading
import time
import signal
import hashlib
import datetime

import sublime
import sublime_plugin
import Default


def log(msg):
    print("[BuildSystemTerminal] {}".format(msg))

def clear(folder):
    for _file in os.listdir(folder):
        file_path = os.path.join(folder, _file)
        try:
            if os.path.isfile(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            log(e)

def cmd_string(cmd):
    if isinstance(cmd, str):
        return cmd

    return " ".join([shlex.quote(c) for c in cmd])

def plugin_loaded():
    global CACHE_PATH

    CACHE_PATH = os.path.abspath(os.path.join(sublime.cache_path(), "BuildSystemTerminal"))

    # Create log path if it does not exist
    if not os.path.exists(CACHE_PATH):
        os.mkdir(CACHE_PATH)


class Terminal():
    def __init__(self, env, encoding="utf-8", tee=True, exit_method="prompt", cache_path="."):
        self.env = os.environ.copy()
        self.env.update(env)
        for key, value in self.env.items():
            self.env[key] = os.path.expandvars(value)

        self.encoding = encoding
        self.tee = tee
        self.exit_method = exit_method
        self.cache_path = cache_path

        self.proc = None
        self.logfile = ""

    def __del__(self):
        self.terminate()

    def run(self, cmd):

        hash_cmd = hashlib.sha1(cmd.encode()).hexdigest()
        date_time = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        self.logfile = os.path.join(self.cache_path, "{}_{}.log".format(hash_cmd, date_time))

        # Create empty log file
        if not os.path.exists(self.logfile):
            open(self.logfile, "a").close()

        settings = sublime.load_settings("BuildSystemTerminal.sublime-settings")
        tee_path = sublime.expand_variables(
            settings.get("tee_path")[sublime.platform()],
            {"packages": sublime.packages_path()},
        )

        if self.tee:
            cmd = "{cmd} 2>&1 | \"{tee}\" \"{log}\"".format(
                cmd=cmd,
                tee=tee_path,
                log=self.logfile,
            )

        terminal_geometry = settings.get("terminal_geometry")

        if sublime.platform() == "windows":
            # Use shell=True on Windows, so shell_cmd is passed through
            # with the correct escaping the startupinfo flag is used for hiding
            # the console window

            cmd = "{cmd} & {exit}".format(
                cmd=cmd,
                exit={
                    "prompt": "pause && exit",
                    "manual": "waitfor exit",
                    "auto": "exit",
                }[self.exit_method]
            )

            if terminal_geometry:
                cmd = "powershell -command \"[console]::WindowWidth={cols}; [console]::WindowHeight={rows}; [console]::BufferWidth=[console]::WindowWidth\" & {cmd}".format(
                    cmd=cmd,
                    rows=terminal_geometry["lines"],
                    cols=terminal_geometry["columns"],
                )

            terminal = "cmd"
            terminal_cmd = "start /wait {term} /k \"{cmd}\"".format(term=terminal, cmd=cmd)
            terminal_settings = {
                "startupinfo": subprocess.STARTUPINFO(),
                "shell": True,
            }
            terminal_settings["startupinfo"].dwFlags |= subprocess.STARTF_USESHOWWINDOW

        else:
            # Explicitly use /bin/bash on Unix. On OSX a login shell is used,
            # since the users expected env vars won't be setup otherwise.

            cmd = "{cmd}; {exit}".format(
                cmd=cmd,
                exit={
                    "prompt": "echo && echo Press ENTER to continue && read && exit",
                    "manual": "sleep infinity",
                    "auto": "exit",
                }[self.exit_method]
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
                "{term} -e \"{cmd}; read\"".format(term=terminal, cmd=cmd)
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

    def __init__(self, cmd, env, listener, exit_method, path="", tee=True):

        self.listener = listener
        self.killed = False
        self.start_time = time.time()

        # Set temporary PATH to locate executable in cmd
        if path:
            old_path = os.environ["PATH"]
            # The user decides in the build system whether he wants to append $PATH
            # or tuck it at the front: "$PATH;C:\\new\\path", "C:\\new\\path;$PATH"
            os.environ["PATH"] = os.path.expandvars(path)

        self.terminal = Terminal(
            env,
            encoding=self.listener.encoding,
            tee=tee,
            exit_method=exit_method,
            cache_path=CACHE_PATH
        )
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


class TerminalExecCommand(Default.exec.ExecCommand):
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
            input_prompt=False,
            terminal_exit=None,
            tee=True,
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

        self.settings = sublime.load_settings("BuildSystemTerminal.sublime-settings")
        self.output_view.assign_syntax(
            "Packages/BuildSystemTerminal/Panel.sublime-syntax"
            if self.settings.get("panel_highlighting") else syntax
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

        if show_panel_on_build or self.settings.get("show_panel_on_build"):
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

        exit_method = terminal_exit
        if not exit_method:
            exit_method = self.settings.get("terminal_exit")

        if input_prompt:
            self.window.show_input_panel(
                "$",
                cmd_string(cmd) if cmd else shell_cmd,
                lambda cmd: self._start_process(cmd, merged_env, exit_method, tee, **kwargs),
                None,
                None
            )
        else:
            self._start_process(cmd_string(cmd) if cmd else shell_cmd, merged_env, exit_method, tee, **kwargs)

    def _start_process(self, cmd, env, exit_method, tee, **kwargs):

        try:
            # Forward kwargs to AsyncTerminalProcess
            self.proc = AsyncTerminalProcess(cmd, env, self, exit_method, tee=tee, **kwargs)

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


class TerminalExecEventListener(Default.exec.ExecEventListener):
    def on_load(self, view):
        w = view.window()
        if w is not None:
            w.run_command("terminal_exec", {"update_phantoms_only": True})


class ClearTerminalExecCacheCommand(sublime_plugin.WindowCommand):
    def run(self):
        clear(CACHE_PATH)
        log("Cache cleared")
