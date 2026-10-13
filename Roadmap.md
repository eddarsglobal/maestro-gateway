# Roadmap.md — MAESTRO Production Consolidation

**Historical V8.1 package reference**: original standalone build snapshot; official version control is the three Git repositories identified below.

**Release status**: **NOT PRODUCTION-READY**. Production requires all gates including real Ollama evaluations, real macOS application testing, signed/notarized distribution, and no known P0 defects. A passing Python test suite is not sufficient.

## Mandatory sequential gates

`Roadmap_guardian` is an executable *local gatekeeper*, not a sentient autonomous agent. It inspects evidence when invoked by the developer, CI or build script. It cannot watch a Mac remotely or certify subjective quality without actual human review. It never writes project source or gives models new permissions.

### G0 — Audit of the actual UI → Gateway → Core → model path

- [x] Inventory the single bundled source of truth, its UI, Gateway and Core.
- [x] Identify HTTP `/mission`, conversation context, runtime routing, Ollama adapter, AY delivery verdict, model metrics, Electron launch and macOS build pipeline.
- [ ] Reconfirm runtime call graph with logs from the **installed** macOS binary and trace IDs (G3 evidence).

**Gate evidence**: source paths, SHA-256 source fingerprint, static wiring assertions, runtime-proof deferred explicitly to G3. `Roadmap_guardian.py run --stage audit`.

### G1 — Repair and integrate existing mechanisms; no reimplementation from zero

- [x] Route software creation intentions (including Three.js) as code work, not `quick`; support EN FR DE AR PT ES HR IT phrases.
- [x] Count timeout attempts' `wall_ms` and failed model outcomes in AUTO history; avoid assigning cumulative time to the final model.
- [x] Reject empty promises and generic greetings to software-creation requests.
- [x] Propagate the *remaining* monotonic mission budget to the existing Ollama generation transport; retain one bounded AUTO rescue.
- [x] Normalize a socket-level `TimeoutError` into the recoverable deadline exception even if the socket fires just before the watchdog event (macOS/Python 3.12 race); preserve non-timeout `OllamaHTTPError` transport failures. Added two deterministic tests; verified 44/44 integrated regressions in the package QA environment. **On-device Mac rerun required.**
- [ ] **P0 OPEN — partial safe parallelism is implemented**: eliminate whole-mission blocking for remaining research/tool effects, prove bounded process/tool cancellation and durable Core abort during crashes. Production-grade concurrency and all-effects cancellation remain unverified.
- [x] **G1 partial, not closure (12 Oct 2026)**: authenticated `POST /mission/cancel` and `GET /mission/status`; per-request local mission state with restart reconciliation, refusal of duplicate request IDs, cancellation propagation to live Ollama HTTP sockets, and an eight-language native UI Stop button. Ten added deterministic tests cover direct transport cancellation, queued cancellation, duplicate requests, restart state, and real local HTTP cancellation. The old Core-wide mission lock still serializes governed Core transitions. Cancellation of every possible external tool and process, true per-conversation concurrency, and exact durable Core abort on process failure are **not** proven. **DO NOT CLOSE G1** on this evidence.
- [x] **G1 bounded Ollama concurrency (12 Oct 2026; partial)**: distinct conversations may execute concurrently only while awaiting Ollama model I/O. The Gateway releases the shared Core serialization lock during a known Ollama adapter call, then reacquires it before quality decisions, mission state transitions, evidence and TX. An independent reference-counted per-conversation lane serializes same-chat requests, prevents stale context and checks cancellation/deadline before entering the Core. Unknown future model adapters retain the Core lock. Three new deterministic concurrency tests cover simultaneous model I/O with two governed TX commits, same-conversation serialization, and cancellation while queued. **This does not establish cancellation of arbitrary tools, research fetches, or external processes and is NOT the full P0 closure.**
- [ ] **P0 OPEN**: task-specific semantic validation and verified artifact pipeline for authorized software project creation. A regex is not semantic proof.
- [x] **G1 partial evidence (not closure)**: a read-only Three.js artifact verifier now checks an authorized Workspace, independently APPROVED → APPLIED change proposals with exact SHA-256, and a separately user-approved `node_npm_build` execution record with a real passing npm process. `POST /software-project/verify-threejs` returns an explicitly limited proof; it never grants permission, creates files, claims browser/semantic truth or substitutes for a complete software-creation mission. Five positive/negative real-build tests; this is not yet integrated as autonomous creation and does **not** close either P0.

