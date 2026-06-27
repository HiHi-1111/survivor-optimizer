const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

let currentScenario = "clan";
let activeSurvivor = "Master Yang";
let activeSourceCategory = "All";
const enabledSystems = new Set();

const survivors = [
  ["Master Yang", "Y", 1.18], ["Metallia", "M", 1.14], ["King", "K", 1.11], ["Common", "C", 1.00],
  ["Worm", "W", 1.06], ["Catnips", "N", 1.04], ["TMNT", "T", 1.10], ["Other", "O", 1.02]
];

const activeSkills = ["Kunai", "Void Power", "Lightchaser", "Drone", "RPG", "Molotov", "Soccer Ball", "Drill", "Lightning", "Guardian", "Forcefield", "Brick", "Durian", "Laser", "Boomerang", "Modular Mine", "Thunderbolt Bomb", "Inferno Bomb"];
const passiveItems = ["Koga Scroll", "Energy Cube", "HE Fuel", "Ammo Thruster", "Hi-Power Bullet", "Exo-Bracer", "Fitness Guide", "Sports Shoes", "Ronin Oyoroi", "Oil Bond", "Magnet", "None"];
const evolutionPairs = { "Kunai":"Koga Scroll", "Void Power":"Exo-Bracer", "Lightchaser":"Ronin Oyoroi", "Drone":"Hi-Power Bullet", "RPG":"HE Fuel", "Molotov":"Oil Bond", "Soccer Ball":"Sports Shoes", "Drill":"Ammo Thruster", "Lightning":"Energy Cube", "Guardian":"Exo-Bracer", "Forcefield":"Energy Cube", "Brick":"Fitness Guide", "Durian":"HE Fuel", "Laser":"Energy Cube", "Boomerang":"Magnet" };
const gearSlots = ["Weapon", "Necklace", "Gloves", "Armor", "Belt", "Boots"];
const gearOptions = ["SS Starforged Havoc", "SS Necklace", "SS Gloves", "SS Armor", "SS Belt", "SS Boots", "Kunai", "Void Power", "Lightchaser", "SoD", "Voidwaker", "Eternal", "Chaos", "Army", "Other"];
const techOptions = ["Drone", "Forcefield", "Drill", "RPG", "Lightning", "Boomerang", "Laser", "Guardian", "Molotov", "Brick", "Durian", "Soccer", "Normal Drone Resonance", "Normal RPG Resonance", "Normal Molotov Resonance"];
const collectibleNames = ["Crit", "ATK", "Drone", "Pet", "Boss", "Chapter", "Relic", "Tech", "Event"];
const exclusions = ["Void Armor", "Chaos Belt", "Manual Builds", "HP-Only Picks", "Gem Spending", "Unowned SS", "Pet Copies", "Collectibles", "Normal RPG/Molotov Resonance", "SS Armor Early", "SS Necklace Low Crit"];

