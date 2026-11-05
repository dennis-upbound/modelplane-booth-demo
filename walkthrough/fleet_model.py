"""The fleet the walkthrough shows: Modelplane objects in, placement and routing out.

Nothing here talks to a cluster. apply() takes the manifests a real control plane
would, and behaves the way docs.modelplane.ai describes (v0.6):

- scheduling (architecture/scheduling.md): keep every replica where it is, then
  place each new one on the eligible cluster with the fewest of its deployment's
  replicas, spreading before packing; the pod binds to the node with the most of
  its claimed devices free. A replica that fits nowhere isn't created; the
  deployment reports the shortfall. Scaling down drops the highest index first.
- caching (models/model-cache.md): a ModelCache with no clusterSelector stages on
  every cluster with cache storage, and a deployment that references it places
  only on clusters it has staged to.
- draining (platform/drain-cluster.md): a NoExecute taint deletes each replica
  on the cluster and creates a new one wherever it fits.
- names (compose-model-deployment name.py): a replica is <deployment>-<5 hex>,
  hashed from deployment, cluster and index.

Every change is a generator step, so the app can redraw the map between them.
"""
import hashlib, re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
MANIFESTS = ROOT / "manifests"
CHIPS = ("provisioning", "scheduling", "autoscaling", "caching", "routing")
PROVIDERS = {"aws": "AWS", "coreweave": "CoreWeave", "vultr": "Vultr"}
CONDITIONS = ("ModelAccepted", "ModelServing", "ModelReady")    # each implies the one before


def gib(v):
    """'143771Mi' or '140Gi' as GiB."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(Mi|Gi|Ti)\s*", str(v))
    if not m:
        raise ValueError(f"not a quantity: {v}")
    return float(m[1]) * {"Mi": 1 / 1024, "Gi": 1, "Ti": 1024}[m[2]]


def opaque_name(visible, *parts):
    """visible-<5 hex>, hashed from visible and parts, as Modelplane names composed resources."""
    return f"{visible}-{hashlib.sha256('-'.join((visible, *parts)).encode()).hexdigest()[:5]}"


@dataclass
class InferenceClass:
    name: str
    driver: str
    count: int
    memory: float
    description: str = ""

    @property
    def vendor(self):
        return "AMD" if "amd" in self.driver else "NVIDIA"


@dataclass
class Node:
    name: str
    cells: list          # one entry per GPU: the replica name holding it, or None

    def free(self):
        return sum(c is None for c in self.cells)


@dataclass
class Pool:
    name: str
    klass: InferenceClass
    nodes: list


@dataclass
class Cluster:
    name: str
    source: str
    labels: dict
    pools: list
    cache_storage: bool
    address: str
    ready: bool = False
    taints: list = field(default_factory=list)
    caches: set = field(default_factory=set)

    @property
    def provider(self):
        return PROVIDERS.get(self.name.split("-")[0], self.name.split("-")[0])

    @property
    def region(self):
        return "-".join(self.name.split("-")[1:])

    @property
    def vendor(self):
        return "/".join(sorted({p.klass.vendor for p in self.pools}))

    @property
    def gateway_hostname(self):
        """The internal name InferenceGateways reach this cluster's gateway by."""
        return f"{opaque_name('gateway-' + self.name)}.modelplane-system.svc.cluster.local"

    def blocked(self):
        return any(t.get("effect") in ("NoSchedule", "NoExecute") for t in self.taints)

    def schedulable_pools(self):
        """status.schedulablePools: per pool, its nodes and a histogram of them by free devices.

        The cluster agent observes only NVIDIA pools, so other pools carry no observedNodes
        or nodesByFreeDevices.
        """
        out = []
        for p in self.pools:
            pool = {"name": p.name, "nodes": len(p.nodes)}
            if p.klass.vendor == "NVIDIA":
                buckets = {}
                for n in p.nodes:
                    buckets[n.free()] = buckets.get(n.free(), 0) + 1
                pool["observedNodes"] = len(p.nodes)
                pool["nodesByFreeDevices"] = [{"freeDevices": {p.klass.driver: free}, "nodes": count}
                                              for free, count in sorted(buckets.items(), reverse=True)]
            out.append(pool)
        return out