- [ ] Test failure and recovery with multi-mission concurrency, cancellation and persistence across process restarts.

**Gate evidence**: deterministic regression tests + Core immutability; G1 gate is **BLOCKED** until both P0 items and associated tests are complete. Existing unit tests may pass while G1 remains blocked.

### G1 — Fair runtime discovery and atomic model choice (12 Oct 2026)

**Constitutional rule:** neither Ollama nor LM Studio, llama.cpp, MLX, custom OpenAI-compatible hosts or any vendor has inherent discovery or routing priority. Provider identity is metadata, not a score bonus, authorization or truth.

- [x] At local Gateway launch: passively discover compatible running local inference endpoints concurrently, including an optional list of user-configured loopback endpoints (`MAESTRO_LOCAL_PROVIDER_ENDPOINTS`). Check `/api/tags` for Ollama and `/v1/models` for local OpenAI-compatible engines. Also indicate known installed but stopped binaries/apps without starting them or claiming models are available.
- [x] Register advertised runtime/model pairs through the **generic** Core `ResourceDescriptor` / `SovereignDiscoveryBridge` and provider-specific replaceable *Gateway* adapters. Preserve Core zero-model bootstrap and Core source unchanged.
- [x] Local OpenAI-compatible `/v1/chat/completions` execution supports LM Studio, llama.cpp, MLX-compatible servers and registered local peers; bounded transport, cancellation, request deadline, read-size limit and loopback-only URLs. Do not pretend all models are proven capable of conversation in every language merely because `/v1/models` responded.
- [x] `/models` exposes `provider::model` resource IDs without an Ollama default; manual bare names are accepted only when unambiguous, otherwise fail closed. `/providers` reports neutral inventory and readiness.
- [x] In-memory AUTO candidate filtering/scoring without alphabetical/vendor-first priority; deadline, provider-neutral selection, deterministic per-prompt hash tie-breaking. Passive prewarm at launch; warm routing history cached; no inference or network in the hot decision.
- [x] Integrated regression proof: no Ollama available and a live, compatible local alternate server present → governed DIRECT mission completes. Also no-model Core boot, discovery vendor-order independence and endpoint-collision tests.
- [ ] **ON-MAC PROOF REQUIRED**: real LM Studio OR llama.cpp/MLX server answers while Ollama is fully stopped; verify correct language/quality in a real mission, explicit user selection, failover, timeout and cancellation, not merely a stubbed OpenAI endpoint.
- [ ] **ON-MAC PERFORMANCE REQUIRED**: `python3 agents/Roadmap_guardian.py run --stage router` measures in-memory 16-candidate routing P95 with an objective of ≤100 µs on target hardware. This is only hot routing; model load, model response, language assessment and filesystem scans are NOT included.
- [ ] Model capability/language qualification based on real evaluations; adaptive score must not infer quality solely from size or runtime identity. No silent cloud inference and no automatic engine install/start.

**Roadmap Guardian:** `run --stage code` requires the real local alternative adapter test and the impartial registry tests; `run --stage router` records a source-bound microbenchmark. Both are required for a production candidate, while G1 full P0 closure, G2 and G3 remain **BLOCKED**. Never equate deterministic local stubs with real end-user model quality.


### G1 — Interdiction des réponses conversationnelles codées en dur (12 octobre)

