#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["textual>=0.80", "rich>=13", "pyyaml>=6"]
# ///
"""The Modelplane walkthrough: a kubectl terminal on the left, the fleet it changes on the right.

    uv run walkthrough/walkthrough.py                  # the three chapters, then Day 2
    uv run walkthrough/walkthrough.py --short          # the three chapters only
    uv run walkthrough/walkthrough.py --booth          # plays itself and loops
    uv run walkthrough/walkthrough.py --from deploy    # start at a beat, with the fleet built up to it
    uv run walkthrough/walkthrough.py --talk-track     # print the presenter's notes

Every step is a command at the prompt, already typed: Enter runs it, or edit it
first. kubectl get, cat, diff and curl work at any prompt. The fleet is
simulated; the objects and the commands are Modelplane's.
"""
import argparse, asyncio, os, sys
from pathlib import Path

import yaml
from rich.console import Group
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, RichLog, Static

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet_map
from fleet_model import ROOT, Fleet
from fleet_shell import Shell

STORY = ROOT / "story.yaml"
INK, DIM, PURPLE, CYAN, YELLOW = fleet_map.INK, fleet_map.DIM, fleet_map.PURPLE, fleet_map.CYAN, fleet_map.YELLOW


TEAMS = {"platform": ["PLATFORM"], "ml": ["ML TEAM"], "both": ["PLATFORM", "ML TEAM"]}
BADGE = {"PLATFORM": f"bold {INK} on {PURPLE}", "ML TEAM": f"bold #04121f on {CYAN}"}


def load_story(short=False, path=STORY):
    story = yaml.safe_load(Path(path).read_text())
    if short:
        story["chapters"] = [c for c in story["chapters"] if not c.get("encore")]
    story["beats"] = [dict(b, chapter=c) for c in story["chapters"] for b in c["beats"]]
    for b in story["beats"]:
        if b.get("team") not in TEAMS:
            raise ValueError(f"beat {b['id']}: team must be one of {', '.join(TEAMS)}")
    return story


def badge(team):
    """Who does this step: the platform team, the ML team, or both."""
    t = Text()
    for i, who in enumerate(TEAMS[team]):
        t.append(" + " if i else "")
        t.append(f" {who} ", style=BADGE[who])
    return t


def same_step(typed, command):
    """A typed command counts as the step if it starts the same way (so edited flags still advance)."""
    return typed.split()[:3] == command.split()[:3]


def opening_card(story):
    o = story["opening"]
    return Group(
        Text(o["title"], style=f"bold {INK}"),
        Text(o["tagline"], style=CYAN),
        Text(""),
        *[Text(line, style=INK) for line in o["problem"]],
        Text(""),
        Text("   ".join(x.upper() for x in o["facts"]), style=YELLOW),
        *[Text.assemble((f"{label:<12}", YELLOW), (what, DIM)) for label, what in o["works_with"]],
        Text(""),
        Text(" ".join(o["note"].split()), style=DIM),
        Text("\nEnter to start", style=f"bold {PURPLE}"),
    )


def closing_card(story):
    c = story["closing"]
    lines = [Text(c["title"], style=f"bold {INK}"), Text("")]
    for item in c["recap"]:
        (chip, what), = item.items()
        t = Text()
        t.append_text(fleet_map.chip(chip.ljust(12), True))
        t.append(f"  {what}", style=INK)
        lines.append(t)
    lines += [Text(""), Text(c["ask"], style=CYAN), Text("\nEnter to finish", style=f"bold {PURPLE}")]
    return Group(*lines)


def talk_track(story):
    out = ["# Talk track", "",
           "Generated from `walkthrough/story.yaml` by `uv run walkthrough/walkthrough.py --talk-track`. "
           "Edit the story, not this file.", ""]
    o = story["opening"]
    out += ["## Opening", "", f"**{' '.join(o['problem'])}**", "", " ".join(o["note"].split()), ""]
    for ch in story["chapters"]:
        out += [f"## {ch['title']}", "", f"*{ch['label']}*", "", f"**{ch['line']}**", ""]
        for i, b in enumerate(ch["beats"], 1):
            out += [f"{i}. *{' + '.join(TEAMS[b['team']])}*: **{b['caption']}** {b['detail']}", "",
                    f"   `{' '.join(b['command'].split())}`", "",
                    f"   {' '.join(b['say'].split())}", ""]
    c = story["closing"]
    out += ["## Close", "", f"**{c['title']}**", ""] + [f"- {k}: {v}" for i in c["recap"] for k, v in i.items()]
    return "\n".join(out) + "\n"


