"""The fleet map: the control plane with its five capabilities, then each cluster's GPUs.

Drawn only from the fleet model, so whatever a command changed is what the map shows.
"""
from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fleet_model import CHIPS

INK, DIM, FAINT = "#e6edf5", "#8aa0b8", "#3a4d63"
PURPLE, CYAN, AMBER, RED, YELLOW = "#8b6cff", "#22d3ee", "#f59e0b", "#f87171", "#e8f74a"
COMING_UP = "#1f6f7a"


def chip(name, lit):
    return Text(f" {name} ", style=f"bold {INK} on {PURPLE}" if lit else f"{DIM} on #13263a")


def control(f):
    row = Text()
    for c in CHIPS:
        row.append_text(chip(c, c in f.chips))
        row.append(" ")
    if f.telemetry:
        row.append_text(Text(" telemetry ", style=f"{CYAN} on #0f3640"))
    lines = [row]
    for dep in f.deployments.values():
        short = f.shortfall(dep)
        if short:
            lines.append(Text(f"{dep.name}: {dep.replicas - short}/{dep.replicas} placed, "
                              f"ReplicasScheduled=False (no free GPUs that fit)", style=YELLOW))
    return Panel(Group(*lines), title=Text(" modelplane-control ", style=f"bold {INK}"), title_align="left",
                 subtitle=Text(" CONTROL PLANE ", style=PURPLE), subtitle_align="right",
                 border_style=PURPLE, box=box.ROUNDED, padding=(0, 1))


def client(f):
    t = Table.grid(expand=True)
    t.add_column()
    t.add_column(justify="right")
    gws = "  ".join(f"gateway {g}" for g in f.gateways)
    t.add_row(Text("OpenAI SDK · Claude Code", style=INK), Text("OpenAI /v1   Anthropic /anthropic/v1", style=CYAN))
    t.add_row(Text("model: coding-team/glm", style=DIM), Text(gws, style=DIM))
    return Panel(t, title=Text(" client ", style=DIM), title_align="left", border_style=CYAN, box=box.ROUNDED,
                 padding=(0, 1))


def cluster(f, c):
    rows = []
    gw = [g for g, s in f.gateways.items() if s["clusterName"] == c.name]
    if gw:
        rows.append(Text(f"[ gateway {gw[0]} ]", style=f"bold {CYAN}"))
    for p in c.pools:
        head = Text(f"nodepool {p.klass.name}", style=DIM)
        if not c.ready:
            head.append("   provisioning...", style=YELLOW)
        for cache in sorted(c.caches):
            head.append(f"   cache {cache} ●", style="#5eead4")
        rows.append(head)
        for n in p.nodes:
            line = Text(f"{n.name}  ", style=DIM)
            owner = next((x for x in n.cells if x), None)
            for cell in n.cells:
                if cell is None:
                    line.append("□ ", style=FAINT)
                else:
                    r, dep = f.replicas[cell], f.deployments[f.replicas[cell].deployment]
                    colour = AMBER if dep.labels.get("env") == "canary" or dep.engine == "SGLang" else (CYAN if r.serving else COMING_UP)
                    line.append("■ ", style=colour)
            if owner:
                r = f.replicas[owner]
                dep = f.deployments[r.deployment]
                line.append(f" {r.name}", style=f"bold {INK}")
                line.append(f"  {dep.engine} · TP{dep.count}", style=AMBER if dep.engine == "SGLang" else CYAN)
                if r.condition < 2:
                    line.append(f"  {r.status}", style=YELLOW)
                if f.pulse == r.name:
                    line.append("  ◀ request", style=f"bold {INK}")
            rows.append(line)
    for t in c.taints:
        rows.append(Text(f'taint {t["key"]}:{t["effect"]}', style=f"bold {RED}"))
    border = RED if c.blocked() else ("#2a4a66" if c.ready else YELLOW)
    title = Text()
    title.append(f" {c.provider} ", style=f"bold {DIM}")
    title.append(f"{c.name} ", style=f"bold {INK}")
    sub = Text(f" {c.region} · {c.vendor} ", style="#5eead4" if c.vendor == "NVIDIA" else "#fca5a5")
    return Panel(Group(*rows), title=title, title_align="left", subtitle=sub, subtitle_align="right",
                 border_style=border, box=box.ROUNDED, padding=(0, 1))


def render(f):
    parts = []
    if f.gateways:
        parts.append(client(f))
    parts.append(control(f))
    parts += [cluster(f, c) for c in f.clusters.values()]
    if not f.clusters:
        parts.append(Text("\n  no inference clusters yet", style=FAINT))
    legend = Text("\n■ serving  ", style=CYAN)
    legend.append("■ coming up  ", style=COMING_UP)
    legend.append("■ SGLang / canary  ", style=AMBER)
    legend.append("□ free GPU", style=FAINT)
    parts.append(legend)
    return Group(*parts)
