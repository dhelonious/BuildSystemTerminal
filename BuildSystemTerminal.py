import collections
import functools
import html
import os
import subprocess
import threading
import time
import signal

import sublime
import sublime_plugin


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

class TerminalProcessListener(object):
    def on_data(self, proc, data):
        pass

    def on_finished(self, proc):
        pass


TerminalIndicators = collections.namedtuple("TerminalIndicators", ["start", "end", "error"])


class Terminal():
    def __init__(self, env, encoding="utf-8"):
        self.proc = None
        self.encoding = encoding

        self.env = os.environ.copy()
        self.env.update(env)
        for key, value in self.env.items():
            self.env[key] = os.path.expandvars(value)

        # TODO: Configurable log path
        self.logpath = os.path.join(sublime.cache_path(), "BuildSystemTerminal")
        self.logfile = os.path.join(self.logpath, "terminal_exec.log")

        self.indicators = TerminalIndicators(
            "[terminal_exec_start]",
            "[terminal_exec_end]",
            "[terminal_exec_error]",
        )

        # Create log path if it does not exist
        if not os.path.exists(self.logpath):
            os.makedirs(self.logpath)

        # Remove log file if its already exists
        if os.path.exists(self.logfile):
            os.remove(self.logfile)

    def __del__(self):
        self.terminate()

    def run(self, cmd):

        # Create empty log file
        if not os.path.exists(self.logfile):
            open(self.logfile, "a").close()

        shell_cmd = "echo {start} && ({cmd} && echo {end} || echo {error})".format(
            cmd=cmd,
            start=self.indicators.start,
            end=self.indicators.end,
            error=self.indicators.error,
        )
        shell_cmd = "{cmd} 2>&1 | tee \"{log}\"".format(
            cmd=shell_cmd,
            log=self.logfile,
        )

        if sublime.platform() == "windows":
            # Use shell=True on Windows, so shell_cmd is passed through
            # with the correct escaping the startupinfo flag is used for hiding
            # the console window

            shell_cmd = "{} & pause && exit".format(shell_cmd)
            terminal_cmd = "start /wait cmd /k \"{}\"".format(shell_cmd)
            terminal_settings = {
                "startupinfo": subprocess.STARTUPINFO(),
                "shell": True,
            }
            terminal_settings["startupinfo"].dwFlags |= subprocess.STARTF_USESHOWWINDOW

        elif sublime.platform() == "osx":
            # Use a login shell on OSX, otherwise the users expected
            # env vars won't be setup

            shell_cmd = "{}; echo && Press ENTER to continue && read line && exit".format(shell_cmd)
            terminal_cmd = [
                "/usr/bin/env",
                "bash",
                "-l",
                "-c",
                "xterm -e \"{}\"".format(shell_cmd)
            ]
            terminal_settings = {
                "preexec_fn": os.setsid,
                "shell": False,
            }

        elif sublime.platform() == "linux":
            # Explicitly use /bin/bash on Linux, to keep Linux and OSX as
            # similar as possible. A login shell is explicitly not used for
            # linux, as it's not required

            shell_cmd = "{}; echo && Press ENTER to continue && read line && exit".format(shell_cmd)
            terminal_cmd = [
                "/usr/bin/env",
                "bash",
                "-c",
                "xterm -e \"{}\"".format(shell_cmd)
            ]
            terminal_settings = {
                "preexec_fn": os.setsid,
                "shell": False,
            }

        self.proc = subprocess.Popen(
            terminal_cmd,
            stdout=subprocess.PIPE, # TODO: necessary?
            stderr=subprocess.PIPE, # TODO: necessary?
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

        if os.path.exists(self.logfile):
            os.remove(self.logfile)

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
                while self.running: # TODO: Check if this works on linux
                    line = stdout.readline()
                    if not line:
                        time.sleep(.1)
                        continue
                    elif any([term in line for term in (self.indicators.end, self.indicators.error)]):
                        yield None
                        break

                    yield line

            os.remove(self.logfile)


class AsyncTerminalProcess(object):
    """
    Encapsulates subprocess.Popen, forwarding stdout to a supplied
    TerminalProcessListener (on a separate thread)
    """

    def __init__(self, cmd, shell_cmd, env, listener, path=""):

        if not shell_cmd and not cmd:
            raise ValueError("shell_cmd or cmd is required")

        if shell_cmd and not isinstance(shell_cmd, str):
            raise ValueError("shell_cmd must be a string")

        self.listener = listener
        self.killed = False
        self.start_time = time.time()

        # Set temporary PATH to locate executable in cmd
        if path:
            old_path = os.environ["PATH"]
            # The user decides in the build system whether he wants to append $PATH
            # or tuck it at the front: "$PATH;C:\\new\\path", "C:\\new\\path;$PATH"
            os.environ["PATH"] = os.path.expandvars(path)

        # TODO: Terminal
        self.terminal = Terminal(env, encoding=self.listener.encoding)
        self.terminal.run(cmd_string(cmd) if cmd else shell_cmd)

        if path:
            os.environ["PATH"] = old_path

        threading.Thread(target=self.process_output).start()

    def kill(self):
        if not self.killed:
            self.killed = True
            self.terminal.terminate()
            self.listener = None

    def poll(self):
        return self.terminal.running

    def process_output(self):
        for data in self.terminal.stdout:
            if data:
                if self.listener:
                    self.listener.on_data(self, data)
            else:
                if self.listener:
                    self.listener.on_finished(self)
                break


class TerminalExecCommand(sublime_plugin.WindowCommand, TerminalProcessListener):
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
        self.output_view.assign_syntax(syntax)

        # Call create_output_panel a second time after assigning the above
        # settings, so that it'll be picked up as a result buffer
        self.window.create_output_panel("exec")

        self.encoding = encoding
        self.quiet = quiet

        self.proc = None
        if not self.quiet:
            print("Running {}".format(cmd_string(cmd) if cmd else shell_cmd))
            sublime.status_message("Building")

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

        try:
            # Forward kwargs to AsyncTerminalProcess
            self.proc = AsyncTerminalProcess(cmd, shell_cmd, merged_env, self, **kwargs)

            with self.text_queue_lock:
                self.text_queue_proc = self.proc

        except Exception as e:
            self.append_string(None, str(e) + "\n")
            self.append_string(None, self.debug_text + "\n")
            if not self.quiet:
                self.append_string(None, "[Finished]")

    def is_enabled(self, kill=False, **kwargs):
        if kill:
            return (self.proc is not None) and self.proc.poll()
        else:
            return True

    def append_string(self, proc, string):
        was_empty = False
        with self.text_queue_lock:
            if proc != self.text_queue_proc and proc:
                # a second call to exec has been made before the first one
                # finished, ignore it instead of intermingling the output.
                proc.kill()
                return

            if len(self.text_queue) == 0:
                was_empty = True
                self.text_queue.append("")

            available = self.BLOCK_SIZE - len(self.text_queue[-1])

            if len(string) < available:
                cur = self.text_queue.pop()
                self.text_queue.append(cur + string)
            else:
                self.text_queue.append(string)

        if was_empty:
            sublime.set_timeout(self.service_text_queue, 0)

    def service_text_queue(self):
        is_empty = False
        with self.text_queue_lock:
            if len(self.text_queue) == 0:
                # this can happen if a new build was started, which will clear
                # the text_queue
                return

            characters = self.text_queue.popleft()
            is_empty = (len(self.text_queue) == 0)

        self.output_view.run_command(
            "append",
            {
                "characters": characters,
                "force": True,
                "scroll_to_end": True,
            }
        )

        if self.show_errors_inline and characters.find("\n") >= 0:
            errs = self.output_view.find_all_results_with_text()
            errs_by_file = {}
            for file, line, column, text in errs:
                if file not in errs_by_file:
                    errs_by_file[file] = []
                errs_by_file[file].append((line, column, text))
            self.errs_by_file = errs_by_file

            self.update_phantoms()

        if not is_empty:
            sublime.set_timeout(self.service_text_queue, 1)

    def finish(self, proc):

        if proc != self.proc:
            return

        elapsed = time.time() - proc.start_time
        errs = self.output_view.find_all_results()
        if len(errs) == 0:
            sublime.status_message("Build finished")
            if not self.quiet:
                self.append_string(proc, "[Finished in {:.1f}]".format(elapsed))
        else:
            sublime.status_message("Build finished with {} errors".format(len(errs)))
            if not self.quiet:
                self.append_string(proc, "[Finished in {:.1f} with {} errors]\n".format(elapsed, len(errs)))
                self.append_string(proc, self.debug_text)

    def on_data(self, proc, data):
        # Normalize newlines, Sublime Text always uses a single \n separator
        # in memory.
        data = data.replace("\r\n", "\n").replace("\r", "\n")

        self.append_string(proc, data)

    def on_finished(self, proc):
        sublime.set_timeout(functools.partial(self.finish, proc), 0)

    def update_phantoms(self):
        stylesheet = """
            <style>
                div.error-arrow {
                    border-top: 0.4rem solid transparent;
                    border-left: 0.5rem solid color(var(--redish) blend(var(--background) 30%));
                    width: 0;
                    height: 0;
                }
                div.error {
                    padding: 0.4rem 0 0.4rem 0.7rem;
                    margin: 0 0 0.2rem;
                    border-radius: 0 0.2rem 0.2rem 0.2rem;
                }

                div.error span.message {
                    padding-right: 0.7rem;
                }

                div.error a {
                    text-decoration: inherit;
                    padding: 0.35rem 0.7rem 0.45rem 0.8rem;
                    position: relative;
                    bottom: 0.05rem;
                    border-radius: 0 0.2rem 0.2rem 0;
                    font-weight: bold;
                }
                html.dark div.error a {
                    background-color: #00000018;
                }
                html.light div.error a {
                    background-color: #ffffff18;
                }
            </style>
        """

        for file, errs in self.errs_by_file.items():
            view = self.window.find_open_file(file)
            if view:

                buffer_id = view.buffer_id()
                if buffer_id not in self.phantom_sets_by_buffer:
                    phantom_set = sublime.PhantomSet(view, "exec")
                    self.phantom_sets_by_buffer[buffer_id] = phantom_set
                else:
                    phantom_set = self.phantom_sets_by_buffer[buffer_id]

                phantoms = []

                for line, column, text in errs:
                    pt = view.text_point(line - 1, column - 1)
                    phantoms.append(sublime.Phantom(
                        sublime.Region(pt, view.line(pt).b),
                        """<body id=inline-error>{}
                               <div class=\"error-arrow\"></div><div class=\"error\">
                                   <span class=\"message\">{}</span>
                                   <a href=hide>{}</a>
                               </div>
                           </body>""".format(stylesheet, html.escape(text, quote=False), chr(0x00D7)),
                        sublime.LAYOUT_BELOW,
                        on_navigate=self.on_phantom_navigate
                    ))

                phantom_set.update(phantoms)

    def hide_phantoms(self):
        for file, _ in self.errs_by_file.items():
            view = self.window.find_open_file(file)
            if view:
                view.erase_phantoms("exec")

        self.errs_by_file = {}
        self.phantom_sets_by_buffer = {}
        self.show_errors_inline = False

    def on_phantom_navigate(self, url):
        self.hide_phantoms()


class TerminalExecEventListener(sublime_plugin.EventListener):
    def on_load(self, view):
        w = view.window()
        if w is not None:
            w.run_command("terminal_exec", {"update_phantoms_only": True})