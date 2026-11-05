# Talk track

Generated from `walkthrough/story.yaml` by `uv run walkthrough/walkthrough.py --talk-track`. Edit the story, not this file.

## Opening

**Your GPUs span clouds and vendors, each cluster set up its own way. Your ML teams want one endpoint.**

A simulated fleet, so no cluster is needed. The commands and the objects are Modelplane's own; the map on the right is what they do.

## Set up the inference fleet

*WALKTHROUGH · 1 OF 3*

**The fleet is two clouds, described in two files.**

1. *PLATFORM*: **InferenceClass: a type of GPU node, described once.** Models never name hardware. They ask for devices like these.

   `cat platform/classes.yaml`

   Modelplane runs on a control cluster that needs no GPUs, and everything starts with the platform team. An InferenceClass describes one kind of GPU machine: here, an EKS p5en with eight H200s at 141GB each, and a bring-your-own node with eight B200s. It's written once. ML teams never name a machine type; they ask for devices, and these are the devices they can get.

2. *PLATFORM*: **InferenceCluster: one Kubernetes cluster in the fleet.** It says which class its nodes are and how many. Modelplane can create it or adopt yours.

   `cat platform/clusters.yaml`

   An InferenceCluster is one cluster in the fleet. Its node pool points at a class, so a node is "an h200-8x", and says how many nodes there are. The AWS one has source EKS, so Modelplane creates it. The CoreWeave one has source Existing: you already run it, and hand it over with a kubeconfig and a storage class for model weights.

3. *PLATFORM*: **Apply both, and Modelplane provisions the fleet.** It creates the EKS cluster and installs its serving stack on both clusters.

   `kubectl apply -f platform/`

   That's the whole job for the platform team. Applying the files is all it takes: Modelplane provisions the EKS cluster and its pool, and installs its serving stack, including a gateway, on both clusters. Watch the provisioning chip and the two clusters appear.

## Deploy a model

*WALKTHROUGH · 2 OF 3*

**The model asks for what it needs. Modelplane finds where it fits.**

1. *ML TEAM*: **ModelCache names the weights; ModelDeployment is the ML team's side.** An image, engine flags and a request for 8 GPUs with 140Gi each. No cloud, no node.

   `cat model/cache.yaml model/glm.yaml`

   Now the ML team, with two objects. A ModelCache names the weights to fetch from Hugging Face. A ModelDeployment is like a Kubernetes Deployment, but its replicas land on clusters instead of nodes. It references the cache, names the engine, vLLM here, and makes a request written against device attributes: eight GPUs with at least 140Gi each, on a cluster labelled geo=us. It says nothing about AWS, CoreWeave or machine types.

2. *ML TEAM*: **ModelCache stages the weights, then the scheduler places replicas.** Weights are fetched once per cluster. Replicas spread across clusters before packing.

   `kubectl apply -f model/`

   Two things happen. The ModelCache pulls the weights once into each cluster's shared storage, so pods don't download 700 gigabytes on every start; that's the caching chip. Then the scheduler matches the request against the classes, and places the two replicas on different clusters first; that's scheduling. Watch a whole node of each cluster fill.

3. *ML TEAM*: **ModelReplica: one serving copy, pinned to a cluster.** Like a Pod pinned to a node. Nobody picked these clusters.

   `kubectl get modelreplicas -n coding-team`

   Each replica is a ModelReplica, pinned to one cluster the way a Pod is pinned to a node. One on AWS, one on CoreWeave, and nobody chose. SERVING says each is up.

4. *ML TEAM*: **ModelEndpoint: where a serving replica can be reached.** Each carries its deployment's labels. The next chapter routes by them.

   `kubectl get modelendpoints -n coding-team -o custom-columns='NAME:.metadata.name,MODEL:.spec.model,CLUSTER:.metadata.labels.modelplane\.ai/cluster,APP:.metadata.labels.app'`

   Every serving replica also gets a ModelEndpoint: an address for it, labelled with its cluster and with the deployment's own labels, like app=glm. Those labels are what the next chapter routes on.

## Scale the fleet

*WALKTHROUGH · 3 OF 3*

**The fleet changed. The name didn't.**