const sourceSystems = [
  { id:"chapter_unlocks", cat:"Unlocks", name:"Chapter Unlocks", unlock:"Always", layer:"gating", tags:["shop","equipment","tech","pets","clan","EE","Zone Ops"], note:"Controls which systems can be recommended by chapter." },
  { id:"skills", cat:"Unlocks", name:"Skills + Evo Pairings", unlock:"Chapter gated", layer:"run", tags:["actives","passives","evo","tech links"], note:"SkillName, EvoName, PassiveRequired, TechPartAssociation, BossValue." },
  { id:"wishlist", cat:"Economy", name:"Wishlist Probabilities", unlock:"Chest system", layer:"EV", tags:["keys","odds","target gear"], note:"Expected value and target pull chance." },
  { id:"muster", cat:"Economy", name:"Timed Muster Medal Conversion", unlock:"Event", layer:"value", tags:["medals","conversion"], note:"Spend/convert medal value." },

  { id:"crit", cat:"Damage", name:"Crit Rate / Crit Damage", unlock:"Always", layer:"DPS", tags:["global crit","gear crit","pet crit","breakpoints"], note:"Crit must be split by source; not one flat number." },
  { id:"debuffs", cat:"Damage", name:"Boss Debuffs", unlock:"Always", layer:"DPS", tags:["vulnerability","weaken","chill","poison","laceration","deep wound"], note:"Damage layer for debuff uptime and target multipliers." },
  { id:"shield_skill", cat:"Damage", name:"Shield + Skill Multipliers", unlock:"Gear/skills", layer:"DPS", tags:["shield damage","skill damage"], note:"Keeps shield damage and skill damage as separate multiplier layers." },
  { id:"damage_priority", cat:"Damage", name:"Damage-First Scoring Rules", unlock:"Always", layer:"ranking", tags:["ignore HP","DPS per cost"], note:"High score: ATK, crit, skill damage, debuffs, boss damage, pet DPS." },

  { id:"survivor_base", cat:"Survivors", name:"Survivor Base + Levels", unlock:"Chapter 9", layer:"stats", tags:["level 40","level 80","level 120"], note:"Global ATK, crit, crit damage, and active survivor value." },
  { id:"survivors_s", cat:"Survivors", name:"S Survivors", unlock:"Chapter 9", layer:"DPS", tags:["Yang","Metallia","King","Worm","Catnips"], note:"Active-skill and mode-specific survivor scoring." },
  { id:"survivors_sp", cat:"Survivors", name:"SP / TMNT Survivors", unlock:"Event", layer:"DPS", tags:["Raphael","Leonardo","Donatello","April","Splinter"], note:"TMNT 5-star crit, teamwork passives, laceration, pet buffs." },
  { id:"yang", cat:"Survivors", name:"Master Yang Skills", unlock:"Yang owned", layer:"DPS", tags:["Palm","Yin Yang","stance"], note:"Yang-specific crit and stance effects." },
  { id:"awakening_core", cat:"Survivors", name:"Awakening Core Guide", unlock:"Awakening", layer:"cost", tags:["Yang","Metallia","King","TMNT","CE","EE"], note:"Spend awakening cores by mode and DPS gain per core." },
  { id:"combat_harmony", cat:"Survivors", name:"Combat Harmony", unlock:"Harmony", layer:"global", tags:["crit rate","global"], note:"Global crit rate and harmony skill upgrades." },
  { id:"survivor_synergy", cat:"Survivors", name:"Survivor Synergy", unlock:"Synergy", layer:"global", tags:["crit damage","global"], note:"Global crit damage from synergy levels." },
  { id:"survivor_costs", cat:"Survivors", name:"Survivor Upgrade Costs", unlock:"Chapter 9", layer:"cost", tags:["essence","shards","levels"], note:"Energy essence and shard investment cost." },

  { id:"normal_gear", cat:"Gear", name:"Normal / S Gear Effects", unlock:"Chapter 1", layer:"DPS", tags:["weapons","necklace","gloves","armor","belt","boots"], note:"Normal, S, and slot-specific effects." },
  { id:"ss_gear", cat:"Gear", name:"SS Gear Effects", unlock:"Late gear", layer:"DPS", tags:["SS weapon","SS belt","SS gloves","SS boots"], note:"Starforged Havoc, Mega Crit, Weaken, energy levels, chill." },
  { id:"astral_forge", cat:"Gear", name:"Astral Forge", unlock:"Legendary gear", layer:"cost", tags:["AF","E path","V path","C path"], note:"Forge levels, material costs, salvage returns." },
  { id:"relic_core_ee", cat:"Gear", name:"Relic Core Usage: Ender's Echo", unlock:"SS gear", layer:"cost", tags:["relic cores","EE","SS spread"], note:"Best relic-core spread for boss scoring." },
  { id:"relic_core_clan", cat:"Gear", name:"Relic Core Usage: Clan Expedition", unlock:"SS gear", layer:"cost", tags:["relic cores","CE","SS spread"], note:"Best relic-core spread for clan expedition." },
  { id:"xeno_transmute", cat:"Gear", name:"Xeno Transmute", unlock:"E4 + V4", layer:"late", tags:["XTC","Twin Lance","gems"], note:"Xeno trigger priorities and xeno core/gem costs." },
  { id:"gear_meta", cat:"Gear", name:"Equipment Meta Guide", unlock:"Always", layer:"priority", tags:["SS order","wishlist","selectors"], note:"Progression warnings and recommended creation order." },
  { id:"merge_equip", cat:"Gear", name:"Merging Equip", unlock:"Chapter 1", layer:"cost", tags:["rarity","fodder"], note:"Merging requirements and fodder planning." },
  { id:"design_costs", cat:"Gear", name:"Equip Design Costs", unlock:"Gear", layer:"cost", tags:["designs","slot costs"], note:"Design currencies by slot and upgrade." },

  { id:"tech_parts", cat:"Tech", name:"Tech Parts", unlock:"Chapter 6", layer:"DPS", tags:["Drone","RPG","Molotov","Guardian","Forcefield"], note:"Tech modifies in-run skills and unlocks major DPS systems." },
  { id:"normal_resonance", cat:"Tech", name:"Normal Tech Resonance", unlock:"Legendary tech", layer:"DPS", tags:["9000 max","offensive/defensive"], note:"Normal resonance rules and restrictions." },
  { id:"drone_res", cat:"Tech", name:"Drone Resonance", unlock:"Legendary Drone", layer:"DPS", tags:["Attack Wingbit","Augment Wingbit"], note:"Best normal resonance before Twinborn." },
  { id:"rpg_res", cat:"Tech", name:"RPG / C4 Resonance", unlock:"Legendary RPG", layer:"DPS", tags:["C4","Sharkmaw","chill"], note:"Explosion radius, interval, delay, and C4 scaling." },
  { id:"molotov_res", cat:"Tech", name:"Molotov Resonance", unlock:"Legendary Molotov", layer:"DPS", tags:["Soulfire","Poison","Deep Wound"], note:"Fire, poison, and Deep Wound resonance path." },
  { id:"twinborn_rules", cat:"Tech", name:"Twinborn Rules", unlock:"Two legendary paired techs", layer:"gating", tags:["15000 max","any assist","one mode"], note:"Twinborn unlocks, assist freedom, and one-mode rule." },
  { id:"tech_overload", cat:"Tech", name:"Tech Overload", unlock:"3000 Twinborn resonance", layer:"late", tags:["overload","harmony chips"], note:"Unlocked at 3000 Twinborn resonance; max level tied to 15000." },
  { id:"tb_drone_forcefield", cat:"Twinborn", name:"Twinborn Drone + Forcefield", unlock:"Legendary pair", layer:"DPS", tags:["Destroyer","Force Barrier","highest priority"], note:"Top Twinborn pair with missile scaling and skill damage." },
  { id:"tb_drill_rpg", cat:"Twinborn", name:"Twinborn Drill + RPG", unlock:"Legendary pair", layer:"DPS", tags:["Frostfire","Sharkmaw","poison","laceration"], note:"Strong late damage, fire/ice/poison/laceration path." },
  { id:"tb_light_boom", cat:"Twinborn", name:"Twinborn Lightning + Boomerang", unlock:"Legendary pair", layer:"DPS", tags:["vulnerability","weaken","skill damage"], note:"Crit, skill damage, vulnerability, and weakened damage." },
  { id:"tb_laser_guardian", cat:"Twinborn", name:"Twinborn Laser + Guardian", unlock:"Legendary pair", layer:"DPS", tags:["death ray","crit","skill"], note:"Crit and lock-on laser scaling." },
  { id:"tb_molotov_brick", cat:"Twinborn", name:"Twinborn Molotov + Brick", unlock:"Legendary pair", layer:"DPS", tags:["deep wound","weaken","burn"], note:"Deep Wound, skill damage, shield damage, weakened target damage." },
  { id:"tb_soccer_durian", cat:"Twinborn", name:"Twinborn Soccer + Durian", unlock:"Legendary pair", layer:"DPS", tags:["chill","poison","crit"], note:"Lower priority but still real crit/chill/poison scaling." },
  { id:"merge_tech", cat:"Tech", name:"Merging Tech Parts", unlock:"Tech", layer:"cost", tags:["rarity","fodder"], note:"Tech part merge cost and fodder planning." },

  { id:"pets", cat:"Pets", name:"Pets Base Table", unlock:"Chapter 9", layer:"DPS", tags:["Murica","Croaky","Rex","Crab","Robot"], note:"Direct pet damage and pet identity." },
  { id:"pet_skills", cat:"Pets", name:"Pet Skills", unlock:"Pets", layer:"DPS", tags:["Motivation","Inspiration","Raid","Crush"], note:"Owner buffs, pet DPS skills, assist skills." },
  { id:"pet_awakening", cat:"Pets", name:"Pet Awakening Costs", unlock:"Pet awakening", layer:"cost", tags:["Y1-R5","350 crystals","affection ATK"], note:"Awakening crystals, affection ATK, toy bonuses." },
  { id:"murica_guide", cat:"Pets", name:"Murica Guide", unlock:"Murica", layer:"priority", tags:["Y5","Raid","Powerful","Penetration"], note:"Direct pet DPS path and when to consider switching." },
  { id:"croaky_guide", cat:"Pets", name:"Croaky Guide", unlock:"Croaky", layer:"priority", tags:["R5","owner skill damage","crit"], note:"Strong long-term owner/pet DPS mix." },
  { id:"rex_guide", cat:"Pets", name:"Rex Guide", unlock:"Rex", layer:"priority", tags:["R5","crit rate","crit damage"], note:"Long-term owner crit support and total damage contender." },
  { id:"merge_pets", cat:"Pets", name:"Merging Pets", unlock:"Pets", layer:"cost", tags:["copies","rarity"], note:"Pet copy and rarity requirements." },
  { id:"xeno_pets", cat:"Pets", name:"Xeno Pets", unlock:"Late pets", layer:"late", tags:["xeno","crystals","copies"], note:"Late pet saving and normal-vs-xeno decision." },

  { id:"collectible_odds", cat:"Collectibles", name:"Collectible Chest Odds", unlock:"Collectibles", layer:"EV", tags:["rarity odds","shards"], note:"Chest odds and shard probabilities." },
  { id:"collectible_costs", cat:"Collectibles", name:"Collectible Costs", unlock:"Collectibles", layer:"cost", tags:["stars","hearts","exchange"], note:"Star costs, shard costs, exchange rates, hearts." },
  { id:"collectible_editions", cat:"Collectibles", name:"Collectible Editions 1-3", unlock:"Collectibles", layer:"pool", tags:["item pools","drop rates"], note:"Edition pools and item drop rates." },
  { id:"collectible_sets", cat:"Collectibles", name:"Collectible Set Bonuses", unlock:"Collectibles", layer:"DPS", tags:["SS gear links","crit","skill damage"], note:"Set bonuses modify exact SS and Twinborn effects." },

  { id:"clan_shop", cat:"Clan", name:"Clan Shop", unlock:"Chapter 3", layer:"value", tags:["S selectors","shards","keys","stock"], note:"Clan currency value and stock by clan level." },
  { id:"clan_level", cat:"Clan", name:"Clan Level Rewards", unlock:"Chapter 3", layer:"global", tags:["clan EXP","size","ATK/HP"], note:"Clan perks, member boosts, quick earnings." },
  { id:"clan_expedition", cat:"Clan", name:"Clan Expedition Rewards", unlock:"Chapter 3", layer:"mode", tags:["CE","ranking","rewards"], note:"Clan expedition rewards and CE mode logic." },
  { id:"ee_rewards", cat:"Modes", name:"Ender's Echo Rewards", unlock:"Chapter 8", layer:"mode", tags:["boss","ranking","rewards"], note:"Boss DPS and ranking reward mode." },
  { id:"survivor_pass", cat:"Pass", name:"Survivor Pass Rewards", unlock:"Chapter 8", layer:"value", tags:["relic core","keys","shards"], note:"Free/paid pass reward valuation." },
  { id:"pass_exp", cat:"Pass", name:"Survivor Pass EXP", unlock:"Chapter 8", layer:"income", tags:["dailies","EE","CE","PoT"], note:"Pass EXP sources and seasonal cap." },

  { id:"gems", cat:"Economy", name:"Sources of Gems", unlock:"Always", layer:"income", tags:["gems","events","dailies"], note:"Gem income source modeling." },
  { id:"oil", cat:"Economy", name:"Sources of Oil", unlock:"Talents", layer:"income", tags:["oil","talents"], note:"Oil income and talent progression." },
  { id:"essence", cat:"Economy", name:"Energy Essence Costs", unlock:"Survivors", layer:"cost", tags:["survivor levels","essence"], note:"Survivor level cost planning." },
  { id:"item_values", cat:"Economy", name:"Item Values", unlock:"Always", layer:"EV", tags:["gem equivalent","shop","events"], note:"Gem-equivalent values for spend decisions." },
  { id:"universal_exchange", cat:"Economy", name:"Universal Exchange", unlock:"Late", layer:"value", tags:["xeno","shards","conversion"], note:"Conversion logic and rare bottleneck exchange." },

  { id:"mount_basics", cat:"Mounts", name:"Mount System Basics", unlock:"Mounts", layer:"DPS", tags:["vehicles","components","sync rate"], note:"Equipped mount skill plus inactive synced component stats." },
  { id:"mount_scooter", cat:"Mounts", name:"Electric Scooter", unlock:"30 shards", layer:"DPS", tags:["auto damage","laceration"], note:"Auto discs/lightning/laceration style mount." },
  { id:"mount_hoverboard", cat:"Mounts", name:"Tech Hoverboard", unlock:"50 shards", layer:"DPS", tags:["shield","avoid hits"], note:"Shield stack and no-hit style mount." },
  { id:"mount_doomsteed", cat:"Mounts", name:"Doomsteed", unlock:"80 shards + cores", layer:"DPS", tags:["boss hugging","movement"], note:"Boss movement-scaling mount." },
  { id:"mount_components", cat:"Mounts", name:"Mount Components + Sync", unlock:"Mounts", layer:"global", tags:["component board","inactive sync"], note:"Components installed in inactive mounts give partial value by sync rate." }
];