@dataclass
class Deployment:
    name: str
    namespace: str
    replicas: int
    labels: dict
    cluster_selector: dict
    driver: str
    memory: float
    count: int
    image: str
    engine_name: str
    cache: str | None

    @property
    def engine(self):
        return "SGLang" if "sglang" in self.image else "vLLM"

    @property
    def served_model(self):
        return f"{self.namespace}/{self.name}"


@dataclass
class Replica:
    name: str
    deployment: str
    cluster: str
    node: str
    index: int                     # its index on that cluster
    order: int                     # creation order across the deployment, for scale-down
    condition: int = -1            # how far through CONDITIONS it has got

    @property
    def serving(self):
        return self.condition >= 1

    @property
    def status(self):
        return "Ready" if self.condition >= 2 else ("waiting on " + CONDITIONS[self.condition + 1])


def load(*paths):
    """The YAML documents at these paths (files or directories), relative to the walkthrough manifests."""
    docs = []
    for p in paths:
        path = (MANIFESTS / p) if not Path(p).is_absolute() else Path(p)
        files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
        if not files or not all(f.exists() for f in files):
            raise FileNotFoundError(p)
        for f in files:
            docs += [d for d in yaml.safe_load_all(f.read_text()) if d]
    return docs


def device_request(spec):
    """Driver, minimum memory and count from a ModelDeployment's first device request."""
    dev = spec["engines"][0]["members"][0]["nodeSelector"]["devices"][0]
    cel = " ".join(s["cel"] for s in dev.get("selectors", []))
    m = re.search(r'capacity\["([^"]+)"\]\.memory\.compareTo\(quantity\("([^"]+)"\)\)', cel)
    return (m[1], gib(m[2])) if m else ("gpu.nvidia.com", 0.0), dev.get("count", 1)


