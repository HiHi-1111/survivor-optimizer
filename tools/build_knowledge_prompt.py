"""Build AI extraction prompts from extracted text."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTRACTED_DIR = ROOT / "data_sources" / "extracted" / "text"
PROMPT_DIR = ROOT / "archive" / "ai" / "prompts" / "generated"

SUPPORTED_SYSTEMS = """
gear, gear rarity, eternal/void/chaos/SS gear, astral forge, cosmic cast,
relic cores, astral cores, xeno cores, resonance chips, core selector chests,
survivors, survivor awakening, pets, xeno pets, pet awakening, assisting pets,
tech parts, resonance, collectibles, collectible selector chests, salvage,
event shops, event currencies, player stats, detailed damage stats, conditional
damage bonuses, debuffs, enemy states, mode-specific recommendations, hidden or
uncertain interactions, and community-tested veteran rules.
""".strip()


PROMPT_TEMPLATE = """Extract structured Survivor.io optimizer knowledge from the text below.

Supported systems:
{systems}

Return JSON only. Do not include markdown, comments, prose, or explanations
outside the JSON object. Do not invent stats, costs, percentages, breakpoints, or
rankings.

Allowed top-level sections only:
items, item_effects, gear, gear_sets, survivors, survivor_awakenings, pets,
xeno_pets, tech_parts, collectibles, collectible_sets, resources, chests, events,
event_shops, breakpoints, rules, hidden_interactions, warnings.

Every record must use snake_case ids and include:
- id
- name
- category or type
- description
- effects
- tags
- source
- source_type
- date
- confidence
- notes
- scoring_relevance

Allowed confidence values: low, medium, high.
Allowed source_type values: game, discord, community-tested, veteran-rule,
inferred, unknown.
Allowed scoring_relevance values: damage, resource, utility, survival,
ignored_by_default. Survival-only records should include survival and
ignored_by_default.

Distinguish official game text from Discord, community-tested, veteran-rule, and
inferred information. If source quality is unclear, use source_type "unknown" and
confidence "low".

If info is unclear, uncertain, disputed, or incomplete, put it in warnings or
hidden_interactions with confidence "low" and explain the uncertainty in notes.
Records with confidence "low" should have notes.

If information belongs to multiple systems, duplicate references by id in the
relevant records instead of writing vague text.

Extraction rules:
- If a guide mentions "best", "priority", "worth it", "trap", "avoid",
  "only good if", "breakpoint", "scales later", or "not worth without X",
  extract that as a rule.
- If a guide mentions "works differently than written", "hidden", "tested",
  "overperforms", "underperforms", or "veteran knowledge", extract that as a
  hidden_interaction.
- If a guide mentions upgrade thresholds, costs, stars, awakening, cores, AF,
  EE, CE, PoT, or mode differences, extract those as breakpoints or rules.

Source file: {source}

TEXT:
{text}
"""


def build_prompts() -> list[Path]:
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for path in EXTRACTED_DIR.rglob("*.txt"):
        if path.name == ".gitkeep":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            continue
        relative = path.relative_to(EXTRACTED_DIR)
        output_path = PROMPT_DIR / relative.with_suffix(".prompt.txt")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            PROMPT_TEMPLATE.format(systems=SUPPORTED_SYSTEMS, source=str(relative), text=text),
            encoding="utf-8",
        )
        written.append(output_path)
        print(f"wrote {output_path.relative_to(ROOT)}")
    if not written:
        print("no extracted text found; no prompts written")
    return written


if __name__ == "__main__":
    build_prompts()