sourceSystems.forEach(s => enabledSystems.add(s.id));

function init() {
  renderSurvivors();
  renderSkillPairs();
  renderEquipment();
  renderTech();
  renderCollectibles();
  renderExclusions();
  renderSourceCategories();
  renderSourceSystems();
  bindEvents();
  updatePairing();
  updateShareString(false);
}

function bindEvents() {
  $$(".mode-btn").forEach(btn => btn.addEventListener("click", () => {
    currentScenario = btn.dataset.scenario;
    $$(".mode-btn").forEach(b => b.classList.remove("is-active"));
    btn.classList.add("is-active");
    updateShareString(false);
  }));
  ["chapter", "base-atk", "crit-rate", "crit-dmg"].forEach(id => $("#" + id)?.addEventListener("input", () => { renderSourceSystems(); updateShareString(false); }));
  $$(".resource-input").forEach(input => input.addEventListener("input", () => updateShareString(false)));
  $("#source-system-search")?.addEventListener("input", renderSourceSystems);
  $("#btn-copy-string")?.addEventListener("click", copyConfiguration);
  $("#btn-tech-order")?.addEventListener("click", generateTechChecklist);
  $("#tooltip-guide-trigger")?.addEventListener("click", () => { const tip = $("#collectible-tooltip"); tip.hidden = !tip.hidden; });
  $("#btn-execute-optimization")?.addEventListener("click", runOptimization);
}

