#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["rich>=13"]
# ///
"""Modelplane booth demo: one model on two clouds, one endpoint, and a cluster drained for maintenance.

    modelplane-demo              the demo: space or Enter at every step
    modelplane-demo --auto       plays itself; add --loop for a booth screen
    modelplane-demo --live       runs the read-only commands for real on your kube context

Keys: space, Enter or the right arrow moves on. b or the left arrow goes back a screen. q quits.
MPDEMO_PACE scales the typing and pauses: 0 skips them, 2 is half speed.

Every command and output is replayed from a two-cloud Modelplane fleet, as Modelplane v0.6 prints it.
Nothing touches a cluster unless you pass --live, and even then nothing is changed.
"""
import argparse, json, os, select, shutil, subprocess, sys, tempfile, textwrap, time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

VERSION = "0.1.0"
PACE = float(os.environ.get("MPDEMO_PACE", "1"))
COLS, ROWS = 80, 25          # what every screen fits in, so the font can be large
WRAP = 68                    # streamed answers wrap here
con = Console(highlight=False)

CMD, OUT, DIM, SAY, BEAT = "bold cyan", "", "grey62", "bold green", "bold cyan"

# The files the commands read. Live mode writes them to a scratch directory and runs there.
FILES = {
    "columns/classes.txt":
        "NAME             GPUS                     MEMORY                                   DESCRIPTION\n"
        ".metadata.name   .spec.devices[0].count   .spec.devices[0].capacity.memory.value   .spec.description\n",
    "columns/clusters.txt":
        "NAME             SOURCE                 REGION                                   READY\n"
        ".metadata.name   .spec.cluster.source   .metadata.labels.modelplane\\.ai/region   "
        ".status.conditions[?(@.type==\"Ready\")].status\n",
    "model-deployment.yaml": """\
kind: ModelDeployment
metadata: {name: qwen3-8b, namespace: ml-team}
spec:
  replicas: 2
  template:
    spec:
      engines:
      - name: qwen3-8b
        members:
        - role: Standalone
          nodeSelector:
            devices:
            - name: gpu
              count: 1
              selectors:
              - cel: |
                  device.capacity["gpu.nvidia.com"].memory
                    .compareTo(quantity("20Gi")) >= 0
          template: … vllm/vllm-openai, --model=Qwen/Qwen3-8B-FP8
""",
}

ANSWER_GATEWAY = ("An inference gateway enables the integration of AI models into existing systems by handling "
                  "inference requests. It processes input data, routes it to the appropriate model, and returns "
                  "the results to the application.")
ANSWER_DRAIN = ("Draining a cluster removes all workloads and pods from the nodes to prepare them for maintenance "
                "or decommissioning. It ensures that no active tasks are running on the nodes before they are "
                "taken offline.")


def curl(question):
    """The streamed request, as shown, and the command live mode runs."""
    shown = ["curl -N $GATEWAY/chat/completions \\",
             '    -H "Authorization: Bearer $KEY" \\',
             '    -d \'{"model": "ml-team/qwen3-8b", "stream": true,',
             '         "messages": [{"role": "user", "content":',
             f'         "{question}"}}]}}\'']
    body = json.dumps({"model": "ml-team/qwen3-8b", "stream": True,
                       "messages": [{"role": "user", "content": question}]})
    live = ['curl', '-sN', '--max-time', '30', os.environ.get("GATEWAY", "") + "/chat/completions",
            '-H', 'Authorization: Bearer ' + os.environ.get("KEY", ""), '-H', 'Content-Type: application/json',
            '-d', body]
    return shown, live


MR_HEAD = "NAME             CLUSTER          SERVING   SYNCED   READY   AGE"
EKS_PODS = ["qwen3-8b-fd54a-epp-8f46f9f8b-49pfh              1/1   Running   0     6m44s",
            "qwen3-8b-fd54a-qwen3-8b-67897-f758b8586-rktlw   1/1   Running   0     6m44s"]
NO_PODS = ["No resources found in mp-ml-team-51733 namespace."]
EP = "modelendpoint.modelplane.ai/qwen3-8b-"
MR = "modelreplica.modelplane.ai/qwen3-8b-"


