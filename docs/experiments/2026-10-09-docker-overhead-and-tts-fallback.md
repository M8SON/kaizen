# Docker start-up overhead, container isolation, and ElevenLabs runtime fallback (on the Pi)

**Date:** 2026-10-09
**Where:** Raspberry Pi 5 (`ssh pi`), Docker 26.1.5, real Open-Meteo / ElevenLabs APIs
**Code:** `main` at `87d27f0`…`f25b703` (process skills `ff6ca03`, container hardening
`2429757`, dashboard isolation `f452cc3`, ElevenLabs fallback `f25b703`)

## Question

1. What does running a skill in Docker cost per call on the Pi, versus a plain subprocess?
2. Do the hardened container flags actually isolate a skill?
3. When ElevenLabs fails mid-session, how long until local Kokoro speaks?

## Method

- **Overhead:** timed with `date +%s%N` around the command, 3 runs each:
  `docker run` of `kaizen/base` running `python -c pass` with the skill flags;
  host `python3 -c pass`; the weather skill
  (`SKILL_INPUT='{"query":"Boston, MA"}'`) in Docker vs `python3 skills/weather/scripts/app.py`;
  after the change, `ContainerManager.execute_skill` on the `type: process` weather skill.
- **Isolation:** inside a container built with `_build_docker_cmd` flags, checked uid,
  `CapEff`, TCP connect to a host loopback listener (127.0.0.1:36151), raw ICMP socket,
  `https://example.com`; a 3s-timeout call running `sleep 60`, then `docker ps`.
  After the firewall (`d2db134`): TCP to 172.17.0.1:22, 192.168.1.1:80, 192.168.1.176:22, DNS.
- **Memory cgroup:** `docker run --memory=64m … cat /sys/fs/cgroup/memory.max`.
- **Fallback:** `ElevenLabsTTSBackend(api_key="sk_invalid_key_for_test", fallback_factory=KokoroONNX fp32)`,
  `_synth_audio` on two sentences.

## Results

### Overhead

| Run | Times (ms) |
|---|---|
| `docker run` empty python | 399, 383, 429 |
| host `python3 -c pass` | 16, 21 |
| weather skill in Docker (real API) | 1,680, 1,570, 1,565 |
| weather `app.py` on host | 1,152 |
| weather via `type: process` path | 1,213, 1,178, 1,203 |

Docker adds ~400–450ms per call. Weather went ~1.57s → ~1.2s with `type: process`.

### Isolation (hardened flags)

uid 1000; CapEff `0000000000000000`; host loopback: ConnectionRefused; raw socket:
PermissionError; internet: 200; timed-out container: 0 left running.
With the firewall: docker gateway SSH, router admin and laptop SSH all timed out (blocked);
internet 200; DNS resolves. From the laptop: librespot :4070 HTTP 200, rpcbind :111 blocked,
dashboard :7860 unreachable.

### Memory limits

Before `cgroup_enable=memory`: Docker warned "Your kernel does not support memory limit
capabilities… Limitation discarded" (cmdline had `cgroup_disable=memory`, controllers
`cpuset cpu io pids`). After the cmdline change + reboot: controllers include `memory`;
`memory.max` inside a `--memory=64m` container = 67108864.

### ElevenLabs runtime fallback

Real 401 (`invalid_api_key`) → warning logged → Kokoro ONNX spoke.
Sentence 1: 1.43s of audio in 7.0s (includes model load). Sentence 2: 1.13s of audio in 1.9s.

## Conclusion

Docker costs ~0.4s per call on the Pi, so trusted bundled skills run as `type: process`;
untrusted ones stay in Docker, where the hardened flags hold. Memory limits need the
cmdline fix on Raspberry Pi OS. The mid-session fallback works but its first sentence
waits ~7s for the lazy Kokoro load.

## Not tested

Dashboard news/weather panels with live data and the kiosk display; a real mid-session
quota exhaustion (simulated with an invalid key); preloading the fallback model; memory
limit enforcement under actual pressure (only the configured limit was read back).
