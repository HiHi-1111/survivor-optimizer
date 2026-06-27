# Survivor.io Optimizer - Dataset Extraction + Logic/GPU Upgrade Prompt

You are working on my Survivor.io optimizer project. I have a Google Drive folder of guide images/PDFs from Survivor.io community/game guides. These files are mostly screenshots/images, so do not assume normal text extraction will work. Build a serious data ingestion + verification pipeline and then connect the extracted data to the optimizer.

## High-level goal
Turn my guide images/PDFs into structured optimizer knowledge, then use that knowledge to make the optimizer smarter, more complete, and more GPU-accelerated.

The optimizer should be damage-first, full-mode, explainable, and future website/app ready. It should not invent fake stats. If a value cannot be confidently extracted from an image, put it in a manual review queue instead of guessing.

## Known guide/data categories in the Drive folder
I have files covering these systems and probably more:
- Tech Part Resonance: Drone
- Tech Part Resonance: RPG / HI Maintainer
- Tech Part Resonance Cost
- Weapons
- Boots
- Gloves
- Necklaces
- Skills
- Clan Shop
- Survivors
- Survivor SP / special survivor data
- Merging - Pets
- Pet Awakening Guide - Rex
- Crit Rate and Crit Damage
- Energy Essence Cost
- Full Energy Essence Cost
- Collectible Chest Odds
- S Grade Supply Crate Wishlist Probabilities / chest odds PDF

Do not hardcode only these. Scan every image/PDF in the dataset folder and build a manifest.

## Important rule
These images cannot be trusted through blind OCR alone. They contain icons, tables, rarity colors, percentages, levels, costs, and small text. Build a hybrid pipeline:
1. Convert all TIFF/PDF/image files into readable PNG previews.
2. Run OCR/table extraction where possible.
3. Preserve raw OCR text, image filename, bounding boxes if available, and confidence.
4. Use filename/category to infer the system.
5. Normalize names and values into structured JSON.
6. Put uncertain values into a manual review queue.
7. Never invent missing values.
8. Never silently overwrite confirmed data with low-confidence OCR.

## Folder/data ingestion requirements
Add a tool such as:
python tools/ingest_guide_images.py --input raw_data/guide_images --output knowledge/extracted --manual-review

It should:
- recursively scan .png, .jpg, .jpeg, .webp, .tif, .tiff, and .pdf
- convert TIF/TIFF/PDF pages to PNG previews
- create a manifest of every source file
- classify each file by likely system based on filename and extracted text
- store original source filename, page number, image hash, modified date, source path, and extraction timestamp
- save raw OCR output even when messy
- save structured extraction separately from raw OCR
- create a manual review queue for low-confidence or conflicting values

Expected output files:
- knowledge/extracted/source_manifest.json
- knowledge/extracted/raw_ocr.jsonl
- knowledge/extracted/table_candidates.jsonl
- knowledge/extracted/manual_review_queue.jsonl
- knowledge/extracted/extraction_summary.md
- knowledge/extracted/extraction_conflicts.json
- knowledge/extracted/extraction_confidence_report.json

## Data confidence model
Every extracted fact must include:
- source_file
- source_page if applicable
- source_image_hash
- raw_text
- normalized_value
- confidence
- extraction_method
- needs_review true/false
- notes

Confidence levels:
- confirmed: manually verified or exact structured source
- high: OCR/table extraction is clean and matches expected pattern
- medium: likely correct but visual/OCR ambiguity exists
- low: uncertain and must not be used for final scoring without warning
- missing: known required data is unavailable

## Knowledge schemas to build/update
Do not dump everything into one giant file. Create or update structured knowledge files.

Needed knowledge files:
- knowledge/items.json
- knowledge/item_effects.json
- knowledge/gear.json
- knowledge/gear_sets.json
- knowledge/weapons.json if separate
- knowledge/skills.json
- knowledge/survivors.json
- knowledge/survivor_awakenings.json
- knowledge/survivor_energy_essence_costs.json
- knowledge/pets.json
- knowledge/pet_merging.json
- knowledge/pet_awakenings.json
- knowledge/xeno_pets.json
- knowledge/tech_parts.json
- knowledge/tech_resonance.json
- knowledge/tech_resonance_costs.json
- knowledge/collectibles.json
- knowledge/collectible_sets.json
- knowledge/collectible_chest_odds.json
- knowledge/chests.json
- knowledge/chest_odds.json
- knowledge/event_shops.json
- knowledge/clan_shop.json
- knowledge/resources.json
- knowledge/conversions.json
- knowledge/crit_stats.json
- knowledge/breakpoints.json
- knowledge/rules.json
- knowledge/hidden_interactions.json
- knowledge/warnings.json
- knowledge/source_confidence.json

## Normalization rules
Create a canonical ID system. OCR may read names inconsistently, so normalize:
- item names
- gear names
- survivor names
- pet names
- tech part names
- collectible names
- resource names
- rarity names/colors
- percent values
- cost values
- odds/probability values
- level/star/awakening notation

