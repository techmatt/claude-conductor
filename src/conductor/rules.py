"""Preamble rules a hook can check (122): the executor's PreToolUse hook (`talk.pre_hook`)
hands every matched call here first, and a call that breaks one is denied with a one-line
reason the executor can act on. Enforcement beats text: a rule here needs no preamble line
(docs/hooks.md lists each rule as hooked or left as text, and why).

Each check is cheap (regexes over the call's input, no file reads) and errs toward letting a
call through: a pattern that can't be matched without real false positives stays text.

- **Shell edits**: `sed -i` / `perl -i`, a `-c` / `-e` interpreter string or heredoc script
  that writes a file, a heredoc redirected or `tee`d into a file, PowerShell's `-replace` or
  a here-string into `Set-Content` / `Out-File`. A heredoc that only feeds a command's stdin
  (`git commit -m "$(cat <<'EOF' …)"`, `git commit -F - <<EOF`) goes through.
- **Temp**: a file tool's path, or a shell command naming `%TEMP%`, `$TEMP`, `$env:TEMP`,
  `AppData\\Local\\Temp`, Git Bash's `/tmp`, a bare `mktemp` or a temp-file API. Claude Code's
  own task output (`Temp\\claude\\…\\tasks\\`) may still be read from the shell.
- **Worktree isolation**: an Agent (or Task) call or a Workflow script with `isolation: worktree`.
- **Helpers discarding**: a subagent's (`agent_id`) `git checkout`, `restore`, `switch`,
  `stash`, `clean` or `reset --hard`: the folder is the lead's.
- **Found in the preambles** (122): elevation (`sudo`, `runas`, `-Verb RunAs`); a Bash job
  left to die with its shell (`nohup`, a trailing `&`); waiting by `sleep` (5 s or more, or a
  poll loop); a background call with no `timeout`; a `CLAUDE.md` or memory file under
  `~/.claude`; a file tool writing the control folder (outside `reports/`); a commit in a
  readonly item.
"""

from __future__ import annotations

import re
from pathlib import Path

SHELL_TOOLS = ("Bash", "PowerShell")
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
AGENT_TOOLS = ("Agent", "Task")
SLEEP_LIMIT = 5         # seconds: a longer `sleep` is a wait, which `wait_for` does

# ---------------------------------------------------------------- shell edits
SED_I_RE = re.compile(r"\b(?:sed|perl)(?:\.exe)?\b[^|;&\n]*?\s(?:-[a-zA-Z]*i\S*|--in-place\S*)")
INTERP = r"\b(?:python[0-9.]*|py|pythonw|node|deno|perl|ruby|php)(?:\.exe)?"
INLINE_RE = re.compile(INTERP + r"\b[^|;&\n]*?\s(?:-c|-e|-E|--eval|-pe|-ne)\s+(.+)", re.DOTALL)
SCRIPT_STDIN_RE = re.compile(INTERP + r"\b(?:\s+-)?\s*<<")       # `python - <<'PY'`, `node <<EOF`
# What makes inline code a file write (Python, Node, Perl, Ruby, PowerShell/.NET).
WRITE_RE = re.compile(
    r"open\s*\([^)]*?,\s*['\"][wax]\+?b?t?['\"]|mode\s*=\s*['\"][wax]|\.write_(?:text|bytes)\s*\("
    r"|\b(?:write|append)File(?:Sync)?\s*\(|createWriteStream|\bshutil\.(?:copy|move)|\bos\.(?:replace|rename)\s*\("
    r"|\bFile\.(?:write|open)\s*\([^)]*['\"][wa]|\bIO\.write|open\s*\(\s*(?:my\s+)?\$?\w+\s*,\s*['\"]?[>+]"
    r"|WriteAll(?:Text|Lines|Bytes)", re.IGNORECASE)
