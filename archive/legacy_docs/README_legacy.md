# survivor-optimizer

Survivor Optimizer is a Python-first foundation for a future Survivor.io damage and resource optimizer website. The optimizer logic is intentionally browser-friendly so it can later run through Pyodide with no backend.

The current version focuses on the repo foundation, knowledge pipeline, validation schema, and a first working optimizer skeleton. It does not build the full website UI yet.

## Architecture

Raw guides and notes are never read by the optimizer at runtime. The project root folder named `folder/` is treated as the full local Discord/game guide dataset. It can contain images, PDFs, text files, docs, and guide exports. Those sources are converted into structured JSON knowledge first, then validated with Pydantic.

Pipeline:

1. Put source screenshots, PDFs, text guides, patch notes, event notes, or manual notes under `folder/`.
2. Run `tools/train_knowledge.py` to scan, extract/OCR where possible, chunk, classify, create draft knowledge, checkpoint progress, and build a local search index.
3. Review generated JSON under `training_outputs/` and `ai_outputs/`.
4. Run `tools/compile_knowledge.py` to merge accepted output into `knowledge/`.
5. Run `tools/validate_knowledge.py` to validate all knowledge files.
6. Runtime code in `optimizer/` reads only validated structured knowledge.

AI or heuristic extraction is only used to organize extracted knowledge. The optimizer should not trust raw output unless it passes validation. Runtime must stay explainable and free of paid APIs.

## Current Optimizer

V1 implements a core selector chest optimizer:

- Generates every split across `astral_core`, `xeno_core`, and `resonance_chip`.
- Simulates the resulting resource state.
- Scores each option with placeholder values and breakpoint hints.
- Ranks best options and avoid options.
- Produces rule-based explanations without any paid AI/API call.

The current scoring is placeholder logic. Future versions should replace it with real damage math, stat bucket interactions, mode-specific rules, and community-tested breakpoints.

This optimizer is damage-first. HP, healing, armor, damage reduction, revival,
shield durability, and other survivability-only effects are tracked as knowledge
metadata but do not affect recommendations by default. If a survival upgrade
unlocks a future damage breakpoint, score the damage breakpoint rather than the
HP or survival stat itself.

