"""The Modelplane walkthrough: the fleet model behaves the way docs.modelplane.ai says, and the app plays through."""
import asyncio
import re
from collections import Counter
from pathlib import Path

import pytest

from fleet_model import Fleet, load
from fleet_shell import Shell
from walkthrough import load_story, same_step, talk_track

ROOT = Path(__file__).resolve().parent.parent     # walkthrough/


def play(until=None):
    f = Fleet()
    sh = Shell(f)
    out = []
    for b in load_story()["beats"]:
        out = [o for _, o in sh.run(" ".join(b["command"].split())) if o is not None]
        if b["id"] == until:
            break
    return f, out


def placed(f):
    """deployment -> Counter of the clusters its replicas are on."""
    out = {}
    for r in f.replicas.values():
        out.setdefault(r.deployment, Counter())[r.cluster] += 1
    return out


def text(out):
    return "\n".join(getattr(o, "code", None) or str(o) for o in out)    # Syntax keeps its text in .code


def test_every_beat_runs_and_is_unique():
    beats = load_story()["beats"]
    assert len({b["id"] for b in beats}) == len(beats)
    play()


def test_replicas_are_named_like_modelplane_names_them():
    f, _ = play("deploy")
    assert all(re.fullmatch(r"glm-[0-9a-f]{5}", n) for n in f.replicas)


def test_deploy_spreads_across_clouds():
    f, _ = play("deploy")
    assert placed(f) == {"glm": Counter({"aws-us-east": 1, "coreweave-us-west": 1})}
    assert all(r.condition == 2 for r in f.replicas.values())        # ModelReady


def test_a_cluster_without_cache_storage_gets_no_cached_replicas():
    f = Fleet()
    docs = load("platform/", "model/")
    for d in docs:
        if d["kind"] == "InferenceCluster" and d["spec"]["cluster"]["source"] == "Existing":
            del d["spec"]["cluster"]["existing"]["cache"]
    list(f.apply(docs))
    assert "glm-5-3" not in f.clusters["coreweave-us-west"].caches
    assert placed(f) == {"glm": Counter({"aws-us-east": 2})}


def test_scale_spreads_before_packing():
    f, _ = play("scale-up")
    assert placed(f)["glm"] == Counter({"aws-us-east": 2, "coreweave-us-west": 1})


def test_amd_deployment_lands_only_on_amd():
    f, _ = play("add-deployment")
    assert placed(f)["glm-eu"] == Counter({"vultr-ams": 1})
    assert "vultr-ams" not in placed(f)["glm"]


def test_same_name_is_answered_by_both_deployments_on_three_clouds():
    f, out = play("same-name")
    served = text(out)
    assert "coding-team/glm-eu" in served and re.search(r"coding-team/glm\b(?!-)", served)
    for cluster in ("aws-us-east", "coreweave-us-west", "vultr-ams"):
        assert cluster in served


def test_drain_deletes_and_recreates_elsewhere():
    before, _ = play("retire")
    old = {n for n, r in before.replicas.items() if r.cluster == "coreweave-us-west"}
    assert old                                                        # there is something to drain
    f, _ = play("drain")
    assert "coreweave-us-west" not in {r.cluster for r in f.replicas.values()}
    assert not old & set(f.replicas)                                  # new names: delete plus create
    assert placed(f)["glm-sglang"] == Counter({"aws-us-east": 2})


def test_canary_takes_about_one_request_in_ten():
    f, out = play("canary-traffic")
    assert sum("coding-team/glm-sglang" in str(o) for o in out) == 1


def test_canary_spreads_across_clouds():
    f, _ = play("canary")
    assert placed(f)["glm-sglang"] == Counter({"aws-us-east": 1, "coreweave-us-west": 1})


def test_promote_and_retire_leave_only_the_new_engine_in_the_us():
    f, _ = play("retire")
    assert set(placed(f)) == {"glm-sglang", "glm-eu"}
    assert sum(placed(f)["glm-sglang"].values()) == 2
    assert all(r.condition == 2 for r in f.replicas.values())


def test_free_gpus_report_the_pool_histogram():
    f, _ = play("deploy")
    sh = Shell(f)
    out = [o for _, o in sh.run("kubectl get ic aws-us-east -o jsonpath='{.status.schedulablePools}' "
                                "| jq '.[] | {name, nodes, nodesByFreeDevices}'") if o is not None]
    assert '"nodes": 3' in text(out) and '"gpu.nvidia.com": 8' in text(out)
    pools = f.clusters["aws-us-east"].schedulable_pools()[0]["nodesByFreeDevices"]
    assert {"freeDevices": {"gpu.nvidia.com": 0}, "nodes": 1} in pools


