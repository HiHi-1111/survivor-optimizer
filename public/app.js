const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

let currentScenario = "clan";
let activeSurvivor = "Master Yang";

const survivors = [
  ["Master Yang", "Y", 1.18, "Damage scaling"],
  ["Metallia", "M", 1.14, "Late-game scaling"],
  ["King", "K", 1.11, "Crit focused"],
  ["Common", "C", 1.00, "Baseline"],
  ["Worm", "W", 1.06, "Support damage"],
  ["Catnips", "N", 1.04, "Utility"],
  ["TMNT", "T", 1.10, "Event survivor"],
  ["Other", "O", 1.02, "Manual entry"]
];

const activeSkills = ["Kunai", "Void Power", "Lightchaser", "Drone", "RPG", "Molotov", "Soccer Ball", "Drill", "Lightning", "Guardian", "Forcefield", "Brick", "Durian"];
const passiveItems = ["Koga Scroll", "Energy Cube", "HE Fuel", "Ammo Thruster", "Hi-Power Bullet", "Exo-Bracer", "Fitness Guide", "Sports Shoes", "Ronin Oyoroi", "Oil Bond", "Magnet", "None"];
const evolutionPairs = { "Kunai":"Koga Scroll", "Void Power":"Exo-Bracer", "Lightchaser":"Ronin Oyoroi", "Drone":"Hi-Power Bullet", "RPG":"HE Fuel", "Molotov":"Oil Bond", "Soccer Ball":"Sports Shoes", "Drill":"Ammo Thruster", "Lightning":"Energy Cube", "Guardian":"Exo-Bracer", "Forcefield":"Energy Cube", "Brick":"Fitness Guide", "Durian":"HE Fuel" };
const gearSlots = ["Weapon", "Necklace", "Gloves", "Armor", "Belt", "Boots"];
const gearOptions = ["SS Starforged Havoc", "Kunai", "Void Power", "Lightchaser", "SoD", "Eternal", "Void", "Chaos", "Army", "Other"];
const techOptions = ["Drone", "RPG", "Molotov", "Soccer Ball", "Drill", "Lightning", "Guardian", "Forcefield", "Brick", "Durian", "Laser", "Boomerang"];
const collectibleNames = ["Crit Set", "ATK Set", "Drone Set", "Pet Set", "Boss Set", "Chapter Set", "Relic Set", "Tech Set", "Event Set"];
const exclusions = ["Void Armor", "Chaos Belt", "Manual Builds", "HP-Only Picks", "Gem Spending", "Unowned SS Gear", "Pet Copies", "Collectible Chasing"];

function init() {
  renderSurvivors();
  renderSkills();
  renderEquipment();
  renderTech();
  renderCollectibles();
  renderExclusions();
  bindGlobalEvents();
  updatePairing();
  updateShareString(false);
}

function bindGlobalEvents() {
  $$(".mode-btn").forEach(btn => btn.addEventListener("click", () => {
    currentScenario = btn.dataset.scenario;
    $$(".mode-btn").forEach(b => b.classList.remove("is-active"));
    btn.classList.add("is-active");
    updateShareString(false);
  }));
  ["base-atk", "crit-rate", "crit-dmg", "core-shards-pool", "core-stellar-pool"].forEach(id => $("#" + id).addEventListener("input", () => updateShareString(false)));
  $("#btn-copy-string").addEventListener("click", copyConfiguration);
  $("#btn-tech-order").addEventListener("click", generateTechChecklist);
  $("#tooltip-guide-trigger").addEventListener("click", () => { const tip = $("#collectible-tooltip"); tip.hidden = !tip.hidden; });
  $("#btn-execute-optimization").addEventListener("click", runOptimization);
}

function renderSurvivors() {
  $("#survivor-grid").innerHTML = survivors.map(s => `
    <button type="button" class="survivor-cell ${s[0] === activeSurvivor ? "is-active" : ""}" data-survivor="${s[0]}">
      <span class="survivor-icon">${s[1]}</span><span class="survivor-name">${s[0]}</span>
    </button>`).join("");
  $$(".survivor-cell").forEach(cell => cell.addEventListener("click", () => {
    activeSurvivor = cell.dataset.survivor;
    $("#selected-survivor-label").textContent = activeSurvivor;
    renderSurvivors();
    updateShareString(false);
  }));
}