def watch_frame(endpoints, pods, replicas):
    return [("kubectl get modelendpoints -n ml-team -o name", "1 traffic stops", [EP + e for e in endpoints]),
            ("kubectl --context eks-us-east get pods --no-headers", "2 the engine", pods),
            ("kubectl get modelreplicas -n ml-team -o name", "3 the replica", [MR + r for r in replicas])]


def spark(before, after, width=22):
    return (before + after).ljust(width)


# A screen is a beat, a title and its steps. A card is a screen of its own.
SCREENS = [
    {"card": "open"},
    {"beat": "1 · Fleet", "title": "What the platform team offers", "steps": [
        ("cmd", ["kubectl get inferenceclasses \\", "    -o custom-columns-file=columns/classes.txt"], [
            "NAME             GPUS   MEMORY    DESCRIPTION",
            "eks-l4-1x-g6     1      23034Mi   EKS g6.xlarge, 1x NVIDIA L4",
            "gke-a100-40-1x   1      40960Mi   GKE a2-highgpu-1g, 1x NVIDIA A100 40GB",
            "gke-l4-1x-g2     1      23034Mi   GKE g2-standard-4, 1x NVIDIA L4"],
         "kubectl get inferenceclasses -o custom-columns-file=columns/classes.txt"),
        ("cmd", ["kubectl get inferenceclusters \\", "    -o custom-columns-file=columns/clusters.txt"], [
            "NAME             SOURCE   REGION       READY",
            "eks-us-east      EKS      us-east      True",
            "gke-us-central   GKE      us-central   True"],
         "kubectl get inferenceclusters -o custom-columns-file=columns/clusters.txt"),
        ("cmd", ["kubectl get inferencegateway"], [
            "NAME     CLUSTER          ADDRESS        SYNCED   READY   AGE",
            "public   gke-us-central   203.0.113.41   True     True    26h"],
         "kubectl get inferencegateway"),
        ("say", "GPUs on two clouds, behind one API."),
    ]},
    {"beat": "2 · Serve", "title": "What the ML team writes", "steps": [
        ("cmd", ["cat model-deployment.yaml   # trimmed"], FILES["model-deployment.yaml"].rstrip().splitlines(),
         "cat model-deployment.yaml"),
        ("say", "The GPU it needs, in CEL. No cluster name."),
    ]},
    {"beat": "2 · Serve", "title": "One model, two clouds, one endpoint", "steps": [
        ("cmd", ["kubectl get modelreplicas -n ml-team"], [
            MR_HEAD,
            "qwen3-8b-a02c0   gke-us-central   True      True     True    4m49s",
            "qwen3-8b-fd54a   eks-us-east      True      True     True    4m49s"],
         "kubectl get modelreplicas -n ml-team"),
        ("stream", *curl("What does an inference gateway do? Two sentences."), ANSWER_GATEWAY),
        ("say", "One OpenAI-compatible endpoint, wherever the replicas run."),
    ]},
    {"beat": "3 · Drain", "title": "Take eks-us-east out for maintenance", "steps": [
        ("cmd", ["kubectl patch inferencecluster eks-us-east --type json -p \\",
                 "    '[{\"op\": \"add\", \"path\": \"/spec/taints/-\",",
                 "      \"value\": {\"key\": \"modelplane.ai/maintenance\",",
                 "                \"effect\": \"NoExecute\"}}]'"],
         ["inferencecluster.modelplane.ai/eks-us-east patched"], None),
        ("watch", [("0:00", watch_frame(["a02c0", "fd54a"], EKS_PODS, ["a02c0", "fd54a"])),
                   ("0:02", watch_frame(["a02c0"], EKS_PODS, ["a02c0", "fd54a"])),
                   ("0:07", watch_frame(["a02c0"], NO_PODS, ["a02c0", "fd54a"])),
                   ("0:12", watch_frame(["a02c0"], NO_PODS, ["a02c0"]))]),
        ("say", "Traffic stops first, then the engine, then the replica."),
    ]},
    {"beat": "3 · Drain", "title": "Same request, same endpoint", "steps": [
        ("stream", curl("What does draining a cluster do? Two sentences.")[0], None, ANSWER_DRAIN),
        ("note", "No GPU pool is free for the second replica,"),
        ("note", "so the deployment runs one short, and says so:"),
        ("out", ["ReplicasScheduled: Scheduled 1 of 2 replicas (checked 2 clusters)",
                 "ReplicasServing:   1 of 2 replicas serving"]),
        ("say", "Same endpoint, one cluster fewer."),
    ]},
    {"beat": "3 · Drain", "title": "The drain in traffic", "steps": [
        ("note", "In the background: an inference client, 2 streams of chat requests"),
        ("traffic",),
        ("say", "Every request since the taint got a 200."),
    ]},
    {"beat": "4 · Undrain", "title": "Bring eks-us-east back", "steps": [
        ("cmd", ["kubectl patch inferencecluster eks-us-east --type json -p \\",
                 "    '[{\"op\": \"remove\", \"path\": \"/spec/taints/0\"}]'"],
         ["inferencecluster.modelplane.ai/eks-us-east patched"], None),
        ("cmd", ["kubectl get modelreplicas -n ml-team"], [
            MR_HEAD,
            "qwen3-8b-a02c0   gke-us-central   True      True     True    5h24m",
            "qwen3-8b-fd54a   eks-us-east                                 1s"], None),
        ("note", "Its engine pulls the weights and starts again, about 4 minutes."),
        ("note", "The ML team changed nothing."),
        ("say", "Back on two clouds in about 4 minutes."),
    ]},
    {"card": "close"},
]