Example normalized fields:
- id
- display_name
- aliases
- system
- subtype
- rarity
- level_requirement
- cost
- resource_type
- effect_type
- effect_value
- damage_relevance
- scenario_relevance
- source_refs
- confidence
- needs_review

## Damage-first logic
The optimizer is damage-first.
- Prioritize DPS, boss damage, damage multipliers, crit rate, crit damage, attack, skill damage, pet damage if relevant, tech damage, collectible damage, and damage breakpoints.
- Ignore HP/healing/defense/revive/survival-only effects unless they directly enable damage progress.
- Store survival data, but default scoring should not overvalue it.
- If survival enables farming/chapter progress that unlocks damage systems, mark that as indirect damage value and explain it.

## System-specific extraction targets

### Gear/weapons/boots/gloves/necklaces
Extract:
- item name
- slot
- rarity tiers
- rarity effects
- damage effects
- crit effects
- special passives
- AF/SS/resonance requirements if present
- upgrade or merge requirements if present
- effect unlock conditions
- damage relevance tag
- OCR confidence and source

### Skills
Extract:
- skill name
- evolution relationship if shown
- damage-relevant effects
- support/passive relationships
- synergy tags
- scenario relevance

### Survivors and SP survivors
Extract:
- survivor name
- unlock/upgrade shards if shown
- energy essence costs if shown
- level thresholds
- star/awakening effects
- damage/crit/attack bonuses
- all-survivor bonuses if shown
- whether effect is personal-only or account-wide

### Energy essence cost
Extract level ranges, cost per level, total cost, survivor upgrade thresholds, and source confidence. This is important for long-term planning.

### Pets and pet merging
Extract:
- pet names
- rarity progression
- merge rules
- awakening resources
- awakening effects
- cookie costs if present
- pet crystal costs if present
- xeno-related requirements if present
- damage vs survival classification

### Pet Awakening - Rex
Extract Rex-specific awakening path, costs, star requirements, skill/effect changes, and damage relevance. Store as pet-specific data, not generic pet data.

### Tech part resonance
Extract:
- tech part name
- resonance costs
- resonance level/thresholds
- special effects
- damage relevance
- drone/RPG-specific tables
- resource requirements
- system unlock conditions if present

### Crit rate and damage
Extract formulas or reference rules for crit rate, crit damage, additive vs multiplicative behavior if shown, and any warnings. This should feed scoring logic.

### Collectible chest odds and collectible sets
Extract:
- chest type
- odds/probabilities
- collectible rarity odds
- expected value fields
- set bonuses if available
- damage-relevant collectible effects
- missing collectible data warnings

### Clan shop
Extract:
- shop items
- prices
- currency type
- refresh/cycle rules if present
- purchase value category
- damage relevance
- whether item should be bought/saved/skipped by profile type

### Chests / S Grade Supply Crate odds / wishlist probabilities
Extract:
- chest name
- wishlist rules
- empty wishlist probabilities
- partial wishlist probabilities
- full wishlist probabilities
- S-grade probabilities
- specific item probabilities if shown
- expected value logic
- how wishlist changes recommendation value

## Optimizer integration
After extracting data, connect it to the optimizer.

Add or improve action generators for:
- gear
- weapons
- boots
- gloves
- necklaces
- skills if applicable
- SS gear
- astral forge
- pets
- pet merging
- pet awakening
- xeno pets
- tech parts
- tech resonance
- survivors
- survivor awakening
- energy essence upgrades
- collectibles
- collectible chest odds
- collectible sets
- chests
- S grade wishlist odds
- clan shop
- event shops
- shard/resource conversions
- save/hold decisions

Each action generator should return:
- action_id
- system
- action_type
- required_resources
- consumed_resources
- produced_state_delta
- expected_damage_delta
- long_term_value_delta
- risk/uncertainty
- source_refs
- confidence
- missing_data_warnings
- can_score_now true/false

## Whole-inventory planner
Do not score items in isolation. The optimizer must evaluate action chains using the whole inventory.

Examples:
- selector + core + SS gear path
- pet crystals + pet shards + pet awakening breakpoint
- event shop currency + chest odds + missing resource bottleneck
- clan shop purchase + future upgrade chain
- survivor shards + energy essence + survivor all-account bonus
- collectible chest odds + set bonus breakpoint
- save gems for future event vs spend now

Score final account state after a chain, not just the first action.

## GPU/CUDA upgrade
The GPU should do more than tiny leftover scoring. Restructure the planner so more candidate actions/chains become numeric feature rows that CUDA can score/rank in large batches.

CPU responsibilities:
- parse knowledge
- generate actions
- apply actions to states
- branch/search
- manage queues
- handle rules/exceptions
- explain recommendations

