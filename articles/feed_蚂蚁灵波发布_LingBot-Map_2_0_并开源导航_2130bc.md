---
title: "蚂蚁灵波发布 LingBot-Map 2.0 并开源导航模型 LingBot-Navi"
summary: "Robbyant, Ant Group's embodied AI company (@robbyant_brain), just released LingBot-Map 2.0."
publisher: "AIHOT热榜"
url: "https://x.com/rohanpaul_ai/status/2126019340525056182"
score: 75
---

# 蚂蚁灵波发布 LingBot-Map 2.0 并开源导航模型 LingBot-Navi

- **来源**：AIHOT 热榜第 9 名（原信源：X：Rohan Paul (@rohanpaul_ai)）
- **发布时间**：2026-11-27 05:05 (BJT)
- **原文链接**：[https://x.com/rohanpaul_ai/status/2126019340525056182](https://x.com/rohanpaul_ai/status/2126019340525056182)

---

Robbyant, Ant Group's embodied AI company (@robbyant_brain), just released LingBot-Map 2.0. 

It builds a 3D map from ordinary video while the camera is still moving, and more importantly it can now go back and fix that map partway through a run.

The old problem was drift: each new camera position is estimated from the one before it, so tiny errors pile up over a long route and slowly bend the whole map out of shape.

Its key upgrade is a step called Refine, which can be triggered at any time to revise the model's existing spatial memory and reduce that accumulated drift.

All of it happens inside the model during normal inference, with no external optimization backend (no separate solver running next to the model to re-fit the camera positions).

The corrected memory is written back into the model's cache, so every frame that comes after is read against the fixed version.

On the VBR benchmark, which is multi-kilometer routes around Rome (up to 5.2km and about 18,800 frames), average trajectory error is 31.12m for LingBot-Map 1.0 and 4.72m for 2.0 with Refine.

Robbyant is building it as a key ability of its embodied-native, general-purpose robot brain. Without a spatial memory a robot can rely on, there's no autonomous work over large areas and long hours.

Average trajectory error is the gap between where the model thinks the camera was and where it really was, averaged over the whole route.

Code and weights are available for the Streaming version