HEREDOC_RE = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][\w.-]*)\1")
# A stdout redirect into a file (not `2>`, `>&2`, `/dev/null` or `$null`).
REDIRECT_RE = re.compile(r"(?<![<>&\d])>>?\s*(?!&|/dev/null\b|\$null\b|nul\b)([^\s;|&)]+)", re.IGNORECASE)
TEE_RE = re.compile(r"\btee\b(?:\s+-\S+)*\s+(?!/dev/null)[^\s;|&]")
PS_WRITE_RE = re.compile(r"\b(?:Set-Content|Add-Content|Out-File)\b|WriteAll(?:Text|Lines)", re.IGNORECASE)
PS_EDIT_RE = re.compile(r"-replace\b|@['\"]\s*$", re.IGNORECASE | re.MULTILINE)

# ---------------------------------------------------------------- temp
TEMP_RE = re.compile(
    r"%te?mp%|(?-i:\$\{?(?:TEMP|TMP|TMPDIR)\}?\b)|\$env:te?mp\b|appdata[\\/]+local[\\/]+temp\b|(?<![\w.~/$-])/tmp\b"
    r"|\bgettemp(?:path|filename)\b|\bnew-temporaryfile\b|\btempfile\.(?:mk|named|temporary|gettempdir)"
    r"|\bos\.tmpdir\s*\(|\bmktemp\b(?![^;|&\n]*(?:\s-p\s|--tmpdir=|\s\S*/))", re.IGNORECASE)
TASK_OUTPUT_RE = re.compile(r"[\\/]temp[\\/]+claude[\\/].*?[\\/]tasks[\\/]", re.IGNORECASE)

# ---------------------------------------------------------------- the rest
ISOLATION_RE = re.compile(r"isolation\s*:\s*['\"`]worktree['\"`]")
GIT = r"\bgit(?:\.exe)?(?:\s+-[cC]\s+(?:\"[^\"]*\"|'[^']*'|\S+)|\s+--[\w-]+(?:=\S+)?)*\s+"
DISCARD_RE = re.compile(GIT + r"(?:checkout|restore|switch|clean\b[^;|&\n]*\s-\w*f|reset\b[^;|&\n]*--hard"
                        r"|stash(?!\s+(?:list|show)\b))\b")
COMMIT_RE = re.compile(GIT + r"commit\b")
ELEVATE_RE = re.compile(r"(?:^|[\s;&|(])(?:sudo|gsudo|runas)(?:\.exe)?\s|-verb\s+['\"]?runas\b", re.IGNORECASE)
NOHUP_RE = re.compile(r"(?:^|[\s;&|(])(?:nohup|disown|setsid)\b")
TRAILING_AMP_RE = re.compile(r"(?<![&|>\\])&\s*$")
SLEEP_RE = re.compile(r"\b(?:sleep|start-sleep(?:\s+-s(?:econds)?)?)\s+(\d+(?:\.\d+)?)(m|h)?\b"
                      r"|\bstart-sleep\s+-m(?:illiseconds)?\s+(\d+)|\btimeout(?:\.exe)?\s+/t\s+(\d+)", re.IGNORECASE)
POLL_RE = re.compile(r"\b(?:until|while)\b[^\n]*\b(?:sleep|start-sleep)\b", re.IGNORECASE)


def strip_heredocs(command: str) -> tuple[str, list[tuple[str, str]]]:
    """The command without its heredoc bodies, and each heredoc as (the line that opened
    it, its body)."""
    lines, kept, docs, i = command.split("\n"), [], [], 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        i += 1
        for m in HEREDOC_RE.finditer(line):
            end, body = m.group(2), []
            while i < len(lines) and lines[i].strip() != end:
                body.append(lines[i])
                i += 1
            i += 1                          # the delimiter line
            docs.append((line, "\n".join(body)))
    return "\n".join(kept), docs


