const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const sampleProfile = {
  chapter: 126,
  gems: 51000,
  survivor: "Master Yang",
  weapon: "SS Starforged Havoc",
  pet: "Eagle",
  cores: 2,
  techFocus: "Drone / Twinborn",
  style: "AFK / Low Effort",
};

const resultTemplates = {
  afk: [
    {
      title: "Keep upgrades focused on auto-clear damage",
      reason: "Your selected style favors low-effort clears, so wide damage and passive value beat manual-only burst upgrades.",
      cost: "Varies",
      confidence: "High confidence",
    },
    {
      title: "Compare pet level and tech progress before gear spending",
      reason: "Pet and drone/twinborn gains can beat gear if your next SS forge step is expensive.",
      cost: "Cookies / tech",
      confidence: "Medium confidence",
    },
    {
      title: "Save gems unless the event has premium upgrade value",
      reason: "Gems should be protected until event rewards clearly improve relic cores, S gear, tech, or awakenings.",
      cost: "0 gems now",
      confidence: "High confidence",
    },
  ],
  boss: [
    {
      title: "Prioritize boss damage multipliers first",
      reason: "Enders Echo and boss setups care more about focused burst than lazy map clear.",
      cost: "Build swap",
      confidence: "Medium confidence",
    },
    {
      title: "Check weapon and glove synergy before spending cores",
      reason: "Core spending is hard to undo, so the optimizer should compare SS path gains before committing.",
      cost: "Relic cores",
      confidence: "High confidence",
    },
    {
      title: "Use events only when they move a blocked DPS system",
      reason: "Boss-focused accounts should spend on upgrades that directly improve damage windows.",
      cost: "Gems / keys",
      confidence: "Medium confidence",
    },
  ],
  balanced: [
    {
      title: "Build the cheapest high-impact DPS upgrade first",
      reason: "Balanced mode should choose the next efficient gain across gear, pets, tech, and collectibles.",
      cost: "Lowest useful cost",
      confidence: "Medium confidence",
    },
    {
      title: "Do not chase HP unless it unlocks progress",
      reason: "HP is useful, but your planner should not rank it above damage unless you are actually blocked.",
      cost: "Avoid waste",
      confidence: "High confidence",
    },
    {
      title: "Hold scarce materials until the comparison is connected",
      reason: "Relic cores, awakening crystals, and high-value shards need exact math before spending.",
      cost: "Wait",
      confidence: "High confidence",
    },
  ],
};

function numberWithCommas(value) {
  const number = Number(value || 0);
  return number.toLocaleString("en-US");
}

function getCheckedGoals() {
  return $$(".check-list input:checked").map((input) => input.value);
}

function updateSnapshot() {
  const chapter = $("#chapter")?.value || sampleProfile.chapter;
  const gems = $("#gems")?.value || sampleProfile.gems;
  const pet = $("#pet")?.value || sampleProfile.pet;
  const cores = $("#cores")?.value || sampleProfile.cores;
  const goals = getCheckedGoals();
  const goal = goals[0] || "Balanced";
  const mode = goals.includes("Steamroll") ? "Steamroll" : goals.includes("Enders Echo") ? "Enders Echo" : "Normal";

  $("#snap-chapter").textContent = chapter;
  $("#snap-gems").textContent = numberWithCommas(gems);
  $("#snap-pet").textContent = pet;
  $("#snap-cores").textContent = cores;
  $("#snap-goal").textContent = goal;
  $("#snap-mode").textContent = mode;
}

function pickResults() {
  const style = $("#style")?.value || "Balanced";
  const goals = getCheckedGoals();

  if (goals.includes("Boss Damage") || goals.includes("Enders Echo") || style === "Boss Damage") {
    return resultTemplates.boss;
  }

  if (style === "AFK / Low Effort" || goals.includes("AFK / Low Effort") || goals.includes("Steamroll")) {
    return resultTemplates.afk;
  }

  return resultTemplates.balanced;
}

function renderResults() {
  updateSnapshot();

  const list = $("#result-list");
  const results = pickResults();

  list.innerHTML = results
    .map((item, index) => `
      <article class="result-row ${index === 0 ? "top-result" : ""}">
        <span class="rank">${index + 1}</span>
        <div>
          <h3>${item.title}</h3>
          <p>${item.reason}</p>
        </div>
        <div class="result-meta">
          <span>Cost: ${item.cost}</span>
          <strong>${item.confidence}</strong>
        </div>
      </article>
    `)
    .join("");
}

function fillSample() {
  $("#chapter").value = sampleProfile.chapter;
  $("#gems").value = sampleProfile.gems;
  $("#survivor").value = sampleProfile.survivor;
  $("#weapon").value = sampleProfile.weapon;
  $("#pet").value = sampleProfile.pet;
  $("#cores").value = sampleProfile.cores;
  $("#tech-focus").value = sampleProfile.techFocus;
  $("#style").value = sampleProfile.style;

  $$(".check-list input").forEach((input) => {
    input.checked = ["Max DPS", "AFK / Low Effort", "Steamroll", "Save Gems"].includes(input.value);
  });

  renderResults();
}

function setActiveTool(clickedCard) {
  $$(".tool-card").forEach((card) => card.classList.remove("active"));
  clickedCard.classList.add("active");

  const toolName = clickedCard.querySelector("h3")?.textContent || "Tool";
  const firstResult = $("#result-list .result-row h3");
  if (firstResult) {
    firstResult.textContent = `${toolName}: starter view selected`;
  }

  $("#optimizer").scrollIntoView({ behavior: "smooth", block: "start" });
}

$$("input, select").forEach((element) => {
  element.addEventListener("input", updateSnapshot);
  element.addEventListener("change", updateSnapshot);
});

$$(".check-list input").forEach((input) => input.addEventListener("change", updateSnapshot));

$("#generate-plan")?.addEventListener("click", () => {
  renderResults();
  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
});

$("[data-fill-sample]")?.addEventListener("click", fillSample);
$("[data-scroll-results]")?.addEventListener("click", () => $("#results").scrollIntoView({ behavior: "smooth" }));

$$(".tool-card").forEach((card) => {
  card.addEventListener("click", (event) => {
    if (event.target.tagName.toLowerCase() === "button" || event.currentTarget === card) {
      setActiveTool(card);
    }
  });
});

updateSnapshot();