1. *PLATFORM + ML TEAM*: **ModelService names the model; InferenceGateway is the front door.** The service selects endpoints labelled app=glm. The gateway serves services labelled exposure=public.

   `cat routing/service.yaml routing/gateway-us.yaml`

   A ModelService is to endpoints what a Kubernetes Service is to Pods: it selects everything labelled app=glm and gives it one name, coding-team/glm, its namespace and name. An InferenceGateway is the front door. It runs on a cluster you name, checks an API key per caller, and serves every ModelService whose labels match its serviceSelector.

2. *PLATFORM + ML TEAM*: **Apply them, and the model has an address.** The gateway serves coding-team/glm on the OpenAI and Anthropic APIs.

   `kubectl apply -f routing/`

   Applying them lights the routing chip: the gateway comes up on the AWS cluster and publishes coding-team/glm on the OpenAI API at /v1 and Anthropic's Messages API at /anthropic/v1.

3. *ML TEAM*: **One request, by name. The response says which deployment answered.** For Claude Code, set ANTHROPIC_BASE_URL=$GW_US/anthropic.

   `curl $GW_US/anthropic/v1/messages -H "x-api-key: $BOB_KEY" -H "anthropic-version: 2023-06-01" -d '{"model":"coding-team/glm","max_tokens":256,"messages":[{"role":"user","content":"hello"}]}'`

   A caller only knows the name. This is the Anthropic Messages API, so Claude Code works by pointing ANTHROPIC_BASE_URL at the gateway; OpenAI SDKs use /v1 the same way. The response names the deployment that served it, and the map marks the replica.

4. *ML TEAM*: **Scale it like any Deployment.** The new replica goes where there's room. KEDA can drive the same knob.

   `kubectl scale md glm -n coding-team --replicas 3`

   More traffic: kubectl scale, the same scale subresource any Deployment has, so KEDA can drive it too. That's autoscaling. Each cluster already has one replica, so the third goes to the cluster with more room.

5. *PLATFORM*: **Grow the fleet with one more InferenceCluster.** Europe, on AMD, with its own gateway. The weights stage there by themselves.

   `kubectl apply -f grow/vultr-ams.yaml`

   Growing the fleet is the same move as chapter one: a class for AMD MI325X nodes, an InferenceCluster in Amsterdam, and a gateway there. The cache from chapter two stages onto it without anyone asking.

6. *ML TEAM*: **The same model on AMD is a second ModelDeployment.** A ROCm image, an AMD device request, and app=glm, so the service picks it up unchanged.

   `kubectl apply -f grow/glm-eu.yaml`

   AMD needs a ROCm build of vLLM, so this is its own ModelDeployment, pinned to Europe and asking for AMD devices. It carries app=glm, so the ModelService from a minute ago includes it. Nothing on the routing side changed.

7. *ML TEAM*: **The same request, eight times, on the OpenAI API.** Answered by two deployments, on three clouds and two GPU vendors.

   `for i in $(seq 8); do curl -s $GW_US/v1/chat/completions -H "Authorization: Bearer $ALICE_KEY" -d '{"model":"coding-team/glm","messages":[]}' | jq -r .model; done`

   Same name, same request, now on the OpenAI API. The answers come from both deployments, across AWS, CoreWeave and Vultr, NVIDIA and AMD. The fleet changed three times; the caller's code didn't.

## Operate the fleet

*ENCORE · DAY 2*

**Measure, try a new engine, promote on evidence. Drain a cluster like a node.**

1. *PLATFORM*: **TelemetryDestination: where the fleet's metrics go.** One object, one sink. Here, your Prometheus.

   `cat encore/telemetry.yaml`

   Day two starts with measuring, before changing anything. A TelemetryDestination says where the fleet's metrics go: a list of sinks, each one an OpenTelemetry exporter. This one writes to your Prometheus; an OTLP endpoint works the same way.

2. *PLATFORM*: **Apply it, and a collector starts on every cluster.** Every engine's metrics get the same modelplane_* names, labelled by deployment and cluster.

   `kubectl apply -f encore/telemetry.yaml`

   Applying it starts a collector on every cluster in the fleet. Each one scrapes the engines there and renames their metrics to the same modelplane_* names, labelled with deployment, cluster and replica. That's what makes the next comparison possible: two different engines, measured on the same terms.