function selectHtml(list, className, index) {
  return `<select class="${className}" data-index="${index}">${list.map(x => `<option>${x}</option>`).join("")}</select>`;
}

function renderSkills() {
  $("#active-grid").innerHTML = Array.from({length:6}, (_, i) => `<div class="skill-slot" data-index="${i}"><span class="slot-label">Active ${i+1}</span>${selectHtml(activeSkills, "active-select", i)}</div>`).join("");
  $("#passive-grid").innerHTML = Array.from({length:6}, (_, i) => `<div class="skill-slot" data-index="${i}"><span class="slot-label">Passive ${i+1}</span>${selectHtml(passiveItems, "passive-select", i)}</div>`).join("");
  $$(".active-select,.passive-select").forEach(sel => sel.addEventListener("change", () => { updatePairing(); updateShareString(false); }));
}

function updatePairing() {
  let ready = 0;
  $$("#active-grid .skill-slot,#passive-grid .skill-slot").forEach(s => s.classList.remove("is-paired"));
  for (let i = 0; i < 6; i++) {
    const active = $(`.active-select[data-index='${i}']`).value;
    const passive = $(`.passive-select[data-index='${i}']`).value;
    if (evolutionPairs[active] === passive) {
      ready++;
      $(`#active-grid .skill-slot[data-index='${i}']`).classList.add("is-paired");
      $(`#passive-grid .skill-slot[data-index='${i}']`).classList.add("is-paired");
    }
  }
  $("#evo-count").textContent = `${ready} evolutions ready`;
}