function renderSurvivors() {
  $("#survivor-grid").innerHTML = survivors.map(s => `
    <button type="button" class="survivor-cell ${s[0] === activeSurvivor ? "is-active" : ""}" data-survivor="${s[0]}">
      <span class="survivor-icon">${s[1]}</span>
      <span class="survivor-name">${s[0]}</span>
    </button>`).join("");
  $$(".survivor-cell").forEach(cell => cell.addEventListener("click", () => {
    activeSurvivor = cell.dataset.survivor;
    $("#selected-survivor-label").textContent = activeSurvivor;
    renderSurvivors();
    updateShareString(false);
  }));
}

function optionList(list, selected = list[0]) {
  return list.map(x => `<option ${x === selected ? "selected" : ""}>${x}</option>`).join("");
}

function renderSkillPairs() {
  const defaults = ["Kunai", "Drone", "RPG", "Molotov", "Soccer Ball", "Lightning"];
  $("#skill-pairs").innerHTML = Array.from({ length: 6 }, (_, i) => {
    const active = defaults[i];
    const passive = evolutionPairs[active] || passiveItems[0];
    return `<div class="skill-row" data-index="${i}">
      <div class="mini-slot"><label>Active ${i + 1}</label><select class="active-select" data-index="${i}">${optionList(activeSkills, active)}</select></div>
      <span class="connector"></span>
      <div class="mini-slot"><label>Passive ${i + 1}</label><select class="passive-select" data-index="${i}">${optionList(passiveItems, passive)}</select></div>
    </div>`;
  }).join("");
  $$(".active-select,.passive-select").forEach(sel => sel.addEventListener("change", () => { updatePairing(); updateShareString(false); }));
}