class Fleet:
    def __init__(self):
        self.classes, self.clusters, self.deployments = {}, {}, {}
        self.replicas, self.services, self.gateways, self.caches = {}, {}, {}, {}
        self.docs = {}                 # (kind, name) -> the manifest as applied
        self.telemetry = False
        self.chips = set()             # what the control plane is doing right now
        self.pulse = None              # the replica that served the last request
        self._swrr = {}                # smooth weighted round robin state per service
        self._order = 0

    # ---- apply -------------------------------------------------------------

    def apply(self, docs):
        """Store each object, print what kubectl prints, then reconcile. Yields (seconds, line or None)."""
        kinds = set()
        for d in docs:
            kind, name = d["kind"], d["metadata"]["name"]
            verb = "configured" if (kind, name) in self.docs else "created"
            self.docs[(kind, name)] = d
            getattr(self, "_set_" + kind)(d)
            kinds.add(kind)
            yield 0.15, f"{kind.lower()}.modelplane.ai/{name} {verb}"
        if "InferenceCluster" in kinds:
            yield from self._provision()
        if kinds & {"ModelCache", "InferenceCluster"}:
            yield from self._stage()
        if kinds & {"ModelDeployment", "InferenceCluster", "ModelCache"}:
            yield from self.reconcile()
        if kinds & {"ModelService", "InferenceGateway"}:
            self.chips = {"routing"}
            yield 0.8, None
        if "TelemetryDestination" in kinds:
            self.telemetry = True
            yield 0.6, None
        self.chips = set()
        yield 0, None

    def _set_InferenceClass(self, d):
        s, dev = d["spec"], d["spec"]["devices"][0]
        self.classes[d["metadata"]["name"]] = InferenceClass(
            d["metadata"]["name"], dev["driver"], dev["count"], gib(dev["capacity"]["memory"]["value"]),
            s.get("description", ""))

    def _set_InferenceCluster(self, d):
        name, s = d["metadata"]["name"], d["spec"]
        if name in self.clusters:
            self.clusters[name].labels = d["metadata"].get("labels", {})
            self.set_taints(name, s.get("taints", []))
            return
        pools = []
        for p in s["nodePools"]:
            k = self.classes[p["className"]]
            pools.append(Pool(p["name"], k, [Node(f"node-{i + 1}", [None] * k.count) for i in range(p["nodeCount"])]))
        source = s["cluster"]["source"]
        # Provisioned clusters get cache storage from Modelplane; on Existing you name an RWX class.
        storage = source != "Existing" or bool(s["cluster"].get("existing", {}).get("cache", {}).get("storageClassName"))
        address = f"203.0.113.{10 + len(self.clusters)}"       # documentation range: stands in for a load balancer
        self.clusters[name] = Cluster(name, source, d["metadata"].get("labels", {}), pools, storage, address,
                                      taints=list(s.get("taints", [])))

    def _set_ModelDeployment(self, d):
        m, s = d["metadata"], d["spec"]
        t = s["template"]["spec"]
        (driver, mem), count = device_request(t)
        self.deployments[m["name"]] = Deployment(
            m["name"], m.get("namespace", "default"), s.get("replicas", 1),
            dict(s["template"].get("metadata", {}).get("labels", {})),
            t.get("clusterSelector", {}).get("matchLabels", {}), driver, mem, count,
            t["engines"][0]["members"][0]["template"]["spec"]["containers"][0]["image"],
            t["engines"][0]["name"], (t.get("modelCacheRef") or {}).get("name"))

    def _set_ModelCache(self, d):
        self.caches[d["metadata"]["name"]] = d["spec"]

    def _set_ModelService(self, d):
        m = d["metadata"]
        key = f'{m.get("namespace", "default")}/{m["name"]}'
        self.services[key] = d
        self._swrr.pop(key, None)

    def _set_InferenceGateway(self, d):
        self.gateways[d["metadata"]["name"]] = {**d["spec"], "address": f"198.51.100.{10 + len(self.gateways)}"}

    def _set_TelemetryDestination(self, d):
        pass

    def _provision(self):
        new = [c for c in self.clusters.values() if not c.ready]
        if not new:
            return
        self.chips = {"provisioning"}
        yield 0.9, None
        for c in new:
            c.ready = True
            yield 0.7, None
        self.chips = set()

    def _stage(self):
        """Stage every cache on every ready cluster that has cache storage and doesn't have it yet."""
        todo = [(c, n) for n in self.caches for c in self.clusters.values()
                if c.ready and c.cache_storage and n not in c.caches]
        if not todo:
            return
        self.chips = {"caching"}
        for c, n in todo:
            yield 1.0, None
            c.caches.add(n)
        self.chips = set()

    # ---- scheduling --------------------------------------------------------

    def eligible(self, dep, cluster):
        """The nodes on this cluster a new replica of dep could bind to."""
        if not cluster.ready or cluster.blocked():
            return []
        if any(cluster.labels.get(k) != v for k, v in dep.cluster_selector.items()):
            return []
        if dep.cache and dep.cache not in cluster.caches:
            return []
        out = []
        for pool in cluster.pools:
            k = pool.klass
            if k.driver != dep.driver or k.memory < dep.memory or k.count < dep.count:
                continue
            out += [n for n in pool.nodes if n.free() >= dep.count]
        return out

    def mine(self, dep):
        return [r for r in self.replicas.values() if r.deployment == dep.name]

    def place(self, dep):
        """The eligible cluster with the fewest of this deployment's replicas, then the node with the most free GPUs."""
        order = list(self.clusters)
        cands = [(c, self.eligible(dep, c)) for c in self.clusters.values()]
        cands = [(c, n) for c, n in cands if n]
        if not cands:
            return None
        on = lambda c: sum(r.cluster == c.name for r in self.mine(dep))
        # Ties between equally loaded clusters: the order they were applied in (the docs leave it open).
        c, nodes = min(cands, key=lambda cn: (on(cn[0]), order.index(cn[0].name)))
        return c, max(nodes, key=lambda n: n.free())

    def _free(self, r):
        for c in self.clusters.values():
            for p in c.pools:
                for n in p.nodes:
                    n.cells = [None if x == r.name else x for x in n.cells]

    def _create(self, dep, c, node):
        used = {r.index for r in self.mine(dep) if r.cluster == c.name}
        index = next(i for i in range(len(used) + 1) if i not in used)
        self._order += 1
        r = Replica(opaque_name(dep.name, c.name, str(index)), dep.name, c.name, node.name, index, self._order)
        left = dep.count
        for i, x in enumerate(node.cells):
            if x is None and left:
                node.cells[i] = r.name
                left -= 1
        self.replicas[r.name] = r
        return r

    def shortfall(self, dep):
        return max(0, dep.replicas - len(self.mine(dep)))

    def reconcile(self):
        """Bring every deployment to its replica count, then let new replicas come up."""
        new = []
        for dep in list(self.deployments.values()):
            for r in sorted(self.mine(dep), key=lambda r: r.order)[dep.replicas:][::-1]:
                self.chips |= {"autoscaling"}
                self._free(r)
                del self.replicas[r.name]
                yield 0.5, None
            while self.shortfall(dep):
                spot = self.place(dep)
                if not spot:
                    break
                self.chips |= {"scheduling"}
                new.append(self._create(dep, *spot))
                yield 0.6, None
        for step in range(len(CONDITIONS)):
            for r in new:
                if r.name in self.replicas:
                    r.condition = step
            if new:
                yield 0.4, None

    def scale(self, name, n):
        self.deployments[name].replicas = n
        self.docs[("ModelDeployment", name)]["spec"]["replicas"] = n
        self.chips = {"autoscaling"}
        yield 0.5, None
        yield from self.reconcile()
        self.chips = set()
        yield 0, None

    def set_taints(self, name, taints):
        c = self.clusters[name]
        c.taints = list(taints)
        if any(t.get("effect") == "NoExecute" for t in taints):
            for r in [r for r in self.replicas.values() if r.cluster == name]:
                self._free(r)
                del self.replicas[r.name]

    def taint(self, name, taints):
        self.set_taints(name, taints)
        self.chips = {"scheduling"}
        yield 0.6, None
        yield from self.reconcile()
        self.chips = set()
        yield 0, None

    def relabel(self, name, labels):
        self.deployments[name].labels.update(labels)
        self.chips = {"routing"}
        yield 0.8, None
        self.chips = set()
        yield 0, None

    # ---- what kubectl and the gateway see ------------------------------------

    def endpoints(self):
        """One ModelEndpoint per serving replica, carrying its deployment's labels."""
        out = []
        for r in sorted(self.replicas.values(), key=lambda r: (r.deployment, r.order)):
            if r.serving:
                dep, c = self.deployments[r.deployment], self.clusters[r.cluster]
                labels = {**dep.labels, "modelplane.ai/deployment": dep.name, "modelplane.ai/cluster": r.cluster}
                out.append({"name": r.name, "replica": r, "labels": labels, "namespace": dep.namespace,
                            "origin": f"https://{c.gateway_hostname}", "model": dep.served_model})
        return out

    def service_endpoints(self, svc_doc):
        """Each entry of a ModelService with the endpoints it selects."""
        eps = self.endpoints()
        out = []
        for e in svc_doc["spec"]["endpoints"]:
            want = e.get("selector", {}).get("matchLabels", {})
            out.append((e, [x for x in eps if all(x["labels"].get(k) == v for k, v in want.items())]))
        return out

    def gateway_serves(self, gw, svc_doc):
        want = self.gateways[gw].get("serviceSelector", {}).get("matchLabels", {})
        have = svc_doc["metadata"].get("labels", {})
        return all(have.get(k) == v for k, v in want.items())

    def route(self, gw, model):
        """The endpoint a request for this model lands on, or an error string."""
        if gw not in self.gateways:
            return None, f"Could not resolve host: {gw}.gw.example.com"
        svc = self.services.get(model)
        if not svc or not self.gateway_serves(gw, svc):
            return None, f'{{"error":{{"message":"model {model} not found","type":"not_found"}}}}'
        entries = self.service_endpoints(svc)
        for prio in sorted({e.get("priority", 0) for e, _ in entries}):
            live = [(e, s) for e, s in entries if e.get("priority", 0) == prio and s]
            if not live:
                continue
            # Weighted split. The simulator uses smooth weighted round robin, so a 90/10
            # split sends exactly one of any ten requests to the 10; real traffic is about that.
            st = self._swrr.setdefault(model, {"cur": {}, "rr": {}})
            total = sum(e.get("weight", 1) for e, _ in live)
            for e, _ in live:
                st["cur"][e["name"]] = st["cur"].get(e["name"], 0) + e.get("weight", 1)
            e, sel = max(live, key=lambda es: st["cur"][es[0]["name"]])
            st["cur"][e["name"]] -= total
            k = st["rr"].get(e["name"], 0)
            st["rr"][e["name"]] = k + 1
            ep = sel[k % len(sel)]
            self.pulse = ep["name"]
            return ep, None
        return None, '{"error":{"message":"no healthy endpoints","type":"unavailable"}}'