- [x] Supprimer `verified_capabilities_reply.py`, sa table de huit réponses préécrites et la voie `gateway::verified_capability_snapshot`.
- [x] Faire passer **toute réponse conversationnelle**, y compris « que peux-tu faire ? », par un modèle enregistré, sans exception fondée sur le contenu de la question.
- [x] Conserver le registre de capacités uniquement comme **données factuelles** dans les instructions transmises au modèle. Ce registre n'accorde ni droit ni capacité supplémentaire.
- [x] En absence de modèle admissible, afficher un état d'indisponibilité explicite ; ne produire aucune réponse conversationnelle simulée.
- [x] Écrire des tests dans les 8 langues qui vérifient `resource_calls >= 1`, `response_origin = local_model` et que la sortie provient de l'adaptateur d'inférence.
- [x] Ajouter le contrôle d'absence de réponse préécrite à `Roadmap_guardian.py` et au smoke-test de build macOS.
- [x] **Correctif G0 (12 octobre)** : contrôle structurel de l'ancien générateur, confirmé par les tests de comportement dans huit langues ; ne plus exiger un littéral exact de `response_origin` dans le code. Deux tests Guardian valident l'indépendance au formatage et le rejet du module préécrit s'il réapparaît. Cette correction ne débloque pas G1, G2 ni G3.
- [ ] Faire confirmer ce comportement dans l'application **installée sur Mac**, avec un moteur réel actif et avec tous les moteurs désactivés. Les tests simulés ne suffisent pas pour G3.

**Frontière importante** : labels UI, messages d'erreur techniques, règles de sécurité et descriptions factuelles du registre peuvent être statiques. Ils ne doivent jamais être exposés comme des **réponses générées par un modèle**. Ni le prompt, ni les tests unitaires ne prouvent la qualité sémantique réelle. Les portes G1 complètes, G2 et G3 restent fermées.

### G1 — Evidence-qualified AUTO Scheduler (12 Oct 2026; development gate)

- [x] Replace model-size preference and default/advertised quality scores in `adaptive_router.py` with local **operational evidence per model × task × language**. No runtime-name/provider-order privilege. A small model with measured successful results can outrank a large unqualified one. A size-based *provisional eligibility safety floor*, where present, does not imply quality or semantic competence.
- [x] `model_qualification.py`: immutable copy-on-write in-memory routing snapshots of Gateway outcome (`quality_accepted`, `quality_rejected`, `timeout`, `transport_failure`) with measured wall time; boot background recovery from local private mission metadata; no filesystem scan, network call or model inference in the hot routing decision. Operational acceptance by the heuristic response guard is **not human semantic validation**.
- [x] Gateway records actual per-attempt wall duration and final quality outcome **only after guard evaluation**, with task and language metadata. Unknown models remain explicitly `provisional` instead of falsely receiving a quality score.
- [x] A retry after timeout or quality rejection requires a second already-qualified resource in the same task/language, a measured response latency compatible with remaining mission budget, and all original governance constraints. No more model-size promotion and no unmeasured rescue claim.
- [x] 12 deterministic G1-QUAL tests enforce impartial order, language isolation, quality evidence, rejection demotion, bounded qualified fallback, hot-path no filesystem I/O, local metadata recovery and rejection of forged outcome types. Existing timeout tests now seed verified operational evidence rather than assuming small/large models are capable.
- [ ] **MAC LIVE QUALIFICATION**: on user's own installed MAESTRO, gather real per-model, per-language performance and quality observations with at least one genuine engine other than Ollama. Confirm that the fast simple chat replies are relevant and respond within agreed on-device P95 targets. Simulations do not count.
- [ ] **DEEP SEMANTIC EVALUATION**: human-/task-specific evaluations and model bench calibration; current response guard is a heuristic and cannot certify truth or language fluency. No universal 5-second answer guarantee without qualifying local hardware and model.
- [ ] **G1 P0 STILL BLOCKED**: full external-tool cancellation/concurrency and actual authorized end-to-end software generation are separate missing closure conditions. Neither this routing fix nor 12 regression tests opens G2 or G3.

### G2 — Test four mechanisms together, in all 8 UI languages, using real models