function updatePairing() {
  let ready = 0;
  $$(".skill-row").forEach(row => {
    const i = row.dataset.index;
    const active = $(`.active-select[data-index='${i}']`).value;
    const passive = $(`.passive-select[data-index='${i}']`).value;
    const paired = evolutionPairs[active] === passive;
    row.classList.toggle("is-paired", paired);
    if (paired) ready++;
  });
  $("#evo-count").textContent = `${ready} ready`;
}

function renderEquipment() {
  $("#equipment-grid").innerHTML = gearSlots.map(slot => `
    <article class="gear-card grade-epic" data-slot="${slot}">
      <div class="gear-top">
        <span class="gear-slot-name">${slot}</span>
        <label class="forge-wrap">★ <input type="number" class="mini-input forge-stars-input" min="0" max="10" value="0"></label>
      </div>
      <select class="gear-selector">${gearOptions.map(g => `<option>${g} ${slot}</option>`).join("")}</select>
      <div class="grade-group">
        <button type="button" class="grade-btn is-active" data-grade="epic">Epic</button>
        <button type="button" class="grade-btn" data-grade="legendary">Legendary</button>
        <button type="button" class="grade-btn" data-grade="cosmic">Cosmic</button>
      </div>
      <div class="material-box"><label>Designs / cores / fodder</label><input type="number" class="mini-input input-designs" min="0" placeholder="0"></div>
    </article>`).join("");

  $$(".grade-btn").forEach(btn => btn.addEventListener("click", () => {
    const card = btn.closest(".gear-card");
    card.classList.remove("grade-epic", "grade-legendary", "grade-cosmic");
    card.classList.add("grade-" + btn.dataset.grade);
    card.querySelectorAll(".grade-btn").forEach(b => b.classList.remove("is-active"));
    btn.classList.add("is-active");
    updateShareString(false);
  }));
  $$(".gear-selector,.forge-stars-input,.input-designs").forEach(el => el.addEventListener("input", () => updateShareString(false)));
}