def test_compare_returns_every_serving_deployment_once_telemetry_is_on():
    _, out = play("compare")
    shown = text(out)
    for d in ("glm ", "glm-eu", "glm-sglang"):
        assert d in shown
    assert "illustrative" in shown                                    # the values are labelled as made up
    query = [b["command"] for b in load_story()["beats"] if b["id"] == "compare"][0]
    f = Fleet()
    sh = Shell(f)
    list(sh.run("kubectl apply -f platform/"))
    list(sh.run("kubectl apply -f model/"))
    assert '"result":[]' in text(o for _, o in sh.run(" ".join(query.split())))   # no sink yet, no data


def test_endpoints_show_the_served_model_and_cluster():
    _, out = play("endpoints")
    assert "coding-team/glm" in text(out) and "coreweave-us-west" in text(out)


def test_short_names_match_the_xrds():
    f = Fleet()
    sh = Shell(f)
    list(sh.run("kubectl apply -f platform/"))
    assert "aws-us-east" in text(o for _, o in sh.run("kubectl get ic"))
    assert "doesn't have" in text(o for _, o in sh.run("kubectl get igw"))


def test_no_cluster_fits_leaves_a_shortfall():
    f = Fleet()
    list(f.apply(load("platform/", "model/")))
    list(f.scale("glm", 9))
    assert f.shortfall(f.deployments["glm"]) == 4                     # 5 NVIDIA nodes, 9 replicas


def test_same_step_accepts_edited_flags():
    assert same_step("kubectl scale md glm -n coding-team --replicas 4", "kubectl scale md glm --replicas 3")
    assert not same_step("kubectl get modelreplicas", "kubectl apply -f model/")


def test_talk_track_is_generated_from_the_story():
    assert (ROOT / "talk-track.md").read_text() == talk_track(load_story()), \
        "regenerate: uv run walkthrough/walkthrough.py --talk-track > walkthrough/talk-track.md"


def test_app_plays_through_headless_and_holds_the_last_output():
    pytest.importorskip("textual")
    from textual.widgets import RichLog
    from walkthrough import Card, Walkthrough

    async def run():
        story = load_story()
        app = Walkthrough(story, pace=0)
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.press("enter")                       # opening card
            for i in range(len(story["beats"])):
                await pilot.pause()
                if app.turning:
                    await pilot.press("enter")               # turn the page to the next chapter
                    await pilot.pause()
                await pilot.press("enter")
                for _ in range(200):
                    await pilot.pause()
                    if not app.busy and app.idx > i:
                        break
                assert app.idx == i + 1, f"stuck at {story['beats'][i]['id']}"
            for _ in range(100):
                await pilot.pause()
                if app.turning:
                    break
            shown = "\n".join(str(l.text) for l in app.query_one("#term", RichLog).lines)
            assert app.turning and not isinstance(app.screen, Card), "the last output was covered"
            assert "inferencecluster.modelplane.ai/coreweave-us-west patched" in shown
            await pilot.press("enter")                       # Enter to finish: the closing card
            await pilot.pause()
            assert isinstance(app.screen, Card)
            await pilot.press("enter")                       # dismiss it
        return app

    app = asyncio.run(run())
    assert set(placed(app.fleet)) == {"glm-sglang", "glm-eu"}


def test_last_step_of_a_chapter_stays_on_screen_until_enter():
    pytest.importorskip("textual")
    from textual.widgets import RichLog
    from walkthrough import Walkthrough

    async def run():
        app = Walkthrough(load_story(), pace=0, cards=False, start_at="clusters")
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            await pilot.press("enter")                       # kubectl apply -f platform/, the chapter's last step
            for _ in range(100):
                await pilot.pause()
                if app.turning:
                    break
            shown = "\n".join(str(l.text) for l in app.query_one("#term", RichLog).lines)
            assert app.turning and "inferencecluster.modelplane.ai/coreweave-us-west created" in shown
            await pilot.press("enter")                       # turn the page
            await pilot.pause()
            assert not app.turning and app.chapter["id"] == "model"
    asyncio.run(run())


def test_from_builds_the_fleet_up_to_the_beat_and_booth_plays_by_itself():
    pytest.importorskip("textual")
    from walkthrough import Walkthrough

    async def run(until, **kw):
        app = Walkthrough(load_story(short=True), pace=0, cards=False, **kw)
        async with app.run_test(size=(180, 50)) as pilot:
            for _ in range(120):
                await pilot.pause(0.1)
                if app.idx >= until:
                    break
        return app

    at = [b["id"] for b in load_story(short=True)["beats"]].index("replicas")
    started = asyncio.run(run(at, start_at="replicas"))
    assert started.idx == at and len(started.fleet.replicas) == 2
    booth = asyncio.run(run(1, booth=True))                 # no key pressed
    assert booth.idx >= 1 and booth.fleet.clusters == {}    # past the cat step, nothing applied yet