function renderEquipment() {
  $("#equipment-grid").innerHTML = gearSlots.map(slot => `
    <article class="gear-card grade-epic" data-slot="${slot}">
      <div class="gear-top"><span class="gear-slot-name">${slot}</span><label class="forge-wrap">★ <input type="number" class="forge-stars-input" min="0" max="5" value="0"></label></div>
      <select class="gear-selector">${gearOptions.map(g => `<option>${g} ${slot}</option>`).join("")}</select>
      <div class="grade-group">
        <button type="button" class="grade-btn is-active" data-grade="epic">Epic</button>
        <button type="button" class="grade-btn" data-grade="legendary">Legendary</button>
        <button type="button" class="grade-btn" data-grade="cosmic">Cosmic Cast</button>
      </div>
      <input type="number" class="input-designs" placeholder="Available Designs" min="0">
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
  $("#tech-grid").innerHTML = Array.from({length:6}, (_, i) => `<div class="tech-cell"><select>${techOptions.map(t => `<option>${t}</option>`).join("")}</select><input class="level-input" type="number" min="0" value="${i < 2 ? 3 : 0}" placeholder="Level"></div>`).join("");
}

function renderCollectibles() {
  $("#collectibles-grid").innerHTML = collectibleNames.map((name, i) => `<div class="collectible-cell"><label>${name}</label><input class="tier-input" type="number" min="0" value="${i < 2 ? 2 : 0}"></div>`).join("");
}

function renderExclusions() {
  $("#exclusion-toggles").innerHTML = exclusions.map(name => `<label class="toggle-pill"><input type="checkbox" class="exclude-toggle" data-gear-id="${slug(name)}"> ${name}</label>`).join("");
  $$(".exclude-toggle").forEach(el => el.addEventListener("change", () => updateShareString(false)));
}

function getState() {
  return {
    scenario: currentScenario,
    survivor: activeSurvivor,
    atk: val("base-atk"), critRate: val("crit-rate"), critDmg: val("crit-dmg"),
    shards: val("core-shards-pool"), stellar: val("core-stellar-pool"),
    actives: $$(".active-select").map(x => x.value), passives: $$(".passive-select").map(x => x.value),
    gear: $$(".gear-card").map(card => ({ slot: card.dataset.slot, item: card.querySelector(".gear-selector").value, forge: card.querySelector(".forge-stars-input").value, grade: [...card.classList].find(c => c.startsWith("grade-")) })),
    tech: $$(".tech-cell").map(cell => ({ name: cell.querySelector("select").value, level: Number(cell.querySelector("input").value || 0) })),
    collectibles: $$(".collectible-cell").map(cell => ({ name: cell.querySelector("label").textContent, tier: Number(cell.querySelector("input").value || 0) })),
    exclusions: $$(".exclude-toggle:checked").map(x => x.dataset.gearId)
  };
}

function val(id) { return Number($("#" + id).value || 0); }
function slug(s) { return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""); }

function updateShareString(writeUrl = false) {
  const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(getState()))));
  const url = `${location.origin}${location.pathname}?matrix=${encoded}`;
  $("#share-string-output").value = url;
  if (writeUrl) history.replaceState(null, "", url);
  return url;
}

async function copyConfiguration() {
  const url = updateShareString(true);
  try { await navigator.clipboard.writeText(url); $("#copy-status").textContent = "Copied ✓"; }
  catch { $("#copy-status").textContent = "Copy failed"; }
  setTimeout(() => $("#copy-status").textContent = "", 1200);
}

function generateTechChecklist() {
  const tech = getState().tech.sort((a,b) => a.level - b.level);
  $("#tech-checklist").classList.add("is-open");
  $("#tech-checklist").innerHTML = `<ol>${tech.map(t => `<li>Push ${t.name} from level ${t.level} toward next breakpoint.</li>`).join("")}</ol>`;
}

function runOptimization() {
  const state = getState();
  const survivor = survivors.find(s => s[0] === state.survivor) || survivors[0];
  const scenarioMulti = currentScenario === "enders" ? 1.18 : currentScenario === "trials" ? 1.10 : 1.05;
  const critMulti = 1 + (state.critRate / 100) * (state.critDmg / 100);
  const evoCount = countEvos();
  const forgeSum = state.gear.reduce((a,g) => a + Number(g.forge || 0), 0);
  const techLevels = state.tech.reduce((a,t) => a + t.level, 0);
  const collectibleTiers = state.collectibles.reduce((a,c) => a + c.tier, 0);
  const excludedPenalty = state.exclusions.length * 1.8;
  const base = Math.max(1, state.atk || 10000);
  const score = base * survivor[2] * scenarioMulti * critMulti * (1 + evoCount * .045 + forgeSum * .025 + techLevels * .012 + collectibleTiers * .008 - excludedPenalty/100);
  const builds = [
    { title: "Max Damage Multiplier Pathway", cls: "best", multi: 1.00, logic: "pure damage path", gear: state.gear.map(g => g.item) },
    { title: "Balanced Progression Pathway", cls: "", multi: .93, logic: "damage with safer resource use", gear: state.gear.map(g => g.item.replace("SS ", "")) },
    { title: "Defense / Survival Pathway", cls: "", multi: .82, logic: "safer setup with damage drop", gear: state.gear.map((g,i) => i === 3 ? "Defensive Armor Option" : g.item) }
  ];
  $("#results-dashboard").hidden = false;
  $("#results-grid").innerHTML = builds.map((b,i) => solutionCard(b, score, i)).join("");
  $("#results-dashboard").scrollIntoView({ behavior: "smooth", block: "start" });
}

function countEvos() {
  let ready = 0;
  for (let i = 0; i < 6; i++) if (evolutionPairs[$(`.active-select[data-index='${i}']`).value] === $(`.passive-select[data-index='${i}']`).value) ready++;
  return ready;
}

function solutionCard(build, score, index) {
  const scaled = Math.round(score * build.multi);
  return `<article class="solution-card ${build.cls}"><h4>${build.title}</h4><div class="metric-row"><span>Estimated score</span><strong>${scaled.toLocaleString("en-US")}</strong></div><div class="metric-row"><span>Logic</span><strong>${build.logic}</strong></div><div class="solution-list">${gearSlots.map((slot,i) => `<div class="solution-item"><strong>${slot}: ${build.gear[i]}</strong><span class="metric-penalty-label">Removing this selection decreases optimization scaling by <strong class="text-drop">-${(52.04 - index*11 - i*2.4).toFixed(2)}%</strong></span></div>`).join("")}</div></article>`;
}

init();