function renderTech() {
  $("#tech-grid").innerHTML = Array.from({ length: 6 }, (_, i) => `
    <div class="tech-cell">
      <select>${optionList(techOptions, techOptions[i])}</select>
      <input class="mini-input level-input" type="number" min="0" value="${i < 2 ? 3 : 0}">
    </div>`).join("");
}

function renderCollectibles() {
  $("#collectibles-grid").innerHTML = collectibleNames.map((name, i) => `
    <div class="collectible-cell"><label>${name}</label><input class="tier-input" type="number" min="0" value="${i < 2 ? 2 : 0}"></div>`).join("");
}

function renderExclusions() {
  $("#exclusion-toggles").innerHTML = exclusions.map(name => `<label class="toggle-pill"><input type="checkbox" class="exclude-toggle" data-id="${slug(name)}">${name}</label>`).join("");
  $$(".exclude-toggle").forEach(el => el.addEventListener("change", () => updateShareString(false)));
}

function renderSourceCategories() {
  const cats = ["All", ...new Set(sourceSystems.map(s => s.cat))];
  $("#source-category-tabs").innerHTML = cats.map(cat => `<button class="source-tab ${cat === activeSourceCategory ? "is-active" : ""}" type="button" data-cat="${cat}">${cat}</button>`).join("");
  $$(".source-tab").forEach(tab => tab.addEventListener("click", () => {
    activeSourceCategory = tab.dataset.cat;
    renderSourceCategories();
    renderSourceSystems();
  }));
}

function renderSourceSystems() {
  const q = ($("#source-system-search")?.value || "").toLowerCase().trim();
  const chapter = val("chapter") || 1;
  const filtered = sourceSystems.filter(sys => {
    const text = `${sys.name} ${sys.cat} ${sys.unlock} ${sys.layer} ${sys.tags.join(" ")} ${sys.note}`.toLowerCase();
    return (activeSourceCategory === "All" || sys.cat === activeSourceCategory) && (!q || text.includes(q));
  });
  $("#source-system-list").innerHTML = filtered.map(sys => {
    const on = enabledSystems.has(sys.id);
    const locked = isChapterLocked(sys, chapter);
    return `<button type="button" class="source-item ${on ? "is-on" : ""}" data-id="${sys.id}">
      <span class="source-title-row"><strong>${sys.name}</strong><span class="source-chip">${locked ? "locked?" : sys.layer}</span></span>
      <span class="source-meta">${sys.cat} • Unlock: ${sys.unlock}</span>
      <span class="source-meta">${sys.note}</span>
      <span class="source-tags">${sys.tags.slice(0,4).map(t => `<span>${t}</span>`).join("")}</span>
    </button>`;
  }).join("");
  $$(".source-item").forEach(item => item.addEventListener("click", () => {
    const id = item.dataset.id;
    if (enabledSystems.has(id)) enabledSystems.delete(id); else enabledSystems.add(id);
    renderSourceSystems();
    updateShareString(false);
  }));
  const active = enabledSystems.size;
  $("#source-system-count").textContent = `${active}/${sourceSystems.length} active`;
  $("#source-system-summary").innerHTML = `${active} source systems are active. Chapter ${chapter} gating is checked before recommendations. HP/survival sources are stored but down-ranked for DPS-first scoring.`;
}

function isChapterLocked(sys, chapter) {
  const u = sys.unlock.toLowerCase();
  if (u.includes("chapter 9")) return chapter < 9;
  if (u.includes("chapter 8")) return chapter < 8;
  if (u.includes("chapter 6")) return chapter < 6;
  if (u.includes("chapter 3")) return chapter < 3;
  if (u.includes("chapter 1")) return chapter < 1;
  return false;
}

