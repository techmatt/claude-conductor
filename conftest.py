"""Every test run's temp folders live under the repo, in `.pytest-tmp/<pid>`, not
`%TEMP%\\pytest-of-<user>`: `C:\\Code` is under the Defender exclusion and `%TEMP%` is not,
and Defender averaged ~18% CPU scanning a run's folders there (aicomplete 170).

One folder per run (the controller's pid), so two runs at once never clear each other's;
a folder whose run is gone is removed at the next run's start. xdist hands each worker
`<run>/popen-gwN` itself, so a worker never picks its own. `--basetemp` given by hand wins.
"""
import os
import shutil
import stat
from pathlib import Path

import psutil
import pytest

TMP_ROOT = Path(__file__).parent / ".pytest-tmp"


def _writable(fn, path, _exc) -> None:
    """Git's object files are read-only, which stops `rmtree` on Windows."""
    try:
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    except OSError:
        pass


def _remove(path: Path) -> None:
    shutil.rmtree(path, onexc=_writable)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    if config.option.basetemp or hasattr(config, "workerinput"):
        return
    TMP_ROOT.mkdir(exist_ok=True)
    for old in TMP_ROOT.iterdir():
        if old.is_dir() and not (old.name.isdigit() and psutil.pid_exists(int(old.name))):
            _remove(old)
    config.option.basetemp = str(TMP_ROOT / str(os.getpid()))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """A green run leaves nothing; a red one keeps its failed tests' folders until the next run."""
    if exitstatus == 0 and not hasattr(session.config, "workerinput"):
        run = TMP_ROOT / str(os.getpid())
        if session.config.option.basetemp == str(run) and run.is_dir():
            _remove(run)