- [x] Provide deterministic fixtures for route / task / quality / timeout, with FR HR EN DE IT ES PT AR examples (unit-test scaffolding only).
- [ ] Execute real inference for all eight languages and practical follow-up conversations in installed MAESTRO; retain candidate, model, elapsed, failure/quality evidence.
- [ ] At least one qualified model for conversation in each advertised language, or the UI honestly signals unsupported/degraded language.
- [ ] Independently review task answers (native speakers where available) and verify no fabricated success, no repeated generic answer, no false AY semantic assurance.
- [ ] Software request with authorized Workspace produces verifiable files and build/test results; without authorization never claims files were created.
- [ ] Test model unavailable, slow streamed output, timeout fallback, retry cap and cancellation without deadlock.

**Gate evidence**: `Roadmap_guardian live-languages` on macOS, local evidence and explicit human acceptance of each language. `G2` cannot pass on mocks.

### G3 — Validate the **installed native macOS app** (not just Python tests)

- [ ] Build and install an app from the fingerprinted source; preserve SETTINGS, 8 languages and existing conversations.
- [ ] Launch **installed** bundle; verify native clicks SETTINGS, appearance and language switch; chat works from Electron to Gateway to a real local model.
- [ ] Repeat the eight-language suite via installed app; verify mission states, delays, error/cancel paths, workspace permissions and no ghost execution.
- [ ] Run hardware-specific latency benchmarks; define qualified hardware/model profiles and p95 thresholds. No fabricated universal 5s guarantee.
- [ ] Confirm reproducible version/build provenance, code signing with Developer ID, notarization and Gatekeeper acceptance on a clean Mac.
- [ ] Approve release only after zero **known release-blocking** defects, explicit test reports and human sign-off.

**Gate evidence**: installed bundle checks + reviewed screenshots/logs + macOS codesign/notarization tests. No Linux result may satisfy G3.

## Roadmap Guardian operating contract

1. Stages are always ordered G0 → G1 → G2 → G3. If one is blocked, the next cannot be marked complete.
2. Guardian never interprets a green 1,407-test suite as user-level acceptance or semantic truth.
3. `status` is diagnostic. `run --stage audit` and `run --stage code` execute independent checks. `run --stage release` is **fail-closed** until P0 closures, live 8-language proof and a signed/notarized macOS build are confirmed.
4. Re-opened gates are verified with `run --stage closure` (real cancellation/concurrency + software artifact tests), `run --stage languages` (eight real model answers reviewed by humans), and `run --stage mac` (installed, notarized, human-reviewed macOS app). Evidence files are under `~/.maestro/roadmap_guardian` and bind to the source fingerprint.
5. A source change invalidates past local evidence. Every official **new** ZIP must contain this document and the executable Guardian in the package root, all three `sources/*` trees and the macOS bundle's Resources.
6. Previous ZIPs cannot be retroactively modified. Never claim that old source archives have been updated.
7. User data, prompts, proprietary workspace files and private model outputs remain local unless explicitly shared.

## Current release decision

**BLOCKED** at G1 outstanding P0 issues. Partial cooperative cancellation and concurrent Ollama inference for distinct conversations are implemented and pass synthetic tests, but all-effects cancellation / complete Core safety and end-to-end authorized software authoring remain unproven. The source contains measured fixes and regression scaffolding, but production acceptance G2/G3 remains unperformed on the user's Mac. No release tag or production approval.


### G1 — Single Primary Intelligence Manager · 100% LOCAL / SOVEREIGN (12 Oct 2026)

**Confirmed operating policy:** ONE active primary local LLM at a time for ordinary
conversation, managed by MAESTRO's sovereign Core. The Core starts and stays
usable with ZERO models. Ollama, LM Studio, llama.cpp, MLX and authorized local
OpenAI-compatible engines remain interchangeable peers, never ordered by brand.
Cloud models, provider installation/download, and automatic external runtime
launch are OUT OF SCOPE / NOT AUTHORIZED. No model receives permission to change
Core governance or Workspace policy. Explicit user model selection remains a
per-mission override and cannot silently alter the primary model.