class Card(ModalScreen):
    BINDINGS = [Binding("enter", "close"), Binding("space", "close"), Binding("escape", "close")]

    def __init__(self, body, auto=None):
        super().__init__()
        self.body, self.auto = body, auto

    def compose(self) -> ComposeResult:
        yield Static(self.body, id="card")

    def on_mount(self):
        if self.auto:
            self.set_timer(self.auto, self.action_close)

    def action_close(self):
        if self.is_attached and self.app.screen is self:
            self.dismiss()


class Walkthrough(App):
    TITLE = "Modelplane walkthrough"
    CSS = f"""
    Screen {{ background: #071624; }}
    #header {{ height: 3; padding: 1 2 0 2; }}
    #main {{ height: 1fr; }}
    #left {{ width: 1fr; border: round #23384d; border-title-color: {DIM}; margin: 0 1 0 1; }}
    #term {{ background: #040e18; height: 1fr; scrollbar-size-vertical: 1; }}
    #caption {{ height: auto; min-height: 1; padding: 0 1; color: {CYAN}; background: #0a1c2e; }}
    #prompt {{ border: tall {PURPLE}; background: #040e18; }}
    #right {{ width: 74; padding: 0 1 0 0; }}
    #footer {{ height: 1; padding: 0 2; color: {DIM}; }}
    Card {{ align: center middle; background: #071624 85%; }}
    #card {{ width: 84; height: auto; padding: 1 3; border: round {PURPLE}; background: #0b1f33; }}
    """
    BINDINGS = [Binding("ctrl+c", "quit", "Quit", priority=True)]

    def __init__(self, story, pace=1.0, booth=False, cards=True, start_at=None):
        super().__init__()
        self.story, self.pace, self.booth, self.cards, self.start_at = story, pace, booth, cards, start_at
        self.busy, self.idx, self.chapter, self.turning = False, 0, None, False
        self.fresh()

    def fresh(self):
        self.fleet = Fleet()
        self.shell = Shell(self.fleet)

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield RichLog(id="term", wrap=True, markup=False, auto_scroll=True)
                yield Static(id="caption")
                yield Input(id="prompt", placeholder="Enter to bring back the next step")
            with VerticalScroll(id="right"):
                yield Static(id="map")
        yield Static(Text("Enter to run the step · edit it first, or try kubectl get, cat, diff, curl · Ctrl-C to quit"),
                      id="footer")

    def on_mount(self):
        self.query_one("#left").border_title = "control-cluster · kubectl"
        self.draw()
        if self.cards:
            self.push_screen(Card(opening_card(self.story), auto=6 if self.booth else None), lambda _: self.begin())
        else:
            self.begin()

    # ---- the story ---------------------------------------------------------

    def begin(self):
        self.idx, self.chapter, self.turning = 0, None, False
        if self.start_at:
            ids = [b["id"] for b in self.story["beats"]]
            target = ids.index(self.start_at) if self.start_at in ids else 0
            for b in self.story["beats"][:target]:
                for _ in self.shell.run(b["command"]):
                    pass
            self.idx = target
            self.fleet.pulse = None
            self.draw()
        self.show_beat()

    def show_beat(self):
        beat = self.story["beats"][self.idx]
        ch = beat["chapter"]
        if ch is not self.chapter:
            self.chapter = ch
            head = Text()
            head.append(ch["label"], style=f"bold {YELLOW}")
            head.append("   ")
            head.append(ch["title"], style=f"bold {INK}")
            self.query_one("#header", Static).update(head)
            self.query_one("#term", RichLog).clear()
        n = badge(beat["team"])
        n.append(f"  {self.idx + 1}/{len(self.story['beats'])}  ", style=DIM)
        n.append(beat["caption"], style=f"bold {CYAN}")
        n.append(f"\n{beat['detail']}", style=INK)
        self.query_one("#caption", Static).update(n)
        self.offer(beat["command"])
        if self.booth:
            self.set_timer(2.5, self.auto_submit)

    def offer(self, command):
        box = self.query_one("#prompt", Input)
        box.value = " ".join(command.split())
        box.cursor_position = len(box.value)
        box.focus()

    def auto_submit(self):
        if not self.busy and not isinstance(self.screen, Card):
            self.run_worker(self.execute(self.query_one("#prompt", Input).value), exclusive=True)

    def on_input_submitted(self, event):
        if self.busy:
            return
        if self.turning and not event.value.strip():
            self.turn_page()
            return
        if not event.value.strip():
            self.offer(self.story["beats"][self.idx]["command"])
            return
        self.run_worker(self.execute(event.value), exclusive=True)

    async def execute(self, line):
        self.busy = True
        box, term = self.query_one("#prompt", Input), self.query_one("#term", RichLog)
        box.value = ""
        echo = Text("$ ", style=f"bold {PURPLE}")
        echo.append(line, style=INK)
        width = term.scrollable_content_region.width     # RichLog otherwise measures against 80 columns
        term.write(echo, width=width)
        for delay, out in self.shell.run(line):
            if out is not None:
                term.write(out, width=width)
            self.draw()
            if delay and self.pace:
                await asyncio.sleep(delay * self.pace)
        term.write(Text(""))
        self.busy = False
        if self.fleet.pulse:
            self.set_timer(2.5, self.clear_pulse)
        if self.turning:
            return
        beat = self.story["beats"][self.idx]
        if not same_step(line, " ".join(beat["command"].split())):
            self.offer(beat["command"])
            return
        if self.booth:
            await asyncio.sleep(3.5)
        self.idx += 1
        if self.idx >= len(self.story["beats"]) or \
                self.story["beats"][self.idx]["chapter"] is not self.chapter:
            self.end_chapter()
        else:
            self.show_beat()

    def end_chapter(self):
        """Hold the last step's output on screen with the chapter's line until Enter turns the page."""
        self.turning = True
        last = self.idx >= len(self.story["beats"])
        t = Text(self.chapter["line"], style=f"bold {INK}")
        t.append("\nEnter to finish" if last else
                 f"\nEnter to continue to {self.story['beats'][self.idx]['chapter']['title']}", style=DIM)
        self.query_one("#caption", Static).update(t)
        box = self.query_one("#prompt", Input)
        box.value = ""
        box.focus()
        if self.booth:
            self.set_timer(5, self.turn_page)

    def turn_page(self):
        if self.turning:
            self.turning = False
            if self.idx >= len(self.story["beats"]):
                self.finish()
            else:
                self.show_beat()

    def clear_pulse(self):
        self.fleet.pulse = None
        self.draw()

    def finish(self):
        def done(_):
            if self.booth:
                self.fresh()
                self.draw()
                self.begin()
            else:
                self.exit()
        self.push_screen(Card(closing_card(self.story), auto=10 if self.booth else None), done)

    def draw(self):
        self.query_one("#map", Static).update(fleet_map.render(self.fleet))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--short", action="store_true", help="the three chapters only, no Day 2 encore")
    ap.add_argument("--booth", action="store_true", help="play every step by itself and loop")
    ap.add_argument("--no-cards", action="store_true", help="skip the opening and closing cards")
    ap.add_argument("--from", dest="start_at", metavar="BEAT", help="start at this beat id (see walkthrough/story.yaml)")
    ap.add_argument("--pace", type=float, default=float(os.environ.get("FLEET_SIM_PACE", "1")),
                    help="scale the animation delays; 0 turns them off")
    ap.add_argument("--talk-track", action="store_true", help="print the presenter's notes as Markdown")
    args = ap.parse_args()
    story = load_story(short=args.short)
    if args.talk_track:
        print(talk_track(story), end="")
        return
    Walkthrough(story, pace=args.pace, booth=args.booth, cards=not args.no_cards, start_at=args.start_at).run()


if __name__ == "__main__":
    main()