## Commands

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Optional GPU training dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
python -m pip install -r requirements-gpu.txt
```

The GPU stack is training-only. The optimizer runtime and future website path should still work without PyTorch.

Extract raw source text:

```powershell
python tools/extract_text.py
```

Run the resumable local trainer/indexer:

```powershell
python tools/train_knowledge.py --data-folder folder --minutes 30 --device auto
```

Run a heavier 10-minute GPU-assisted rebuild:

```powershell
python tools/train_knowledge.py --data-folder folder --minutes 10 --device gpu --heavy-gpu --force
```

This uses CPU for extraction/JSON work and CUDA for optional OCR/vector embedding work when the GPU packages are installed. If CUDA is unavailable, the trainer logs a warning and falls back to CPU.

Build AI prompt files:

```powershell
python tools/build_knowledge_prompt.py
```

Compile AI output JSON into knowledge files:

```powershell
python tools/compile_knowledge.py
```

Validate knowledge:

```powershell
python tools/validate_knowledge.py
```

Run the sample optimizer demo:

```powershell
python run_demo.py
```

Run tests:

```powershell
python -m pytest
```

## Knowledge pipeline workflow

Use this loop before adding large guide datasets:

```powershell
python tools/train_knowledge.py --data-folder folder --minutes 30 --device auto
python tools/build_knowledge_prompt.py
python tools/check_ai_output.py
python tools/compile_knowledge.py
python tools/validate_knowledge.py
python run_demo.py
pytest
```

Do not dump huge guide data into the repo until the test AI output loop passes. Start with one guide or category at a time, review the AI JSON before compiling it, and only then merge it into `knowledge/`.

Broken placeholder PDFs should be deleted or replaced with real readable files. The extractor skips broken PDFs with warnings so the rest of the pipeline can keep moving.

The trainer stores progress in `training_state/`, draft records and a local searchable cache in `training_outputs/`, copied image inputs in `processed_images/`, and extracted text in `extracted_text/`. Unchanged files are skipped on later runs unless `--force` is used. `--device cpu`, `--device gpu`, and `--device auto` are accepted, but CPU is the supported baseline.

## Smart Small Optimizer Design

The optimizer should stay small, adaptive, and explainable:

- `knowledge/*.json` stores structured facts, rules, breakpoints, warnings, conflicts, source metadata, dates, confidence, notes, categories, and scoring relevance.
- `training_outputs/search_index.json` is the first searchable memory layer. It uses lightweight JSON keyword search so the optimizer can attach relevant guide excerpts to recommendations. Optional embeddings can be added later, but they must remain optional.
- `knowledge/rules.json` stores guide advice such as "only good if near breakpoint", "avoid unless it unlocks X", "better long-term", and "not worth without enough crit rate". Python applies those rules in `optimizer/rules_engine.py`.
- `optimizer/simulator.py` includes a simple chain-reaction simulator for multi-step outcomes: add or spend resources, check breakpoint unlocks, then apply damage effects.
- `knowledge/scoring_weights.json` stores small tunable scoring multipliers for immediate damage, long-term value, breakpoint closeness, resource efficiency, confidence, and mode relevance. These are not neural-network weights.
- `data/player_feedback.jsonl` is reserved for future feedback learning. Each JSONL row can store player state, recommendation, user choice, known result, and whether the recommendation was good. Use that later for simple statistical tuning, not heavy ML, once real feedback exists.

The public runtime API stays:

```python
from optimizer.main import optimize

result = optimize(player_state)
```

`result` is JSON-serializable for CLI, website, Pyodide, and static-client use.

####################
We now have the Survivor optimizer foundation working. Validation passes, run_demo works, and pytest passes.

Next task:
Upgrade the knowledge pipeline so it is ready for real Survivor.io Discord/game datasets, but do not add real data yet.

Current issue:
tools/extract_text.py works, but placeholder/corrupt PDFs are skipped.
tools/build_knowledge_prompt.py creates prompts.
tools/compile_knowledge.py keeps existing knowledge if ai_outputs is empty.
But we need a better system for AI outputs before feeding large data.

Goal:
Make the AI-output pipeline reliable, future-proof, and safe before I start adding real Survivor.io guide data.

Please implement these upgrades:

1. Add an AI output schema example file.

Create:
docs/ai_output_schema_example.json

It should show the exact JSON structure the AI should return:

{
  "items": [],
  "item_effects": [],
  "gear": [],
  "gear_sets": [],
  "survivors": [],
  "survivor_awakenings": [],
  "pets": [],
  "xeno_pets": [],
  "tech_parts": [],
  "collectibles": [],
  "collectible_sets": [],
  "resources": [],
  "chests": [],
  "events": [],
  "event_shops": [],
  "breakpoints": [],
  "rules": [],
  "hidden_interactions": [],
  "warnings": []
}

Each section should include 1 tiny example record when possible, especially:
- resources
- rules
- breakpoints
- hidden_interactions
- chests
- item_effects

Do not use fake exact Survivor.io numbers unless marked as placeholder/example.
Use confidence: "low" for example uncertain data.

2. Improve tools/build_knowledge_prompt.py.

The generated prompt should now include:
- clear instructions that AI must return JSON only
- the exact allowed top-level sections
- reminder to not invent stats
- if info is unclear, put it in warnings or hidden_interactions with confidence low
- if info belongs to multiple systems, duplicate references by id instead of writing vague text
- require snake_case ids
- require source field
- require source_type field
- require confidence field
- require notes if the record is uncertain
- require that official game text and Discord/community-tested info are distinguished

Also add:
- If a guide mentions "best", "priority", "worth it", "trap", "avoid", "only good if", "breakpoint", "scales later", or "not worth without X", extract that as a rule.
- If a guide mentions "works differently than written", "hidden", "tested", "overperforms", "underperforms", or "veteran knowledge", extract that as hidden_interactions.
- If a guide mentions upgrade thresholds, costs, stars, awakening, cores, AF, EE, CE, PoT, or mode differences, extract those as breakpoints or rules.

3. Add tools/check_ai_output.py.

This script should:
- Read every .json file in ai_outputs/
- Check that top-level keys are allowed
- Print unknown top-level keys
- Print record counts by section
- Validate records using existing Pydantic models where possible
- Warn if required fields like id, name, confidence, source/source_type are missing
- Not modify knowledge files
- Exit with a clear error if JSON is invalid

4. Improve tools/compile_knowledge.py.

It should:
- Read all AI output JSON files.
- Merge all sections into correct knowledge files.
- Preserve existing manually created base files if no AI outputs exist.
- Deduplicate records by id.
- If the same id appears more than once:
  - merge records if they are identical
  - if conflicting, keep both only if necessary by adding a warning to warnings.json
  - otherwise prefer higher confidence in order: high > medium > low
- Print a clear summary:
  - files processed
  - records added per section
  - duplicates found
  - conflicts found
  - warnings added

5. Add source tracking.

Every compiled record should keep:
- source
- source_type
- confidence
- notes

If an AI output record does not include these, add:
- source: "unknown"
- source_type: "unknown"
- confidence: "low"
- notes: "Missing source metadata from AI output."

6. Add a sample AI output for the current manual core_test.

Create:
ai_outputs/core_test.json

Use only the small test info from raw_data/manual_notes/core_test.txt.

It should include:
- astral_core resource
- xeno_core resource
- resonance_chip resource
- core_selector_chest chest
- a rule for astral core being valuable near SS/astral breakpoint
- a rule for xeno core being more long-term near xeno awakening breakpoint
- a rule for resonance chip being lower value if it does not unlock a breakpoint
- warnings if any info is generic/placeholder

Use confidence medium or low, not high.

7. Add tests.

Add or update tests to check:
- tools/check_ai_output.py can read ai_outputs/core_test.json
- compile_knowledge.py compiles core_test.json without crashing
- validate_knowledge.py passes after compile
- resources.json has astral_core, xeno_core, and resonance_chip
- chests.json has core_selector_chest
- rules.json has at least 3 rules
- pytest still passes

8. Add README section.

Update README with a section:
"Knowledge pipeline workflow"

Include:
python tools/extract_text.py
python tools/build_knowledge_prompt.py
python tools/check_ai_output.py
python tools/compile_knowledge.py
python tools/validate_knowledge.py
python run_demo.py
pytest

Also explain:
- Do not dump huge guide data until the test AI output loop passes.
- Start with one guide/category at a time.
- Review AI JSON before compiling it.
- Broken PDFs should be deleted or replaced.

After implementing, run:
python tools/check_ai_output.py
python tools/compile_knowledge.py
python tools/validate_knowledge.py
python run_demo.py
pytest

Fix all errors.


########################################

# Survivor Optimizer

Survivor Optimizer is a damage-first recommendation engine for Survivor.io. The purpose of this project is not to make a general wiki, a survival calculator, or a pretty stat tracker. The goal is to answer one practical question:

**What should I do next to gain the most useful damage?**

This project treats Survivor.io as a “kill before killed” optimization game. HP, healing, damage reduction, tankiness, revival value, and other survival-only stats are stored for completeness, but they do not affect the default optimizer score. The calculator should only care about survivability when a survival-related upgrade directly unlocks or enables a damage-relevant breakpoint. In that case, the optimizer scores the damage breakpoint, not the HP itself.

The default scenarios are:

1. **Short-term Boss Damage**
   Prioritizes immediate damage gains, current usable upgrades, and near-term unlocks.

2. **Long-term Account Damage**
   Prioritizes future scaling, rare resource efficiency, major breakpoints, and systems that compound over time.

3. **Balanced Progression**
   Balances immediate damage, long-term scaling, resource efficiency, and breakpoint closeness.

The optimizer is built around Discord/game guide data because that data already focuses on what is actually relevant: gear priority, pet priority, xeno pets, tech resonance, collectibles, mount components, event shop value, clan shop priority, damage buckets, hidden interactions, and community-tested rules.

## Main Design

The project is designed as a local knowledge-based optimizer.

Raw data should go into a folder named:

```text
folder/
```

This folder can contain Discord screenshots, `.tif`, `.png`, `.jpg`, PDFs, text files, exported Google Docs, and guide notes.

The program should process that folder and build structured knowledge from it. The optimizer should not read raw Discord images at runtime. Runtime should use clean validated files from:

```text
knowledge/
```

The intended pipeline is:

```text
folder/
→ extract text / OCR images
→ chunk and classify guide content
→ create structured AI prompts or local extraction tasks
→ convert guide info into JSON knowledge
→ validate JSON with Pydantic
→ compile clean knowledge files
→ build a searchable local memory/index
→ run optimizer recommendations
```

The project should support repeated training/indexing runs. Each run should build on the previous knowledge base instead of starting over. Existing knowledge should be preserved unless newer data clearly improves it. Conflicts should be logged instead of silently overwritten.

This is not meant to be a blind neural network. The optimizer should be a hybrid system:

* structured JSON knowledge
* rule-based scoring
* breakpoint detection
* scenario weighting
* action simulation
* searchable guide memory
* optional embeddings/vector index for retrieval
* optional learned ranking later if real labeled results are collected

The optimizer should stay explainable. Every recommendation should show:

* best move
* why it wins
* what it unlocks
* what to avoid
* confidence level
* source notes
* assumptions
* missing data warnings

## Damage-First Philosophy

The optimizer should score damage, not comfort.

Survival-only stats are ignored by default:

```text
HP
Final HP
Healing
Damage reduction
Armor
Revival
Shield durability
Tank-only effects
```

Damage-relevant stats include:

```text
ATK
Final ATK
Crit rate
Crit damage
Skill damage
Shield damage
Vulnerability
Boss damage
All damage
Final damage
Damage to chilled
Damage to poisoned
Damage to weakened
Damage to lacerated
Pet damage
Survivor damage
Projectile damage
Conditional damage
Enemy debuffs
Uptime-based damage bonuses
```

If a guide says something is “good for survival” but not damage, it should be stored but marked as ignored by default.

If a guide says something is “best,” “priority,” “avoid,” “trap,” “only good if,” “not worth,” “worth near breakpoint,” or “scales later,” that should become an optimizer rule.

If a guide says something “works differently than written,” “overperforms,” “underperforms,” “hidden,” “tested,” or “veteran knowledge,” that should become a hidden interaction or warning.

## Systems the Optimizer Should Support

The final optimizer should eventually understand these systems:

```text
Equipment
SS gear
Eternal gear
Void gear
Chaos gear
Astral forge
Chaos forge
Xeno transmute
Relic cores
Astral cores
Xeno cores
Resonance chips
Core selector chests
Survivors
Survivor awakening
Survivor synergy
Combat harmony
Pets
Xeno pets
Pet awakening
Pet assist skills
Tech parts
Twinborn tech
Tech resonance
Tech overload
Collectibles
Collectible sets
Custom collections
Advanced custom collections
Mounts
Mount components
Mount sync rate
Event shops
Clan shop
Universal exchange
Damage/stat buckets
Mode-specific recommendations
Hidden/veteran-tested mechanics
```

## Knowledge Files

The structured knowledge should live in JSON files like:

```text
knowledge/metadata.json
knowledge/scenarios.json
knowledge/stat_buckets.json
knowledge/items.json
knowledge/item_effects.json
knowledge/gear.json
knowledge/gear_sets.json
knowledge/survivors.json
knowledge/survivor_awakenings.json
knowledge/pets.json
knowledge/xeno_pets.json
knowledge/tech_parts.json
knowledge/collectibles.json
knowledge/collectible_sets.json
knowledge/mounts.json
knowledge/mount_components.json
knowledge/resources.json
knowledge/chests.json
knowledge/events.json
knowledge/event_shops.json
knowledge/clan_shop.json
knowledge/universal_exchange.json
knowledge/breakpoints.json
knowledge/rules.json
knowledge/hidden_interactions.json
knowledge/warnings.json
```

Every record should include as much source metadata as possible:

```json
{
  "id": "snake_case_id",
  "name": "Readable Name",
  "category": "category_name",
  "description": "",
  "effects": [],
  "tags": [],
  "source": "file/page/section if known",
  "source_type": "discord",
  "confidence": "medium",
  "notes": "",
  "scoring_relevance": "damage"
}
```

Allowed confidence values:

```text
low
medium
high
```

Allowed source types:

```text
game
discord
community-tested
veteran-rule
inferred
unknown
```

Allowed scoring relevance values:

```text
damage
resource
utility
survival
ignored_by_default
```

## Training / Knowledge Build

The project should include a command that can process the dataset folder repeatedly.

Example command:

```powershell
python tools/train_knowledge.py --data-folder folder --minutes 30 --device auto
```

The `--minutes` argument should control how long the training/indexing run is allowed to work. The process should checkpoint progress so it can resume later.

The trainer should:

1. scan `folder/`
2. detect file types
3. convert images if needed
4. extract text from PDFs and text files
5. OCR images when possible
6. chunk large documents
7. classify chunks by system/category
8. extract optimizer-relevant facts/rules
9. write draft JSON into `ai_outputs/` or `training_outputs/`
10. validate records
11. merge clean records into `knowledge/`
12. build an index/cache for faster future runs
13. log skipped files, OCR failures, conflicts, and uncertain records

The trainer should be resumable. It should save progress to something like:

```text
training_state/
training_state/processed_files.json
training_state/checkpoints/
training_state/logs/
```

If a file was already processed and has not changed, the trainer should skip it unless `--force` is used.

## GPU / CPU Note

GPU support is optional. Most of this project is data extraction, OCR, parsing, rule-building, and scoring. Those steps are usually CPU-heavy, not GPU-heavy. GPU may help later if embeddings, OCR acceleration, or a real local model is added. The project should support:

```text
--device cpu
--device gpu
--device auto
```

But the code should work on CPU first.

### Optimizer Simulation Training

Knowledge building and optimizer simulation are separate jobs. To run complete
depth-2 chain and global planning for every synthetic profile for 50 minutes:

```powershell
.\tools\run_training.ps1 -Minutes 50 -ProfileCount 5000 -Workers auto -Device auto -BatchSize 4096
```

The PowerShell wrapper defaults to full-search, time-bound training. It keeps
generating profiles until the deadline, finishes the active worker batch, uses
CPU workers for rule/action/chain simulation, and uses CUDA for numeric scoring
when CUDA is available. GPU rows are numeric action candidates, not complete
profiles, so their rate must not be compared directly with CPU profiles/sec.

Use `-FastMode` only when interval-based deep searches are acceptable. Use
`-NoGpuScore`, `-NoGlobalPlanner`, or `-StopWhenExhausted` to explicitly disable
those default behaviors.

For a long adaptive run with compact CUDA-generated profile seeds:

```powershell
.\tools\run_training.ps1 -Minutes 600 -ProfileCount 500000 -Workers auto -Device auto -GpuScore -BatchSize 8192 -TimeOnly -ComboMode -ChainDepth 2 -BeamSize 150 -MaxActionsPerProfile 1000 -CoverageReport -Fresh -KeepGenerating -LearnedPruning normal -ExplorationRate 0.08 -AuditFullSearchInterval 100 -GpuProfileFeatures -ProfileBatchSize 65536 -MaterializeProfiles on_demand
```

With `on_demand`, `ProfileCount` is a target/seed-pool size rather than an
upfront JSON allocation. The wrapper writes 100 initial profiles, then a
bounded producer creates compact numeric seed batches while CPU workers run
legal transitions and the CUDA worker scores numeric action rows. NPU remains
disabled unless an actual ONNX NPU provider and model are available.

The optimizer supports ten scenario records: short-term boss damage, long-term
account damage, balanced progression, event-shop value, F2P gem conservation,
chapter push, clan-shop planning, pet/Xeno progression, gear/SS/astral-forge
progression, and collectible-set progression. `optimize(player_state)` returns
a JSON-serializable website contract with the recommendation, ranked and
rejected alternatives, global action chain, resources used/saved, damage and
long-term values, warnings, assumptions, future goals, missing-data status,
confidence, and next action. Pass `include_global_plan=False` only for internal
high-throughput training where the global planner runs separately.

Optimizer checkpoints are written atomically to
`training_state/checkpoints/optimizer_latest.json`; compact learned memory is
written to `training_state/learning_memory.json`. Use `-CheckpointInterval` to
change the default 30-second checkpoint period.

## Website Compatibility

The final website should be easy to build because the optimizer should expose a clean API.

The optimizer should accept a player state dictionary and return a structured recommendation dictionary.

Example:

```python
from optimizer.main import optimize

result = optimize(player_state)
```

The returned result should be JSON-serializable so it can be used by:

```text
CLI
local scripts
future web UI
Pyodide browser version
static website
```

Avoid backend-only design. Avoid requiring paid APIs at runtime. The final site should be able to run client-side or mostly client-side.

## Current Important Rule

Do not rush into UI. First make the knowledge pipeline and optimizer brain strong.

The project order should be:

1. stable repo
2. knowledge schema
3. data extraction
4. training/indexing pipeline
5. validated knowledge base
6. recommendation engine
7. explainable scoring
8. CLI demo
9. simple website
10. import/export player state