def shell_edit(command: str, tool: str = "Bash") -> str | None:
    """Why this shell command edits a file, or None."""
    outer, docs = strip_heredocs(command)
    if SED_I_RE.search(outer):
        return "edits a file in place with `sed -i` / `perl -i`"
    for opener, body in docs:
        # Inside `$(cat <<'EOF' …)` the body becomes an argument (a commit message); elsewhere
        # a stdout redirect or `tee` on its line puts the body in a file.
        if "$(" not in opener.split("<<", 1)[0] and (REDIRECT_RE.search(opener) or TEE_RE.search(opener)):
            return "writes a heredoc into a file"
        if SCRIPT_STDIN_RE.search(opener) and WRITE_RE.search(body):
            return "runs a heredoc script that writes a file"
    if (m := INLINE_RE.search(outer)) and WRITE_RE.search(m.group(1)):
        return "runs a `-c` / `-e` string that writes a file"
    if tool == "PowerShell" and PS_WRITE_RE.search(command) and PS_EDIT_RE.search(command):
        return "rewrites a file through `-replace` or a here-string piped to Set-Content / Out-File"
    return None


def temp_named(text: str) -> bool:
    return bool(TEMP_RE.search(TASK_OUTPUT_RE.sub("/", text)))


def long_sleep(command: str) -> bool:
    if POLL_RE.search(command):
        return True
    for m in SLEEP_RE.finditer(command):
        if m.group(1):
            secs = float(m.group(1)) * {"m": 60, "h": 3600}.get((m.group(2) or "").lower(), 1)
        else:
            secs = float(m.group(3)) / 1000 if m.group(3) else float(m.group(4))
        if secs >= SLEEP_LIMIT:
            return True
    return False