class Back(Exception):
    pass


class Quit(Exception):
    pass


class Keys:
    """Reads one key at a time: cbreak mode on a terminal, plain reads from a pipe."""

    def __init__(self):
        self.tty = sys.stdin.isatty()
        self.saved = None

    def __enter__(self):
        if self.tty:
            import termios, tty
            self.saved = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        return self

    def __exit__(self, *_):
        if self.saved:
            import termios
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.saved)

    def flush(self):
        """Drops keys pressed while a step was still drawing, so an early key never skips a step."""
        if self.tty:
            import termios
            termios.tcflush(sys.stdin, termios.TCIFLUSH)

    def read(self):
        ch = sys.stdin.read(1)
        if ch == "":
            raise Quit
        if ch == "\x1b" and self.tty and select.select([sys.stdin], [], [], 0.05)[0]:
            ch += sys.stdin.read(2)
        return {" ": "next", "\n": "next", "\r": "next", "\x1b[C": "next", "l": "next",
                "b": "back", "\x1b[D": "back", "h": "back", "q": "quit", "\x03": "quit"}.get(ch, "")


class Demo:
    def __init__(self, args, keys):
        self.auto, self.live, self.keys = args.auto, args.live, keys
        self.dir = None
        if self.live:
            self.dir = tempfile.mkdtemp(prefix="modelplane-demo-")
            for name, body in FILES.items():
                os.makedirs(os.path.dirname(os.path.join(self.dir, name)) or self.dir, exist_ok=True)
                with open(os.path.join(self.dir, name), "w") as f:
                    f.write(body)

    # Pacing and keys.

    def sleep(self, s):
        if PACE > 0 and s > 0:
            time.sleep(s * PACE)

    def wait(self, hold):
        """Waits for a key, or in auto mode for hold seconds."""
        if self.auto:
            self.sleep(hold)
            return
        self.keys.flush()
        while True:
            k = self.keys.read()
            if k == "next":
                return
            if k == "back":
                raise Back
            if k == "quit":
                raise Quit

    # Drawing.

    def header(self, s):
        con.print(Text.assemble((s["beat"], BEAT), "  ", (s["title"], "bold")))
        con.print()

    def type_cmd(self, lines):
        for i, line in enumerate(lines):
            text, _, comment = line.partition("   # ")
            prefix = "$ " if i == 0 else ""
            con.print(Text(prefix, CMD), end="")
            for ch in text:
                con.print(Text(ch, CMD), end="")
                sys.stdout.flush()
                self.sleep(0.018)
            if comment:
                con.print(Text("   # " + comment, DIM), end="")
            if i < len(lines) - 1:
                con.print()
        sys.stdout.flush()

    def out(self, lines, style=OUT):
        for line in lines:
            con.print(Text("  " + line, style))

    def run_live(self, cmd):
        try:
            p = subprocess.run(["bash", "-c", cmd], cwd=self.dir, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, str(e)
        if p.returncode != 0:
            return None, (p.stderr or p.stdout).strip().splitlines()[-1:] or ["exit %d" % p.returncode]
        return p.stdout.rstrip("\n").splitlines(), None

    def fallback(self, why):
        why = why[0] if isinstance(why, list) else why
        con.print(Text(f"  live: {why[:60]} · showing the recording", "yellow"))

    def step(self, st):
        kind = st[0]
        if kind == "cmd":
            _, shown, output, live = st
            self.type_cmd(shown)
            self.wait(0.8)
            con.print()
            if self.live and live:
                got, err = self.run_live(live)
                if got is not None:
                    self.out(got)
                    return
                self.fallback(err)
            self.sleep(0.4)
            self.out(output)
            self.sleep(1.2 + 0.15 * len(output))
        elif kind == "stream":
            _, shown, live_cmd, answer = st
            self.type_cmd(shown)
            self.wait(0.8)
            con.print()
            if self.live and live_cmd:
                if os.environ.get("GATEWAY") and os.environ.get("KEY"):
                    if self.stream_live(live_cmd):
                        return
                else:
                    self.fallback("set GATEWAY and KEY to send it")
            self.sleep(0.5)
            self.stream(answer.split())
            self.sleep(2)
        elif kind == "out":
            self.out(st[1])
            self.sleep(1.5)
        elif kind == "note":
            con.print(Text("  " + st[1], DIM))
        elif kind == "say":
            con.print(Text("→ " + st[1], SAY))
        elif kind == "watch":
            self.watch(st[1])
        elif kind == "traffic":
            self.traffic()

    def stream(self, words):
        col = 4
        con.print("    ", end="")
        for w in words:
            col = self.word(w, col)
            self.sleep(0.05)
        con.print()

    def word(self, w, col):
        """Prints one streamed word, wrapping at WRAP, and returns the new column."""
        if col + 1 + len(w) > WRAP:
            con.print()
            con.print("    ", end="")
            col = 4
        con.print(("" if col == 4 else " ") + w, end="")
        sys.stdout.flush()
        return col + len(w) + (0 if col == 4 else 1)

    def stream_live(self, cmd):
        """Sends the request and prints the streamed tokens as they arrive. False if nothing came back."""
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError as e:
            self.fallback(str(e))
            return False
        buf, words = "", 0
        col = 4
        con.print("    ", end="")
        for line in p.stdout:
            line = line.strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            try:
                delta = json.loads(line[5:])["choices"][0].get("delta", {}).get("content") or ""
            except (ValueError, KeyError, IndexError):
                continue
            buf += delta
            while " " in buf or "\n" in buf:
                i = min(x for x in (buf.find(" "), buf.find("\n")) if x >= 0)
                w, buf = buf[:i], buf[i + 1:]
                if w:
                    col = self.word(w, col)
                    words += 1
        p.wait()
        if buf.strip():
            col = self.word(buf.strip(), col)
            words += 1
        con.print()
        if not words:
            self.fallback(f"no answer from the gateway (curl exit {p.returncode})")
            return False
        return True

    def watch(self, frames):
        def render(label, frame, done):
            rows = [Text.assemble(("watch", "bold magenta"), ("  every 2 s · " + ("done at " if done else "") + label, DIM))]
            for cmd, comment, output in frame:
                rows.append(Text.assemble(("$ " + cmd, CMD), ("   # " + comment, DIM)))
                rows += [Text("  " + o) for o in output]
            return Group(*rows)

        with Live(render(*frames[0][:2], False), console=con, auto_refresh=False) as live:
            for i, (label, frame) in enumerate(frames):
                live.update(render(label, frame, i == len(frames) - 1), refresh=True)
                if i < len(frames) - 1:
                    nxt = frames[i + 1][0]
                    self.sleep(1.2 * (secs(nxt) - secs(label)) / 2 + 0.5)

    def traffic(self):
        rows = [
            ("output tokens/s", "modelplane_engine_tokens_total", [
                ("eks-us-east", spark("▂▂▃▂▂▃▃▂▂▃▂", ""), "0.0"),
                ("gke-us-central", spark("▂▂▂▃▂▂▃▂▂▂▃", "▅▆▅▆▆▅▆▅▆▆▅"), "34.3")]),
            ("requests/s", "modelplane_engine_requests_total", [
                ("eks-us-east", spark("▂▂▂▂▃▂▂▂▃▂▂", ""), "0.00"),
                ("gke-us-central", spark("▂▂▂▂▂▃▂▂▂▂▂", "▄▅▅▄▅▅▄▅▅▅▄"), "0.73")]),
        ]
        for title, metric, series in rows:
            con.print(Text.assemble(("  " + title, "bold"), ("  " + metric, DIM)))
            for name, line, value in series:
                con.print(Text.assemble(("  " + name.ljust(17)), (line, "white"), ("  " + value.rjust(5))))
            con.print(Text.assemble(" " * (19 + 11), ("▲", "yellow"),
                                    (" the taint, 1:47 ago" if title == rows[0][0] else "", DIM)))
            self.sleep(1.5)
        con.print()
        con.print(Text.assemble(("  time to first token at the gateway, p90: ", "bold"), "0.04 s",
                                ("  what callers feel", DIM)))
        con.print(Text.assemble(("  since the taint: ", "bold"), "84 of 84 HTTP 200"))
        self.sleep(2)

    def card(self, which):
        mode = (f"LIVE · read-only commands run on {self.context()}; the patches are replayed" if self.live
                else "Replayed from a real two-cloud fleet, offline.")
        if which == "open":
            body = Text.assemble(
                ("The open source control plane for AI inference\nacross a fleet of clusters.\n\n", "bold"),
                "Watch one model run on two clouds behind one endpoint.\n"
                "Then a cluster goes down for maintenance.\n\n",
                (mode, DIM))
            title = "Modelplane"
        else:
            body = Text.assemble(
                "· Places a model by what a GPU can do, across clouds.\n"
                "· Serves every replica behind one endpoint.\n"
                "· Drains a cluster like a node, with a taint.\n\n",
                ("Open source, Apache 2.0, built on Crossplane.\n", "bold"),
                "github.com/modelplaneai/modelplane\n"
                "docs.modelplane.ai/getting-started")
            title = "What Modelplane does"
        con.print()
        con.print(Panel(body, title=Text(title, "bold"), title_align="left", border_style="cyan",
                        padding=(1, 3), width=COLS - 2))

    def context(self):
        try:
            return subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True,
                                  timeout=5).stdout.strip() or "no context"
        except (OSError, subprocess.TimeoutExpired):
            return "no kubectl"

    def screen(self, s):
        con.clear()
        if "card" in s:
            self.card(s["card"])
            self.wait(6)
            return
        self.header(s)
        prev = None
        for st in s["steps"]:
            if prev and not (st[0] in ("note", "out") and prev == "note"):
                con.print()
            self.step(st)
            prev = st[0]
        self.wait(5)

    def run(self, loop):
        i = 0
        while True:
            try:
                self.screen(SCREENS[i])
                i += 1
            except Back:
                i = max(0, i - 1)
            if i == len(SCREENS):
                if not loop:
                    return
                i = 0