function val(id) { return Number($("#" + id)?.value || 0); }
function slug(s) { return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""); }

function getResources() {
  const out = {};
  $$(".resource-input").forEach(input => out[input.id.replace("res-", "")] = Number(input.value || 0));
  return out;
}

function getState() {
  return {
    scenario: currentScenario,
    survivor: activeSurvivor,
    chapter: val("chapter"),
    atk: val("base-atk"),
    critRate: val("crit-rate"),
    critDmg: val("crit-dmg"),
    resources: getResources(),
    sourceSystems: [...enabledSystems],
    actives: $$(".active-select").map(x => x.value),
    passives: $$(".passive-select").map(x => x.value),
    gear: $$(".gear-card").map(card => ({ slot: card.dataset.slot, item: card.querySelector(".gear-selector").value, forge: Number(card.querySelector(".forge-stars-input").value || 0), grade: [...card.classList].find(c => c.startsWith("grade-")) })),
    tech: $$(".tech-cell").map(cell => ({ name: cell.querySelector("select").value, level: Number(cell.querySelector("input").value || 0) })),
    collectibles: $$(".collectible-cell").map(cell => ({ name: cell.querySelector("label").textContent, tier: Number(cell.querySelector("input").value || 0) })),
    exclusions: $$(".exclude-toggle:checked").map(x => x.dataset.id)
  };
}

function updateShareString(writeUrl = false) {
  const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(getState()))));
  const url = `${location.origin}${location.pathname}?matrix=${encoded}`;
  $("#share-string-output").value = url;
  if (writeUrl) history.replaceState(null, "", url);
  return url;
}

async function copyConfiguration() {
  const url = updateShareString(true);
  try {
    await navigator.clipboard.writeText(url);
    $("#copy-status").textContent = "Copied ✓";
  } catch {
    $("#copy-status").textContent = "Copy failed";
  }
  setTimeout(() => $("#copy-status").textContent = "", 1200);
}

function generateTechChecklist() {
  const state = getState();
  const tech = state.tech.sort((a,b) => a.level - b.level);
  const twinbornOn = state.sourceSystems.some(id => id.startsWith("tb_"));
  const chips = state.resources["resonance-chips"] || 0;
  const rows = tech.map(t => `<li>Push ${t.name} from level ${t.level}; ${twinbornOn ? "compare Twinborn resonance before normal resonance" : "normal Drone resonance is still valuable before Twinborn"}.</li>`).join("");
  $("#tech-checklist").classList.add("is-open");
  $("#tech-checklist").innerHTML = `<ol>${rows}<li>Available resonance chips: ${chips}. Spend by DPS gain per chip, not evenly.</li></ol>`;
}

function countEvos() {
  let ready = 0;
  for (let i = 0; i < 6; i++) {
    const active = $(`.active-select[data-index='${i}']`).value;
    const passive = $(`.passive-select[data-index='${i}']`).value;
    if (evolutionPairs[active] === passive) ready++;
  }
  return ready;
}

function activeSystemsByCat(cat) {
  return sourceSystems.filter(s => enabledSystems.has(s.id) && s.cat === cat);
}

function runOptimization() {
  const state = getState();
  const survivor = survivors.find(s => s[0] === state.survivor) || survivors[0];
  const scenarioMulti = currentScenario === "enders" ? 1.18 : currentScenario === "trials" ? 1.10 : 1.05;
  const critMulti = 1 + (state.critRate / 100) * Math.max(1, state.critDmg / 100);
  const evoCount = countEvos();
  const forgeSum = state.gear.reduce((a,g) => a + g.forge, 0);
  const techLevels = state.tech.reduce((a,t) => a + t.level, 0);
  const collectibleTiers = state.collectibles.reduce((a,c) => a + c.tier, 0);
  const excludedPenalty = state.exclusions.length * 1.8;
  const sourceCoverageMulti = 1 + Math.min(.22, state.sourceSystems.length / sourceSystems.length * .22);
  const breakpointBonus = state.critRate >= 150 ? .20 : state.critRate >= 130 ? .12 : state.critRate >= 100 ? .06 : 0;
  const resourcePressure = scarcePressure(state.resources);
  const base = Math.max(1, state.atk || 10000);
  const score = base * survivor[2] * scenarioMulti * critMulti * sourceCoverageMulti * (1 + breakpointBonus + evoCount * .045 + forgeSum * .025 + techLevels * .012 + collectibleTiers * .008 - excludedPenalty/100);

  const nextMoves = buildNextMoveList(state, resourcePressure);
  const builds = [
    { title: "Full Source-Pack DPS Path", cls: "best", multi: 1.00, logic: `${state.sourceSystems.length}/${sourceSystems.length} systems active`, moves: nextMoves.damage },
    { title: "Resource-Efficient Progression", cls: "", multi: .93, logic: "DPS per scarce cost", moves: nextMoves.efficient },
    { title: "Coverage / Missing-Data Warnings", cls: "", multi: .82, logic: "Source gates + confidence", moves: nextMoves.coverage }
  ];
  $("#results-dashboard").hidden = false;
  $("#results-grid").innerHTML = builds.map((b,i) => solutionCard(b, score, i)).join("");
  $("#results-dashboard").scrollIntoView({ behavior: "smooth", block: "start" });
}