- [x] Implement `PrimaryIntelligenceManager` in Gateway alongside the preserved
  original Core (313 Python modules unchanged), owned by `MaestroCoreBridge`.
  Route one local primary model across ordinary messages instead of changing
  candidates every prompt. Initial choice uses the in-memory competence and
  language evidence already indexed by the qualification scheduler.
- [x] Swap atomically if the incumbent disappears, a previously unqualified
  language gains a proven alternative, or a mission qualifies an authorized
  local fallback. Stale parallel missions cannot undo a newer choice.
- [x] Track observed success separately from `model listed`; status does not
  claim inference readiness before an actual model-origin response succeeds.
- [x] Apply a bounded timeout/failure cooldown. Do not keep retrying the same
  timed-out model immediately; do not treat cooldown as a permanent ban.
- [x] No canned dialogue, no use of an unregistered/cloud resource, no secret
  model downloads. Governed execution / AY / TX / authorization retained.
- [x] Add 14 simulated contract tests and a Guardian `primary` benchmark/gate.
- [ ] Run live Mac tests with the installed .app: first prompt, 20-message chat
  continuity, 2+ real local engines, one engine disappearing, warm/cold timing,
  language quality, and cancellation. Real providers and real quality NOT proven
  by synthetic tests. This is not a production release.

**Sequential Guardian:** `run --stage code`, `run --stage router`,
`run --stage primary` before considering the already-blocked G1 full closure.
The ≤100 µs benchmark covers only hot in-memory routing; it excludes cold
model load and generation latency. All real G1/G2/G3 release gates remain closed.

- [x] Primary hardening: fail-closed on missing locality attestation, epoch-bound readiness to prevent stale mission success, and HOME/TMPDIR-isolated Guardian suites in parallel with independent result verification (preserve user files).

- [x] Primary benchmark repeats three warm cohorts and exposes each P95; gate checks median cohort P95 to reduce spurious scheduling outliers. This is a synthetic in-memory metric only.

### G1 — Qualité des réponses complètes V6 (13 octobre 2026 ; QA uniquement)

- [x] Corriger la limite de 160 tokens appliquée aux réponses conversationnelles `normal` ; conserver le budget explicitement autorisé par le client (512 tokens par défaut dans React, ≤2048 via validation Gateway). `quick` reste borné pour sa classe.
- [x] Faire remonter au Quality Guard les raisons de terminaison `done_reason` (Ollama) ou `finish_reason` (OpenAI local) et rejeter les générations `length`/limite de tokens, sans déclarer AY sémantiquement vérifié.
- [x] Encadrer l'énumération d'identifiants internes dans une réponse de présentation destinée au public, tout en respectant une demande explicite d'informations API.
- [x] Nouveaux tests avec inférence simulée : `normal` conserve 512 tokens, réponse tronquée rejetée, retry gouverné, pas de réponse en dur. Ancien test « 160 tokens en normal » révisé car contraire à l'incident réel.
- [ ] **MAC RÉEL** : tester la même phrase avec le moteur local, prouver une terminaison `stop` et une réponse utile ; comparer latence réelle et qualité du modèle `phi3:mini` avec alternatives qualifiées.
- [ ] **PAS DE CERTIFICATION DE PRODUCTION** : G1 P0, G2 et G3 restent ouverts. La cohérence sémantique d'une réponse n'est pas prouvée par les tests de préfiltrage.


## V8 — Autonomous Local Model Qualification (development candidate)

- Eight UI languages: en (default), fr, de, ar, pt, es, hr, it. French is never the system default.
- The gateway passively detects all authorized local compatible runtimes. After normal user boot and 90 seconds without user activity on AC power (macOS), one background, abortable local inference probe may run. Foreground user missions cancel probes immediately. No cloud, model downloads, automatic engine startup, file tools, or privileged operations. `MAESTRO_AUTO_QUALIFICATION=0` disables this at any time.
- Independent arithmetic/translation/reasoning/permission checks per language are tiny, deliberately weak evidence; never human-quality certification. Live user outcomes remain separated from these tests.
- Bounded observations persist privately by model fingerprint, language and scenario; changes in the model digest invalidate prior probes. Plaintext user prompts or responses are never stored in the probe database.
- Model selection consumes bounded operational evidence, preserves the single sovereign local primary and fails closed on permission/runtime changes; background tests never change a primary slot directly.
- G1 P0 closure, G2 authentic eight-language review, and G3 macOS production remain BLOCKED pending device-level verification and full operational evidence.