def _norm(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").lower()


def home_claude(path: str) -> bool:
    """A `CLAUDE.md` or memory file under `~/.claude` (config there is fine)."""
    p, root = _norm(path), _norm(str(Path.home() / ".claude"))
    if not p.startswith(root + "/"):
        return False
    return p.endswith("/claude.md") or "/memory/" in p + "/"


def check(event: dict, control: Path | None = None, readonly: bool = False) -> str | None:
    """The deny reason for a PreToolUse event, or None to let it run."""
    tool, ti = event.get("tool_name"), event.get("tool_input")
    if not isinstance(ti, dict):
        return None
    helper = isinstance(event.get("agent_id"), str) and bool(event["agent_id"].strip())
    if tool in SHELL_TOOLS and isinstance(cmd := ti.get("command"), str):
        return _shell(cmd, tool, ti, helper, readonly)
    if tool in FILE_TOOLS:
        path = str(ti.get("file_path") or ti.get("notebook_path") or "")
        if temp_named(path):
            return ("conductor: refused: scratch and output go in `out/<item>/` in your working directory, "
                    "never %TEMP%. Write it there instead.")
        if home_claude(path):
            return ("conductor: refused: `~/.claude` is config only, never memory or a CLAUDE.md. "
                    "Put the lesson in your report's `carry:` line.")
        if control is not None:
            from .talk import touch_paths
            if touch_paths(control, event):
                return ("conductor: refused: that is in the control folder, where your report is the only "
                        "file you may write; conductor reverts any other change and marks the item red.")
        return None
    if tool in AGENT_TOOLS and ti.get("isolation") == "worktree":
        return _isolation("subagent")
    if tool == "Workflow" and isinstance(script := ti.get("script"), str) and ISOLATION_RE.search(script):
        return _isolation("Workflow agent")
    return None


START_TOOL = "mcp__conductor__start"
TOKEN_RE = re.compile(r"""[^\s'"]+|"[^"]*"|'[^']*'""")
COUNTS_RE = re.compile(r"(?:^|\s)--?(?:seeds?|games?|count|first|runs)\b[=\s]*\d|\b\d+\.\.\d+\b"
                       r"|\s\d+\s+\d+(?:\s|$)", re.IGNORECASE)    # a flag, a range, or a positional `first count`
FARM_REMINDER = ("conductor: this item is `remote`: a seeded run of the project's native sim ({cmd}) runs on "
                 "the main box alone, and the idle remote workers get nothing. Run seeded batches through "
                 "`farm-seeds <first seed> <count> <out file> [args…]` (same output; `farm-seeds --at <commit> …` "
                 "for an older revision, such as a paired read's old side); a native run is the "
                 "fallback when it can't farm. Say in `friction:` why, if you keep running it directly.")


def native_run(event: dict, names: list[str], flags: list[str]) -> str | None:
    """A `remote` item's shell or `start` call running the project's `[remote] native`
    executable (`names`: its base name; or any executable under `out/`, a copy of it) with a
    seed range or game count (`flags`: what the native command puts before `{first}` and
    `{count}`): the command, else None. Never a denial: a native run is the documented
    fallback (124: aicomplete 249 ran every read as `sim.exe experiment … --games 4`)."""
    tool, ti = event.get("tool_name"), event.get("tool_input")
    if not isinstance(ti, dict) or tool not in (*SHELL_TOOLS, START_TOOL) or not isinstance(cmd := ti.get("command"), str):
        return None
    if "farm-seeds" in cmd:
        return None
    wanted = {n.lower() for n in names}
    exe = False
    for tok in TOKEN_RE.findall(strip_heredocs(cmd)[0]):
        path = _norm(tok.strip("'\""))
        base = path.rsplit("/", 1)[-1]
        stem = base[:-4] if base.endswith(".exe") else base
        if stem in wanted or (base.endswith(".exe") and (path.startswith("out/") or "/out/" in path)):
            exe = True
            break
    if not exe:
        return None
    seeded = COUNTS_RE.search(cmd) or any(re.search(rf"(?:^|\s){re.escape(f)}[=\s]*\d", cmd) for f in flags)
    return " ".join(cmd.split())[:160] if seeded else None


def _isolation(what: str) -> str:
    return (f"conductor: refused: no `isolation: worktree` for a {what}: it lands in the main checkout. "
            "Run it without isolation and split helpers by file.")


def _shell(cmd: str, tool: str, ti: dict, helper: bool, readonly: bool) -> str | None:
    if why := shell_edit(cmd, tool):
        return (f"conductor: refused: this {why}. Edit files with Write or Edit, and write a script to a "
                "file (in `out/<item>/`) before running it. Heredocs that only feed a command's stdin, "
                "like a commit message, are fine.")
    outer = strip_heredocs(cmd)[0]          # a heredoc body is data (a commit message), never run here
    if helper and DISCARD_RE.search(outer):
        return ("conductor: refused: a helper never runs git checkout, restore, switch, stash, clean or "
                "reset --hard: the folder is shared with the lead, whose unsaved edits it would discard. "
                "Report what you'd revert to the lead instead.")
    if COMMIT_RE.search(outer):             # its message may name %TEMP% or sleep: nothing more to check
        return ("conductor: refused: this item is readonly: commit nothing. Put what should land in `carry:`."
                if readonly else None)
    if temp_named(outer):
        return ("conductor: refused: this names %TEMP% (or /tmp, mktemp, a temp-file API). Scratch and "
                "output go in `out/<item>/` in your working directory; use that path.")
    if ELEVATE_RE.search(outer):
        return ("conductor: refused: nothing that needs elevation (UAC, admin installs, services): its prompt "
                "waits unseen. Take a no-admin route, or say in the report what needs Matt.")
    if tool == "Bash" and (NOHUP_RE.search(outer) or TRAILING_AMP_RE.search(outer.rstrip())):
        return ("conductor: refused: a Git Bash `&` / nohup job dies with its shell. Use `run_in_background` "
                "(with a `timeout`), or `start` (mcp__conductor__start) for one that may outrun 10 minutes.")
    if long_sleep(outer):
        return ("conductor: refused: no waiting with sleep or a poll loop: a message can't reach you. Run "
                "the job with `run_in_background` and wait with `wait_for` (mcp__conductor__wait_for).")
    if ti.get("run_in_background") and not isinstance(ti.get("timeout"), (int, float)):
        return ("conductor: refused: a background call needs a `timeout` sized to its estimate with margin "
                "(up to 600000; left out, Claude Code kills it at 10 minutes). Longer: use `start`.")
    return None