function scarcePressure(resources) {
  return {
    relic: Number(resources["relic-cores"] || 0),
    awaken: Number(resources["awakening-cores"] || 0),
    pet: Number(resources["pet-crystals"] || 0),
    chips: Number(resources["resonance-chips"] || 0),
    gems: Number(resources.gems || 0),
    xeno: Number(resources["xeno-cores"] || 0)
  };
}

function buildNextMoveList(state, res) {
  const damage = [];
  const efficient = [];
  const coverage = [];
  if (enabledSystems.has("crit")) damage.push(`Crit model active: check 100/130/150% breakpoints before ranking SS Belt or Necklace.`);
  if (enabledSystems.has("ss_gear")) damage.push(`SS gear active: prioritize weapon, belt, gloves, and boots before early SS armor.`);
  if (enabledSystems.has("tb_drone_forcefield")) damage.push(`Twinborn Drone + Forcefield active: treat as top tech path if pair is unlocked.`);
  if (enabledSystems.has("pets")) damage.push(`Pet model active: compare Murica direct DPS vs Rex/Croaky long-term owner buffs.`);
  if (enabledSystems.has("mount_basics")) damage.push(`Mounts active: include equipped mount plus synced inactive component board stats.`);

  if (res.relic > 0) efficient.push(`Relic cores available: spend by mode-specific DPS per core, not by random SS rarity.`); else efficient.push(`No relic cores entered: do not recommend SS forge jumps that require cores.`);
  if (res.chips > 0) efficient.push(`Resonance chips available: spend by slot-specific DPS gain per chip.`);
  if (res.pet > 0) efficient.push(`Pet crystals available: compare R5 Rex/Croaky path versus balanced Y5 account ATK path.`);
  if (res.gems > 0) efficient.push(`Gems entered: run item-value/event-value checks before spending.`);
  if (res.xeno > 0) efficient.push(`Xeno cores entered: only value if the required gear gates are met.`);

  const locked = sourceSystems.filter(s => enabledSystems.has(s.id) && isChapterLocked(s, state.chapter));
  if (locked.length) coverage.push(`${locked.length} active source systems may be locked at chapter ${state.chapter}: ${locked.slice(0,4).map(s=>s.name).join(", ")}.`);
  const off = sourceSystems.length - state.sourceSystems.length;
  coverage.push(`${state.sourceSystems.length}/${sourceSystems.length} source systems active; ${off} disabled.`);
  coverage.push(`HP, healing, damage reduction, revival, and pure survival are stored but down-ranked unless you add survival mode.`);
  coverage.push(`Missing exact formulas should show as lower confidence, not fake certainty.`);

  return { damage: padMoves(damage), efficient: padMoves(efficient), coverage: padMoves(coverage) };
}

function padMoves(list) {
  const fallback = ["No high-confidence move generated yet; enable more source systems or enter resources.", "Check source confidence before spending rare materials.", "Prioritize DPS, rare bottlenecks, and unlock gates."];
  return [...list, ...fallback].slice(0, 6);
}

function solutionCard(build, score, index) {
  const scaled = Math.round(score * build.multi);
  return `<article class="solution-card ${build.cls}">
    <h4>${build.title}</h4>
    <div class="metric-row"><span>Estimated score</span><strong>${scaled.toLocaleString("en-US")}</strong></div>
    <div class="metric-row"><span>Logic</span><strong>${build.logic}</strong></div>
    <div class="solution-list">${build.moves.map((move, i) => `<div class="solution-item"><strong>${i + 1}. ${move.split(":")[0]}</strong><span class="metric-penalty-label">${move.includes(":") ? move.slice(move.indexOf(":") + 1).trim() : move}</span></div>`).join("")}</div>
  </article>`;
}

init();
