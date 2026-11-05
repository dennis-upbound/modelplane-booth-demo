"""The commands the walkthrough answers: kubectl, cat, diff and curl, against the fleet model.

run() yields (seconds, output) pairs; output is a string, a Rich renderable, or None
when only the map changed. Paths are relative to walkthrough/manifests.

What kubectl prints follows the Modelplane XRDs: their short names and printer
columns, plus the SYNCED, READY, COMPOSITION and AGE columns every Crossplane
composite gets. Lines marked "simulator:" are the simulator's own notes, not output.
"""
import difflib, json, re, shlex, time

import yaml
from rich.syntax import Syntax
from rich.text import Text

from fleet_model import MANIFESTS, load

KINDS = {   # what kubectl accepts -> the resource (short names from the XRDs)
    **dict.fromkeys(["inferenceclass", "inferenceclasses", "icl"], "inferenceclasses"),
    **dict.fromkeys(["inferencecluster", "inferenceclusters", "ic"], "inferenceclusters"),
    **dict.fromkeys(["modeldeployment", "modeldeployments", "md"], "modeldeployments"),
    **dict.fromkeys(["modelreplica", "modelreplicas", "mr"], "modelreplicas"),
    **dict.fromkeys(["modelendpoint", "modelendpoints", "me"], "modelendpoints"),
    **dict.fromkeys(["modelservice", "modelservices", "ms"], "modelservices"),
    **dict.fromkeys(["inferencegateway", "inferencegateways", "ig"], "inferencegateways"),
    **dict.fromkeys(["modelcache", "modelcaches", "mc"], "modelcaches"),
    **dict.fromkeys(["telemetrydestination", "telemetrydestinations", "td"], "telemetrydestinations"),
}
KIND_OF = {"inferenceclasses": "InferenceClass", "inferenceclusters": "InferenceCluster",
           "modeldeployments": "ModelDeployment", "modelservices": "ModelService",
           "inferencegateways": "InferenceGateway", "modelcaches": "ModelCache",
           "telemetrydestinations": "TelemetryDestination"}
FOLD = 3        # engine flags shown before the rest are folded away
ENV = {"$GW_US": "https://us.gw.example.com", "$GW_EU": "https://eu.gw.example.com",
       "$PROM": "https://prom.example.internal"}
ILLUSTRATIVE_HIT_RATE = {"vLLM": 0.71, "SGLang": 0.83}   # made up for the walkthrough, not measured
NOTE = "#5b6b80"
HELP = ("This walkthrough answers kubectl get / apply / scale / patch, cat, diff and curl "
        "against a simulated fleet. Press Enter on an empty prompt to bring the next step back.")


def table(cols, rows):
    """Columns the way kubectl prints them: left-aligned, three spaces apart, nothing cut short."""
    cells = [[c if isinstance(c, Text) else Text(str(c)) for c in r] for r in rows]
    widths = [max([len(h)] + [len(r[i].plain) for r in cells]) for i, h in enumerate(cols)]
    out = Text("   ".join(h.ljust(w) for h, w in zip(cols, widths)).rstrip(), style="bold #8aa0b8")
    for r in cells:
        line = Text()
        for i, (c, w) in enumerate(zip(r, widths)):
            line.append_text(c)
            if i < len(r) - 1:
                line.append(" " * (w - len(c.plain) + 3))
        out.append("\n")
        out.append_text(line)
    return out


def resolve(obj, path):
    """The value at a path like .metadata.labels.modelplane\\.ai/cluster, or None."""
    for key in re.split(r"(?<!\\)\.", path.strip("{}").lstrip(".")):
        obj = obj.get(key.replace("\\.", ".")) if isinstance(obj, dict) else None
    return obj


def jsonpath(obj, path):
    """A custom-columns cell."""
    v = resolve(obj, path)
    return "<none>" if v is None else str(v)


def jq(value, expr):
    """The two jq filters the walkthrough uses: '.' and '.[] | {a, b}'."""
    expr = expr.strip()
    m = re.fullmatch(r"\.\[\]\s*\|\s*\{\s*([\w\s,]+)\}", expr)
    if m:
        keys = [k.strip() for k in m[1].split(",")]
        return [{k: v.get(k) for k in keys} for v in value]
    if expr == ".":
        return [value]
    raise ValueError(f"jq: this walkthrough only knows '.' and '.[] | {{...}}', not {expr}")


