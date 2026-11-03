"""The demo plays through, fits a large-font terminal, and live mode never changes anything."""
import os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "modelplane_demo.py"
HEADER = re.compile(r"^(\d · |╭─)")


def play(*args, stdin="", env=None):
    e = dict(os.environ, MPDEMO_PACE="0", COLUMNS="80", LINES="25", **(env or {}))
    p = subprocess.run([sys.executable, str(APP), *args], input=stdin, capture_output=True, text=True,
                       env=e, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def screens(out):
    """Splits the output at each screen's first line."""
    found, cur = [], []
    for line in out.splitlines():
        if HEADER.match(line) and cur:
            found.append(cur)
            cur = []
        cur.append(line)
    return found + [cur]


def test_plays_through():
    out = play("--auto")
    for beat in ("1 · Fleet", "2 · Serve", "3 · Drain", "4 · Undrain", "What Modelplane does"):
        assert beat in out
    assert "Back on two clouds in about 4 minutes." in out


def test_fits_80_by_25():
    out = play("--auto")
    assert max(len(line) for line in out.splitlines()) <= 80
    for s in screens(out):
        assert len(s) <= 24, s[0]          # 80x25, with a row free for the cursor


def test_keys_from_a_pipe():
    assert "1 · Fleet" not in play(stdin="q")
    out = play(stdin=" " * 5 + "b" + " " * 40)   # back from the YAML to the fleet
    assert out.count("1 · Fleet") == 2 and out.count("What the ML team writes") == 2


def test_no_real_addresses():
    src = APP.read_text()
    assert "35.238.1.41" not in src and "34.133.83.252" not in src


def stub(tmp_path, body):
    (tmp_path / "kubectl").write_text("#!/bin/sh\necho \"$@\" >> \"$STUB_LOG\"\n" + body)
    (tmp_path / "kubectl").chmod(0o755)
    return {"PATH": f"{tmp_path}:{os.environ['PATH']}", "STUB_LOG": str(tmp_path / "log")}


def test_live_runs_reads_only(tmp_path):
    out = play("--auto", "--live", env=stub(tmp_path, "echo LIVE-ROW\n"))
    assert "LIVE-ROW" in out
    log = (tmp_path / "log").read_text()
    assert "patch" not in log and "get inferenceclasses" in log
    assert "inferencecluster.modelplane.ai/eks-us-east patched" in out     # the patches are replayed


def test_live_falls_back_to_the_recording(tmp_path):
    out = play("--auto", "--live", env=stub(tmp_path, "echo 'error: no context' >&2; exit 1\n"))
    assert "showing the recording" in out
    assert "eks-l4-1x-g6     1      23034Mi   EKS g6.xlarge, 1x NVIDIA L4" in out