3. *ML TEAM*: **Trying a new engine is a new ModelDeployment.** SGLang this time, the same device request, labelled env=canary. The vLLM one keeps running.

   `cat encore/glm-sglang.yaml`

   The ML team wants to try SGLang, whose prefix cache suits agents that resend the same long context every turn. They don't touch the running deployment; they add a second one beside it, with the same device request, labelled env=canary so it gets no production traffic until the service says so.

4. *ML TEAM*: **The same service, split by label.** env=prod at weight 90, env=canary at weight 10, under the same name.

   `diff routing/service.yaml encore/service-canary.yaml`

   To send it a little traffic, the ModelService gets two entries instead of one: endpoints labelled env=prod at weight 90, and env=canary at weight 10. The name stays coding-team/glm.

5. *ML TEAM*: **Apply both. The canary deploys and gets its share.** Its two replicas spread across clusters. Callers still ask for coding-team/glm.

   `kubectl apply -f encore/glm-sglang.yaml -f encore/service-canary.yaml`

   Apply the new deployment and the new service together. The SGLang replicas go wherever there's room, spread across clusters like before, and as soon as they serve, the canary entry routes to them.

6. *ML TEAM*: **Ten requests, same name.** About one in ten reaches SGLang.

   `for i in $(seq 10); do curl -s $GW_US/v1/chat/completions -H "Authorization: Bearer $ALICE_KEY" -d '{"model":"coding-team/glm","messages":[]}' | jq -r .model; done`

   About one request in ten lands on the SGLang deployment, and the response says so. Now there's real traffic on both engines to compare.

7. *ML TEAM*: **Compare the engines on the same metric.** Prefix-cache hit rate by deployment. Same name, same labels, whichever engine reports it.

   `curl -s $PROM/api/v1/query --data-urlencode 'query=sum by (deployment) (rate(modelplane_prefix_cache_hits_total[10m])) / sum by (deployment) (rate(modelplane_prefix_cache_lookups_total[10m]))' | jq -r '.data.result[] | "\(.metric.deployment) \(.value[1])"'`

   One query in your Prometheus: prefix-cache hit rate, by deployment. vLLM and SGLang name this counter differently, but the collector maps both to modelplane_prefix_cache_hits_total, so this is a like-for-like comparison. The numbers here are the simulator's; the point is that the question takes one query.

8. *ML TEAM*: **Promote it by changing one label.** Its endpoints now carry env=prod, so they get production traffic.

   `kubectl patch md glm-sglang -n coding-team --type merge -p '{"spec":{"template":{"metadata":{"labels":{"env":"prod"}}}}}'`

   The evidence says go. Promotion is a label: change the deployment's env label to prod, its endpoints carry the new label, and the prod entry of the service now includes them.

9. *ML TEAM*: **Retire the US vLLM deployment.** Its GPUs free up. Europe's AMD deployment is its own, so it keeps serving on vLLM.

   `kubectl scale md glm -n coding-team --replicas 0`

   Scale the US vLLM deployment to zero and its GPUs come back to the pool. Notice Amsterdam keeps serving: glm-eu is a separate deployment, a ROCm build of vLLM on AMD, and it's still env=prod. The canary was an NVIDIA image, so each hardware type runs the engine that suits it, under one name. Rolling back is the same two commands the other way round.

10. *PLATFORM*: **Drain a whole cluster with a taint.** Each replica there is deleted and a new one placed where it still fits.

   `kubectl patch inferencecluster coreweave-us-west --type merge -p '{"spec":{"taints":[{"key":"modelplane.ai/maintenance","effect":"NoExecute"}]}}'`

   Last, CoreWeave needs maintenance. A NoExecute taint on the InferenceCluster works like draining a node: Modelplane deletes each replica there and places a new one on a cluster that fits. Remove the taint and the cluster takes work again; nothing moves back by itself.

## Close

**Any model. Any engine. Any infrastructure. One API.**

- provisioning: clusters and hardware declared once, across clouds
- scheduling: models ask for devices; Modelplane finds where they fit
- caching: weights staged once per cluster
- routing: one name, OpenAI and Anthropic APIs, canary by label
- autoscaling: kubectl scale or KEDA; callers keep one name