- Exploration budgets: 25 s per probe, at most 24 per session-day and model sizes above 6 GB excluded by default; environment overrides are bounded. These are operational safeguards, not performance guarantees.

### V8.1 — Equitable autonomous qualification / accurate evidence (13 Oct 2026, QA)

- [x] Schedule first probes across the available local conversation models and all eight supported languages in EN-first UI order, instead of sorting languages alphabetically and oversampling Arabic. Deterministic, neutral model rotation; no provider preference.
- [x] Use native-language instructions, normalize Unicode decimal numerals, reject truncated responses, and preserve distinct scenario metrics. No single tiny arithmetic test proves language competence.
- [x] Prevent synthetic narrow rubric results from granting model selection quality bonuses/penalties. Real-mission operational evidence remains separate and governs ordinary Primary selection.
- [x] Inspect actual most recent probe (`model/language/scenario/outcome/failure category/stop reason/duration`) without leaking text; count oversized resources uniquely per discovery cycle, not cumulatively.
- [ ] **MAC REAL**: confirm fair scheduling and inspect exact failure category for the previous `gemma:2b` Arabic attempts. Previously stored synthetic failures remain available but are not linguistic demerits.
- [ ] **MAC REAL**: diagnose `qwen3:8b` 34.4 s English answer with cold/warm model-load and generation timing; this update does not claim faster model inference.
- [ ] **NO PRODUCTION RELEASE**: G1 P0, G2 and G3 remain blocked.

## Official tri-repository source-control contract (V8.1 reconciliation)

- Official local roots: `/Users/noury/Documents/App_py/MAESTRO_AGENT/Github/maestro`,
  `/Users/noury/Documents/App_py/MAESTRO_AGENT/Github/maestro-gateway`,
  `/Users/noury/Documents/App_py/MAESTRO_AGENT/Github/maestro-ui`.
- Official GitHub repositories: `eddarsglobal/maestro`, `eddarsglobal/maestro-gateway`,
  `eddarsglobal/maestro-ui`. This document is installed separately in each repository.
- V8.1 source ZIP SHA-256: `446a9fcbbd5d96773e119e3733d919141f86ec9e1430c9e3030cc7bf49fc7503`.
- Baseline source commits: `maestro=c8f425af1f50810b9cbf8dc779d4c642ceea2215`;
  `maestro-gateway=0e35e8056e774ab5ba89c7b2b58d9f83ec53ed78`;
  `maestro-ui=dd15753e8110ef4b7f798a11cf85caad0d435b53`.
- **Source of truth**: each official Git repository and its reviewed commits. ZIPs are
  immutable delivery snapshots, not a substitute for Git history.
- Maintain coordinated tri-repo commit provenance for each reviewed integration.
  Do not overwrite independent LICENSE, CI, governance or documentation files.
- Guardian rules: verify the three repo identities, clean source history,
  integrated regressions, reviews, GitHub CI, and release evidence. Never mark a
  gate PASS merely because a ZIP test passed. Do not auto-push or force-push.
- The legacy consolidated-package Guardian remains the G0/G1/G2/G3 test gate.
  The repo-local Guardian adapter only checks source-control identity; it does
  **not** replace the consolidated certification tests.
- Local-first: English is the default among EN/FR/DE/AR/PT/ES/HR/IT; retain
  one active conversational LLM, provider neutrality, no automatic cloud-primary
  inference or model downloads, and no semantic claims from tiny probes.
- **Release status: BLOCKED**. G1 full P0 closure, G2 semantic language review
  and G3 notarized macOS distribution are not certified by this integration.