def secs(label):
    m, s = label.split(":")
    return int(m) * 60 + int(s)


def check_size(keys, auto):
    cols, rows = shutil.get_terminal_size((COLS, ROWS))
    if cols >= COLS and rows >= ROWS or not sys.stdout.isatty():
        return
    con.print(Text(f"This terminal is {cols}×{rows}; the demo needs {COLS}×{ROWS}. "
                   "Make the font smaller or the window bigger.", "yellow"))
    if not auto:
        con.print(Text("Space to go on anyway, q to quit.", DIM))
        while (k := keys.read()) not in ("next", "quit"):
            pass
        if k == "quit":
            raise Quit


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--auto", action="store_true", help="play without keys, pausing to read each step")
    ap.add_argument("--loop", action="store_true", help="with --auto, start again after the last screen")
    ap.add_argument("--live", action="store_true", help="run the read-only commands on your current kube context")
    ap.add_argument("--version", action="version", version=f"modelplane-demo {VERSION}")
    args = ap.parse_args()
    if args.loop:
        args.auto = True
    with Keys() as keys:
        demo = Demo(args, keys)
        try:
            check_size(keys, args.auto)
            demo.run(args.loop)
        except (Quit, KeyboardInterrupt):
            pass
        finally:
            if demo.dir:
                shutil.rmtree(demo.dir, ignore_errors=True)
    con.print()


if __name__ == "__main__":
    main()
