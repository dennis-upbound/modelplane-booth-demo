# Modelplane booth demo

A terminal app that plays the [Modelplane](https://github.com/modelplaneai/modelplane)
booth demo: one model runs on two clouds behind one endpoint, then a cluster
goes down for maintenance. Each command and output comes from a real two-cloud
fleet, as Modelplane v0.6 prints it. The app runs offline, without a cluster or
an account.

<img src="https://github.com/dennis-upbound/modelplane-booth-demo/releases/latest/download/booth-demo.gif" width="960" alt="The booth demo">

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/dennis-upbound/modelplane-booth-demo/main/install.sh | sh
```

The script downloads the binary for your machine from the latest release,
checks its SHA-256, and puts it in `~/.local/bin` (set `BIN_DIR` to change
that). Releases have builds for macOS on Apple silicon and Linux on amd64 and
arm64. Anywhere else, with [uv](https://docs.astral.sh/uv/):

```bash
uv run https://raw.githubusercontent.com/dennis-upbound/modelplane-booth-demo/main/modelplane_demo.py
```

## Run

```bash
modelplane-demo          # you drive: space or Enter at each step
modelplane-demo --loop   # plays itself and starts again, for a booth screen
modelplane-demo --live   # runs the read-only commands on your current kube context
```

Space, Enter or the right arrow moves on. `b` or the left arrow goes back a
screen, and `q` quits. Every screen fits in 80 × 25, so make the font as large
as fits. `MPDEMO_PACE` scales the typing and pauses: `0` skips them and `2`
halves the speed.

The talk track, with what to say at each screen, is in the booth demo gist.

## The demo

1. **Fleet.** The GPU node classes, the two clusters (EKS and GKE) and the
   gateway.
2. **Serve.** The ModelDeployment the ML team writes, the replica Modelplane
   placed on each cloud, and a streamed request to the one endpoint.
3. **Drain.** A `NoExecute` taint on the EKS cluster. Its endpoint goes, then
   its engine, then its replica. The same request still works, and a
   background client's requests all return 200.
4. **Undrain.** The taint comes off, and the waiting replica goes back to EKS.

## Live mode

With `--live`, the app runs the read-only commands for real: `kubectl get` for
the classes, clusters, gateway and replicas, and the streamed `curl` once you
set `GATEWAY` and `KEY`. Your fleet needs the demo's names
(`eks-us-east`, `gke-us-central`, `ml-team/qwen3-8b`) for the output to match
the story. The app never runs the taint patches. It replays them, along with the
screens that follow them. A command that fails shows the recorded output
instead.

## Sources

The commands and outputs come from recordings of the Cloud Native Live demo
fleet on Oct 27, 2026, which ran Modelplane v0.5.0. Where v0.6.0 prints
something different, the app shows the v0.6.0 version:

- `kubectl get modelreplicas` has the `SERVING` column v0.6.0 adds.
- A deployment that runs short reports `Scheduled 1 of 2 replicas (checked 2 clusters)`
  and `1 of 2 replicas serving`.
- The traffic screen names `modelplane_engine_tokens_total` and
  `modelplane_engine_requests_total`, which replace
  `modelplane_request_output_tokens_sum` and
  `modelplane_request_duration_seconds_count`.

The gateway's address is a documentation address, 203.0.113.41, in place of
the real one.

## Build and release

```bash
./build.sh                                        # dist/modelplane-demo-<os>-<arch> and its .sha256
uv run --with pytest --with rich pytest -q tests  # the tests
```

`build.sh` uses PyInstaller to make one binary with Python and
[rich](https://github.com/Textualize/rich) inside it. Pushing a `v*` tag runs
`.github/workflows/release.yml`, which tests and builds on each platform and
publishes the release with `install.sh`. Attach the recording to the release
too, so the GIF above and the gist's link resolve:

```bash
gh release upload v0.1.0 booth-demo.gif booth-demo.mp4
```
