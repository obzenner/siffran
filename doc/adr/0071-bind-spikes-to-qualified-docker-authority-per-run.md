---
number: 71
title: Bind single-container spikes to qualified Docker authority per run
status: accepted
date: 2026-10-16
tags:
- empirica
- sandbox
- docker
- security
- state
links:
- target: 30
  kind: Amends
- target: 67
  kind: Amends
---

# Bind single-container spikes to qualified Docker authority per run

## Context and Problem Statement

Empirica needs deterministic experiments without granting model-authored text host-shell or Docker authority. Mutable image tags, runtime/profile changes, capture drift, ambiguous cleanup, and controller crashes must not create false evidence. The unreleased multi-container candidate was too complex for a broad-adoption MVP and was directly replaced before Empirica 6 release.

Empirica `6.0.0` keeps public wire `empirica/v2`, state `empirica.run/3`, and observer `empirica-ui-view/v1` as independent version axes.

## Decision Drivers

* Authors need a familiar local image reference, exact argv, optional repository inputs, and environment overrides—not Docker concepts.
* Every privilege, mount, resource, network, timeout, lifecycle, and destructive action must remain host-owned.
* Tags and ID prefixes must resolve once to immutable local daemon identity before effects.
* Security-relevant Docker semantics matter; cosmetic or unknown serialized fields do not.
* Timeout, exit 137, OOM, output overflow, capture/authority drift, and cleanup ambiguity must produce unavailable, never PASS/FAIL.
* Qualification must be capability-based and usable on local Linux Engine and Docker Desktop macOS hosts.

## Considered Options

* Host-shell execution (rejected: no sandbox or immutable authority).
* Command-string or authored Docker options (rejected: ambient privilege and parsing authority).
* Multi-container authored topology (deferred: excessive author surface and qualification complexity for MVP).
* One host-sealed verdict container under a run-bound qualified profile (chosen).

## Decision Outcome

The authoritative detailed decision is [Empirica 6 single-container Docker spike MVP](../design/specs/empirica-6-docker-spike-mvp.md). It directly replaces the unreleased multi-container plan; there is no old-draft loader, schema, translation, compatibility, or migration path.

The public spike action contains exactly `kind`, `claim_id`, and `empirica.spike-plan-draft/2`, whose five fields are `schema`, `image`, `argv`, `inputs`, and `environment`. The host resolves local immutable image identity and native canonical platform, expands and captures declared repository paths, seals framework policy/profile/runtime facts, and commits the request before mutating effects.

One verdict container runs as uid/gid 65534 in `/work`, receives one read-only `/inputs` tree, framework-owned `/work`, `/tmp`, and shm storage, `min(2, NCPU)` CPU, 2-GiB memory/no extra swap, 256 PIDs, fixed ulimits, independent 1-MiB streams, and a 120-second production deadline. Isolated uses network none; networked uses one private non-internal bridge without host ingress. The author controls none of these facts.

Natural zero is PASS; natural nonzero except 137 is FAIL. Exit 137 and every substrate ambiguity are unavailable. Only recorded full IDs grant cleanup authority, and only their exact absence is required; unrelated Docker activity is irrelevant.

`StartRun` still selects one exact qualified host profile or freezes spikes unavailable. Runtime never pulls, builds, loads, logs in, changes Docker configuration/permissions, or silently changes a run's authority. Qualification uses an operator-supplied already-local capability image and five fast profile-specific probes, and publishes authority only after exact owned-resource cleanup.

Development probes informed this amendment but are not qualification or release evidence; their local bundle manifest has SHA-256 `7ac2c9d9a37c2afa71967a9f65124c613bdb9b7748ee65a1bc066e4405acd575`. The accepted requirement source digest is `0c5c438e35794d74e6b94950a529e915fbbcb0a0c8aafb12af979eeaf4d1da0f`; the repository-owned design record carries the durable decision.

## Consequences

* Good, because the author surface is five familiar fields with no Docker authority.
* Good, because immutable image resolution prevents tag movement between sealing and execution.
* Good, because semantic admission tolerates harmless Docker representation differences while retaining security invariants.
* Good, because unrelated Docker activity no longer invalidates a spike or qualification.
* Bad, because multi-container/service experiments are deferred.
* Bad, because cross-architecture images and declared image volumes are unavailable in the MVP.
* Bad, because broad support still requires the documented Tier-1 host/store acceptance matrix.

## Confirmation

Pure draft/plan tests prove exact closure, local-reference syntax, canonical path/environment handling, fixed policy, host-sealed image/platform/capture facts, exit-137 semantics, and source rejection of the removed multi-container surface. Later checkpoints must implement and qualify image resolution, transaction integration, semantic Docker effects, five-probe authority, recovery, and public observer/UI projections before release acceptance.