GPU responsibilities:
- score candidate actions in batches
- score candidate chains in batches
- rank beam search candidates
- run learned pruning/ranker model
- evaluate numeric profile/action matrices
- compare final states where numeric features are ready

Do not move random branchy Python logic to GPU. Instead, create better numeric feature rows.

Improve GPU features:
- profile_stage
- scenario_id
- system_type
- action_type
- resource_costs
- damage_gain_estimate
- crit_gain_estimate
- attack_gain_estimate
- long_term_value
- breakpoint_distance
- resource_bottleneck_score
- synergy_score
- save_value
- chest_expected_value
- confidence_score
- missing_data_penalty
- source_confidence

Improve GPU metrics:
- gpu_requested
- cuda_available
- gpu_initialized
- gpu_pipeline_active
- gpu_actually_used
- gpu_rows_submitted
- gpu_rows_scored
- gpu_wall_rows_per_sec
- gpu_active_compute_rows_per_sec
- gpu_batch_utilization
- average_gpu_batch_size
- gpu_queue_size
- cpu_waiting_on_gpu
- gpu_waiting_on_cpu

Goal: increase GPU batch utilization and wall rows/sec without reducing recommendation quality.

## Learned training upgrade
Make training actually improve the optimizer over time.

Persistent learned memory should track:
- best chains by profile bucket
- bad chains by profile bucket
- high-value resource bottlenecks
- near-breakpoint wins
- save/hold wins
- false prune examples
- strong action combinations
- weak action combinations
- scenario-specific action value
- system-specific priors

Profile buckets should include:
- early game
- mid game
- late game
- end game
- F2P
- gem-heavy
- gem-poor
- chest-heavy
- selector-heavy
- shard-heavy
- pet-heavy
- gear-heavy
- SS-heavy
- collectible-heavy
- tech-heavy
- event-heavy
- clan-shop-heavy
- near-pet-breakpoint
- near-gear-breakpoint
- near-survivor-breakpoint
- near-collectible-set-breakpoint
- bottlenecked profiles

Use learned priors to:
- reorder actions
- choose beam candidates
- prune weak branches
- select which systems to expand deeper
- decide when save/hold is better

Add audits:
- run full search periodically
- compare pruned vs full result
- track false prune rate
- reduce pruning strength if false prunes rise
- never allow learned pruning to silently ruin recommendations

## Website/app future compatibility
Keep optimizer core separate from training/CLI. The future API should be:
from optimizer.main import optimize
result = optimize(player_state)

Result must be JSON-serializable and include:
- recommendation
- ranked alternatives
- action chain
- resources used
- resources saved
- expected damage gain
- long-term value
- scenario
- explanation
- rejected alternatives
- warnings
- assumptions
- source_refs
- confidence
- missing_data
- next goal

Future features to prepare for:
- web UI
- user profile input form
- screenshot import later
- save key import/export
- local/client-side mode if possible
- event shop calculator
- Black Friday/gem planner
- clan shop planner
- pet/xeno planner
- SS/astral forge planner
- collectible set planner
- long-term roadmap generator
- hardware/training dashboard
- source/confidence viewer
- manual data correction workflow

## Validation and tests
Add tests for:
- image/PDF ingest manifest creation
- OCR output stored with confidence
- manual review queue created
- no fake stats inserted
- knowledge schema validation
- source refs preserved
- action generators register correctly
- unsupported/partial systems reported honestly
- GPU fallback to CPU
- NPU unavailable fallback
- deep chain planning still works
- learned pruning audits
- JSON output shape for website API
- Windows temp-folder reliability

Run:
python tools/ingest_guide_images.py --input raw_data/guide_images --output knowledge/extracted --manual-review
python tools/validate_knowledge.py
python tools/gpu_smoke_test.py
python tools/npu_probe.py
python run_demo.py
python -m pytest --basetemp .\.tmp\pytest-clean

Then run a training test:
.\tools\run_training.ps1 -Minutes 6 -ProfileCount 5000 -Workers auto -Device cuda -BatchSize 8192 -GpuScore -ComboMode -ChainDepth 2 -BeamSize 150 -MaxActionsPerProfile 1000 -KeepGenerating -LearnedPruning normal -ExplorationRate 0.08 -AuditFullSearchInterval 100 -CoverageReport

## Acceptance criteria
Show:
- all source images/PDFs discovered
- extraction summary by system
- manual review queue count
- new/updated knowledge files
- systems fully supported, partially supported, unsupported
- action generators added
- before/after systems covered
- before/after GPU batch utilization
- before/after GPU wall rows/sec
- before/after profiles/sec
- learned memory samples added
- false prune rate
- example recommendation with explanation and source refs
- test results

Final goal: build a real data-driven Survivor.io optimizer that uses the guide images as structured knowledge, improves over time through training, uses CPU + GPU correctly, stays damage-first, avoids fake data, and is ready to become a free website/app later.