def yes(b):
    return Text("True", "green") if b else Text("False", "yellow")


def fold_args(text):
    """Keep the first few engine flags of each args list and say how many more there are."""
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        out.append(lines[i])
        m = re.match(r"^(\s*)args:\s*$", lines[i])
        i += 1
        if not m:
            continue
        flags = []
        while i < len(lines) and re.match(r"^\s*- ", lines[i]):
            flags.append(lines[i])
            i += 1
        out += flags[:FOLD]
        if len(flags) > FOLD:
            indent = re.match(r"^(\s*)", flags[0])[1]
            out.append(f"{indent}# ... {len(flags) - FOLD} more engine flags (cat --full)")
    return "\n".join(out)


def syntax(text, lexer):
    return Syntax(text, lexer, theme="ansi_dark", background_color="default", word_wrap=True)


class Shell:
    def __init__(self, fleet):
        self.f = fleet
        self.created = {}          # (kind, name) -> when it was applied, for AGE

    def run(self, line):
        line = line.strip()
        for var, value in ENV.items():
            line = line.replace(var, value)
        if not line:
            return
        if line in ("help", "?"):
            yield 0, HELP
            return
        if line.startswith("curl") and "prom.example.internal/api/v1/query" in line:
            yield from self.promql(line)
            return
        loop = re.fullmatch(r"for i in \$\(seq (\d+)\); do (curl .+?)(\s*\|\s*jq -r \.model)?;? done", line)
        if loop:
            for _ in range(int(loop[1])):
                yield from self.curl(shlex.split(loop[2]), model_only=bool(loop[3]))
            return
        try:
            argv = shlex.split(line)
        except ValueError as e:
            yield 0, f"parse error: {e}"
            return
        if "|" in argv and argv[0] == "kubectl" and argv[argv.index("|") + 1:argv.index("|") + 2] == ["jq"]:
            yield from self.kubectl_jq(argv[1:argv.index("|")], argv[argv.index("|") + 2:])
            return
        cmd = argv[0]
        if cmd == "kubectl":
            yield from self.kubectl(argv[1:])
        elif cmd == "cat":
            yield from self.cat(argv[1:])
        elif cmd == "diff":
            yield from self.diff(argv[1:])
        elif cmd == "curl":
            yield from self.curl(argv[1:])
        elif cmd in ("ls", "tree"):
            yield 0, "\n".join(str(p.relative_to(MANIFESTS)) for p in sorted(MANIFESTS.rglob("*.yaml")))
        else:
            yield 0, f"{cmd}: not part of this walkthrough. {HELP}"

    # ---- files -------------------------------------------------------------

    def cat(self, args):
        full = "--full" in args
        for p in [a for a in args if not a.startswith("-")]:
            path = MANIFESTS / p
            if not path.is_file():
                yield 0, f"cat: {p}: No such file or directory"
                continue
            text = path.read_text().rstrip()
            yield 0, syntax(text if full else fold_args(text), "yaml")

    def diff(self, args):
        files = [a for a in args if not a.startswith("-")]
        if len(files) != 2:
            yield 0, "usage: diff <file> <file>"
            return
        a, b = (MANIFESTS / x for x in files)
        if not (a.is_file() and b.is_file()):
            yield 0, "diff: no such file"
            return
        strip = lambda p: [l.split("  #")[0].rstrip() for l in p.read_text().splitlines() if not l.lstrip().startswith("#")]
        d = difflib.unified_diff(strip(a), strip(b), files[0], files[1], n=1, lineterm="")
        yield 0, syntax("\n".join(d), "diff")

    # ---- kubectl -----------------------------------------------------------

    def kubectl(self, a):
        opts, pos = self._opts(a)
        if not pos:
            yield 0, "kubectl: get, apply, scale, patch"
            return
        verb, pos = pos[0], pos[1:]
        if verb == "apply":
            try:
                docs = load(*opts.get("-f", []))
            except FileNotFoundError as e:
                yield 0, f"error: the path \"{e}\" does not exist"
                return
            now = time.monotonic()
            for d in docs:
                self.created.setdefault((d["kind"], d["metadata"]["name"]), now)
            yield from self.f.apply(docs)
        elif verb == "get":
            yield 0, self.get(pos, opts)
        elif verb == "scale":
            yield from self.scale(pos, opts)
        elif verb == "patch":
            yield from self.patch(pos, opts)
        else:
            yield 0, f'error: unknown command "{verb}" for this walkthrough'

    def kubectl_jq(self, a, jq_args):
        """kubectl get <kind> <name> -o jsonpath='{...}' | jq '<filter>'."""
        value = self.jsonpath_value(*self._opts(a)[::-1])
        if isinstance(value, str):
            yield 0, value
            return
        try:
            for v in jq(value, jq_args[-1] if jq_args else "."):
                yield 0, syntax(json.dumps(v, indent=2), "json")
        except ValueError as e:
            yield 0, str(e)

    def jsonpath_value(self, pos, opts):
        """The object at a jsonpath, or an error string."""
        out = (opts.get("-o") or [""])[0]
        if len(pos) < 3 or pos[0] != "get" or not out.startswith("jsonpath="):
            return "usage: kubectl get <kind> <name> -o jsonpath='{...}' | jq '...'"
        res, name = KINDS.get(pos[1].lower()), pos[2]
        if res != "inferenceclusters" or name not in self.f.clusters:
            return f'Error from server (NotFound): {pos[1]} "{name}" not found'
        c = self.f.clusters[name]
        obj = {"metadata": {"name": c.name, "labels": c.labels},
               "spec": self.f.docs[("InferenceCluster", name)]["spec"],
               "status": {"schedulablePools": c.schedulable_pools(), "gateway": {"address": c.address}}}
        return resolve(obj, out.split("=", 1)[1])

    @staticmethod
    def _opts(a):
        opts, pos, i = {}, [], 0
        valued = ("-f", "-n", "-o", "-l", "-p", "-c", "-L", "--type", "--replicas", "--tail", "--context")
        while i < len(a):
            x = a[i]
            if x.startswith("--") and "=" in x:
                k, v = x.split("=", 1)
                opts.setdefault(k, []).append(v)
            elif x in valued:
                opts.setdefault(x, []).append(a[i + 1] if i + 1 < len(a) else "")
                i += 1
            elif x.startswith("-"):
                opts.setdefault(x, []).append(True)
            else:
                pos.append(x)
            i += 1
        return opts, pos

    def age(self, kind, name):
        t = self.created.get((kind, name))
        if t is None:
            return "0s"
        s = int(time.monotonic() - t)
        return f"{s}s" if s < 120 else f"{s // 60}m"

    def get(self, pos, opts):
        if not pos:
            return "error: you must specify the type of resource to get"
        res = KINDS.get(pos[0].lower())
        if not res:
            return f'error: the server doesn\'t have a resource type "{pos[0]}"'
        name = pos[1] if len(pos) > 1 else None
        if opts.get("-o") == ["yaml"]:
            doc = self.f.docs.get((KIND_OF.get(res, ""), name))
            if not doc:
                return f'Error from server (NotFound): {res} "{name}" not found'
            return syntax(fold_args(yaml.safe_dump(doc, sort_keys=False).rstrip()), "yaml")
        sel = dict(kv.split("=", 1) for l in opts.get("-l", []) for kv in l.split(",") if "=" in kv)
        out = (opts.get("-o") or [""])[0]
        if out.startswith("jsonpath="):
            v = self.jsonpath_value(["get", *pos], opts)
            return v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))
        if out.startswith("custom-columns="):
            spec = [c.split(":", 1) for c in out.split("=", 1)[1].split(",")]
            objs = [o for o in self._objects(res)
                    if name in (None, o["metadata"]["name"])
                    and all(o["metadata"]["labels"].get(k) == v for k, v in sel.items())]
            if not objs:
                return "No resources found."
            return table([h for h, _ in spec], [[jsonpath(o, p) for _, p in spec] for o in objs])
        found = getattr(self, "_get_" + res)(name, sel)
        if not found:
            return "No resources found."
        cols, rows = found
        return table(["NAME", *cols, "SYNCED", "READY", "COMPOSITION", "AGE"], rows)

    def _objects(self, res):
        """The composed objects as kubectl would return them, for -o custom-columns."""
        if res == "modelendpoints":
            return [{"metadata": {"name": e["name"], "labels": e["labels"]},
                     "spec": {"origin": e["origin"], "model": e["model"]}} for e in self.f.endpoints()]
        if res == "modelreplicas":
            out = []
            for r in self.f.replicas.values():
                labels = {**self.f.deployments[r.deployment].labels, "modelplane.ai/deployment": r.deployment,
                          "modelplane.ai/cluster": r.cluster}
                out.append({"metadata": {"name": r.name, "labels": labels}, "spec": {"clusterName": r.cluster}})
            return out
        return []

    def _composite(self, kind, name, ready=True, age_of=None):
        return [yes(True), yes(ready), f"{kind.lower()}s.modelplane.ai", self.age(*(age_of or (kind, name)))]

    def _get_inferenceclasses(self, name, sel):
        rows = [[k.name, *self._composite("InferenceClass", k.name)] for k in self.f.classes.values()
                if name in (None, k.name)]
        return rows and ([], rows)

    def _get_inferenceclusters(self, name, sel):
        rows = [[c.name, c.source, c.address if c.ready else "", *self._composite("InferenceCluster", c.name, c.ready)]
                for c in self.f.clusters.values()
                if name in (None, c.name) and all(c.labels.get(k) == v for k, v in sel.items())]
        return rows and (["SOURCE", "GATEWAY"], rows)

    def _get_modeldeployments(self, name, sel):
        rows = []
        for d in self.f.deployments.values():
            if name in (None, d.name) and all(d.labels.get(k) == v for k, v in sel.items()):
                mine = self.f.mine(d)
                ready = sum(r.condition >= 2 for r in mine)
                serving = any(r.serving for r in mine)
                complete = len(mine) == d.replicas and ready == d.replicas
                rows.append([d.name, str(ready), yes(serving), *self._composite("ModelDeployment", d.name, complete)])
        return rows and (["REPLICAS", "SERVING"], rows)

    def _get_modelreplicas(self, name, sel):
        rows = []
        for r in sorted(self.f.replicas.values(), key=lambda r: (r.deployment, r.order)):
            labels = {**self.f.deployments[r.deployment].labels, "modelplane.ai/deployment": r.deployment,
                      "modelplane.ai/cluster": r.cluster}
            if name in (None, r.name) and all(labels.get(k) == v for k, v in sel.items()):
                rows.append([r.name, r.cluster, yes(r.serving),
                             *self._composite("ModelReplica", r.name, r.condition >= 2, ("ModelDeployment", r.deployment))])
        return rows and (["CLUSTER", "SERVING"], rows)

    def _get_modelendpoints(self, name, sel):
        rows = [[e["name"], e["origin"], e["model"],
                 *self._composite("ModelEndpoint", e["name"], True, ("ModelDeployment", e["replica"].deployment))]
                for e in self.f.endpoints()
                if name in (None, e["name"]) and all(e["labels"].get(k) == v for k, v in sel.items())]
        return rows and (["ORIGIN", "MODEL"], rows)

    def _get_modelservices(self, name, sel):
        rows = [[s["metadata"]["name"], key, *self._composite("ModelService", s["metadata"]["name"])]
                for key, s in self.f.services.items() if name in (None, s["metadata"]["name"])]
        return rows and (["MODEL"], rows)

    def _get_inferencegateways(self, name, sel):
        rows = [[g, s["clusterName"], s["address"], *self._composite("InferenceGateway", g)]
                for g, s in self.f.gateways.items() if name in (None, g)]
        return rows and (["CLUSTER", "ADDRESS"], rows)

    def _get_modelcaches(self, name, sel):
        rows = []
        for n, s in self.f.caches.items():
            if name in (None, n):
                staged = sum(n in c.caches for c in self.f.clusters.values())
                total = sum(c.cache_storage for c in self.f.clusters.values())
                rows.append([n, s["huggingFace"]["repo"], f"{staged}/{total}", *self._composite("ModelCache", n)])
        return rows and (["REPO", "STAGED"], rows)

    def _get_telemetrydestinations(self, name, sel):
        if not self.f.telemetry:
            return []
        return [], [["fleet", *self._composite("TelemetryDestination", "fleet")]]

    def scale(self, pos, opts):
        if len(pos) < 2 or "--replicas" not in opts or KINDS.get(pos[0]) != "modeldeployments":
            yield 0, "usage: kubectl scale md <name> --replicas N"
            return
        if pos[1] not in self.f.deployments:
            yield 0, f'Error from server (NotFound): modeldeployments "{pos[1]}" not found'
            return
        yield 0, f"modeldeployment.modelplane.ai/{pos[1]} scaled"
        yield from self.f.scale(pos[1], int(opts["--replicas"][0]))

    def patch(self, pos, opts):
        if len(pos) < 2 or "-p" not in opts:
            yield 0, "usage: kubectl patch <resource> <name> --type merge -p '<json>'"
            return
        res, name = KINDS.get(pos[0]), pos[1]
        try:
            body = json.loads(opts["-p"][0])
        except json.JSONDecodeError as e:
            yield 0, f"error: invalid patch: {e}"
            return
        if res == "inferenceclusters" and name in self.f.clusters:
            self.f.docs[("InferenceCluster", name)]["spec"]["taints"] = body["spec"]["taints"]
            yield 0, f"inferencecluster.modelplane.ai/{name} patched"
            yield from self.f.taint(name, body["spec"].get("taints", []))
        elif res == "modeldeployments" and name in self.f.deployments:
            labels = body["spec"]["template"]["metadata"]["labels"]
            self.f.docs[("ModelDeployment", name)]["spec"]["template"]["metadata"]["labels"].update(labels)
            yield 0, f"modeldeployment.modelplane.ai/{name} patched"
            yield from self.f.relabel(name, labels)
        else:
            yield 0, f'Error from server (NotFound): {pos[0]} "{name}" not found'

    def promql(self, line):
        """A Prometheus instant query for prefix-cache hit rate by deployment, from the fleet's sink.

        The series and labels are Modelplane's (platform/telemetry.md); the values are the
        simulator's, made up for illustration, not measurements.
        """
        if "modelplane_prefix_cache_hits_total" not in line:
            yield 0, "simulator: this walkthrough only answers the prefix-cache hit rate query"
            return
        serving = sorted({e["replica"].deployment for e in self.f.endpoints()})
        if not self.f.telemetry or not serving:
            yield 0, '{"status":"success","data":{"resultType":"vector","result":[]}}'
            return
        yield 0.4, None
        for d in serving:
            rate = ILLUSTRATIVE_HIT_RATE[self.f.deployments[d].engine]
            yield 0.1, Text.assemble((f"{d:<12}", "bold #f59e0b" if "sglang" in d else "bold #22d3ee"), f"{rate:.2f}")
        yield 0, Text("simulator: illustrative values, not measurements", NOTE)

    # ---- curl --------------------------------------------------------------

    def curl(self, a, model_only=False):
        url = next((x for x in a if x.startswith("http")), None)
        m = re.match(r"https://(\w+)\.gw\.example\.com(/.*)", url or "")
        if not m:
            yield 0, "curl: (3) URL rejected: use $GW_US/v1/chat/completions"
            return
        gw, path = m[1], m[2]
        data = a[a.index("-d") + 1] if "-d" in a else "{}"
        try:
            body = json.loads(data)
        except json.JSONDecodeError:
            body = {}
        model = body.get("model", "")
        ep, err = self.f.route(gw, model)
        yield 0.25, None
        if err:
            yield 0, err if not err.startswith("Could") else f"curl: (6) {err}"
            return
        r = ep["replica"]
        served = ep["model"]          # the response names the deployment that served: <namespace>/<deployment>
        note = Text(f"   simulator: replica {r.name} on {r.cluster}", NOTE)
        if model_only:
            yield 0.3, Text.assemble((served, "bold #f59e0b" if "sglang" in served else "bold #22d3ee"), note)
            return
        if path.startswith("/anthropic"):
            resp = {"type": "message", "model": served, "role": "assistant",
                    "content": [{"type": "text", "text": "Hello! What are we building today?"}],
                    "usage": {"input_tokens": 18, "output_tokens": 11}}
        else:
            resp = {"object": "chat.completion", "model": served,
                    "choices": [{"message": {"role": "assistant", "content": "Hello! What are we building today?"}}],
                    "usage": {"prompt_tokens": 18, "completion_tokens": 11}}
        yield 0, syntax(json.dumps(resp, indent=2), "json")
        yield 0.3, note
