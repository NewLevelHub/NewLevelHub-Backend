#!/usr/bin/env node
/**
 * Импорт и обновление тикетов в Linear из JSON.
 *
 * Конфиг: scripts/linear-config.local.js (см. linear-config.local.example.js)
 *
 * Формат каждого тикета:
 *   - title — обязателен для создания; для обновления по identifier можно опустить (заголовок не меняется)
 *   - description (опционально)
 *   - block (опционально) — Issue Label команды (блок MVP)
 *   - labels (опционально) — дополнительные лейблы
 *   - acceptanceCriteria или ac (опционально) — AC с чеклистом - [ ]
 *   - identifier (опционально) — например "DEV-51": не создавать, а обновить существующий тикет
 *   - area (опционально) — зона: auth, companies, bookings, crm, … (попадает в блок Planning)
 *   - parallel (опционально) — true/false или "yes"/"no" — можно ли брать параллельно с другими
 *   - dependsOn (опционально) — массив идентификаторов ["DEV-1", "DEV-2"]; в Linear создаются связи blocks
 *
 * В linear-config.local.js (опционально):
 *   - AUTO_DEPENDENCY_LABELS — если true, тикетам с dependsOn ставится лейбл LABEL_DEPENDS_ON (по умолчанию
 *     «depends-on»), чтобы в доске/списке сразу было видно и можно было отфильтровать все «зависящие».
 *   - AUTO_BLOCKER_LABELS — если true, тикетам-блокерам (на кого ссылаются в dependsOn) добавляется
 *     LABEL_BLOCKS_OTHERS («blocks-others»), без снятия остальных лейблов.
 *
 * Запуск:
 *   node scripts/linear-import-tickets.js [path/to/tickets.json] [--dry-run]
 *
 * --dry-run — только вывести, что сделал бы (без запросов на запись, кроме чтения issue для update)
 */

const fs = require("fs");
const path = require("path");

const API_URL = "https://api.linear.app/graphql";
const defaultJsonPath = path.join(__dirname, "linear-tickets.json");

/** Простые цвета для новых лейблов (Linear принимает hex) */
const LABEL_COLORS = [
  "#4EA7FC",
  "#95A2B3",
  "#5E6AD2",
  "#F2C94C",
  "#EB5757",
  "#4CB782",
];

function labelColorForName(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return LABEL_COLORS[h % LABEL_COLORS.length];
}

function loadLocalConfig() {
  const p = path.join(__dirname, "linear-config.local.js");
  if (!fs.existsSync(p)) {
    return {};
  }
  try {
    delete require.cache[require.resolve(p)];
    return require(p);
  } catch (e) {
    console.error("Failed to load linear-config.local.js:", e.message);
    process.exit(1);
  }
}

const local = loadLocalConfig();

/** Автолейблы для зависимостей (удобный фильтр в Linear без открытия описания). */
const AUTO_DEPENDENCY_LABELS = local.AUTO_DEPENDENCY_LABELS === true;
const AUTO_BLOCKER_LABELS = local.AUTO_BLOCKER_LABELS === true;
const LABEL_DEPENDS_ON = local.LABEL_DEPENDS_ON || "depends-on";
const LABEL_BLOCKS_OTHERS = local.LABEL_BLOCKS_OTHERS || "blocks-others";

const apiKey =
  process.env.LINEAR_API_KEY || local.LINEAR_API_KEY || "";
const teamKey = process.env.TEAM_KEY || local.TEAM_KEY || "DEV";
const teamIdEnv =
  (process.env.LINEAR_TEAM_ID || local.LINEAR_TEAM_ID || "").trim() || null;

async function gql(query, variables = {}) {
  const res = await fetch(API_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: apiKey,
    },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors) {
    throw new Error(JSON.stringify(json.errors, null, 2));
  }
  return json.data;
}

const AC_SECTION_HEADER = "## Acceptance Criteria (AC)";
const PLANNING_SECTION_HEADER = "## Planning";

/**
 * Блок планирования для двух разрабов: зона, параллельность, зависимости (и дублирует dependsOn текстом).
 */
function formatPlanningSection(area, parallel, dependsOn) {
  const lines = [];
  if (typeof area === "string" && area.trim()) {
    lines.push(`- **Area:** ${area.trim()}`);
  }
  if (parallel !== undefined && parallel !== null && parallel !== "") {
    const p =
      parallel === true ||
      parallel === "yes" ||
      parallel === "true" ||
      parallel === 1;
    lines.push(`- **Parallel:** ${p ? "yes" : "no"}`);
  }
  if (Array.isArray(dependsOn) && dependsOn.length) {
    const ids = dependsOn
      .filter((x) => typeof x === "string" && x.trim())
      .map((x) => x.trim());
    if (ids.length) {
      lines.push(`- **Depends on:** ${ids.join(", ")}`);
    }
  }
  if (!lines.length) return "";
  return [PLANNING_SECTION_HEADER, "", ...lines].join("\n");
}

/**
 * Полное описание: base + Planning + AC.
 */
function buildFullDescription(base, acceptanceCriteria, planning) {
  const parts = [];
  const baseText = typeof base === "string" ? base.trim() : "";
  if (baseText) parts.push(baseText);

  const planningText = formatPlanningSection(
    planning?.area,
    planning?.parallel,
    planning?.dependsOn
  );
  if (planningText) parts.push(planningText);

  const raw = Array.isArray(acceptanceCriteria) ? acceptanceCriteria : [];
  const lines = raw
    .filter((x) => typeof x === "string" && x.trim())
    .map((x) => `- [ ] ${x.trim()}`);
  if (lines.length) {
    parts.push([AC_SECTION_HEADER, "", ...lines].join("\n"));
  }

  return parts.join("\n\n");
}

function pickStateId(states, preferredName) {
  const nodes = states?.nodes || states || [];
  if (preferredName && String(preferredName).trim()) {
    const wanted = String(preferredName).trim().toLowerCase();
    const exact = nodes.find((s) => String(s.name).toLowerCase() === wanted);
    if (exact?.id) return exact.id;
  }

  const preferred = ["backlog", "triage", "todo"];
  return (
    nodes.find((s) => preferred.includes(String(s.name).toLowerCase()))?.id ??
    null
  );
}

async function resolveCycleId(teamId, cycleName) {
  if (!cycleName || !String(cycleName).trim()) return null;
  const wantedRaw = String(cycleName).trim();
  const wanted = wantedRaw.toLowerCase();

  const data = await gql(
    `query($teamId: ID!) {
      cycles(
        filter: { team: { id: { eq: $teamId } } }
        first: 100
      ) {
        nodes {
          id
          name
          number
        }
      }
    }`,
    { teamId }
  );

  const nodes = data.cycles?.nodes || [];
  const exactByName = nodes.find(
    (c) => String(c.name || "").toLowerCase() === wanted
  );
  if (exactByName?.id) return exactByName.id;

  const m = wanted.match(/^(?:cycle\s*)?(\d+)$/i);
  if (m) {
    const n = Number(m[1]);
    const byNumber = nodes.find((c) => Number(c.number) === n);
    if (byNumber?.id) return byNumber.id;
  }

  throw new Error(`Cycle not found in team: ${wantedRaw}`);
}

function loadTickets(filePath) {
  const abs = path.resolve(filePath);
  if (!fs.existsSync(abs)) {
    console.error(`File not found: ${abs}`);
    console.error(
      `Copy example: cp scripts/linear-tickets.example.json scripts/linear-tickets.json`
    );
    process.exit(1);
  }
  const raw = fs.readFileSync(abs, "utf-8");
  const data = JSON.parse(raw);
  if (!Array.isArray(data)) {
    throw new Error("JSON root must be an array");
  }
  return data.map((item, i) => {
    const identifier =
      typeof item.identifier === "string" && item.identifier.trim()
        ? item.identifier.trim().toUpperCase()
        : null;

    const hasTitle =
      typeof item.title === "string" && item.title.trim().length > 0;
    if (!hasTitle && !identifier) {
      throw new Error(
        `Invalid ticket at index ${i}: title is required (or use identifier for update-only)`
      );
    }

    const block =
      typeof item.block === "string" && item.block.trim()
        ? item.block.trim()
        : null;
    let extraLabels = [];
    if (Array.isArray(item.labels)) {
      extraLabels = item.labels.filter(
        (x) => typeof x === "string" && x.trim()
      );
    }

    let acceptanceCriteria = [];
    if (Array.isArray(item.acceptanceCriteria)) {
      acceptanceCriteria = item.acceptanceCriteria;
    } else if (Array.isArray(item.ac)) {
      acceptanceCriteria = item.ac;
    }

    let dependsOn = [];
    if (Array.isArray(item.dependsOn)) {
      dependsOn = item.dependsOn
        .filter((x) => typeof x === "string" && x.trim())
        .map((x) => x.trim().toUpperCase());
    }

    const baseDescription =
      typeof item.description === "string" ? item.description : "";

    const area =
      typeof item.area === "string" && item.area.trim()
        ? item.area.trim()
        : null;
    const parallel = item.parallel;

    const description = buildFullDescription(baseDescription, acceptanceCriteria, {
      area,
      parallel,
      dependsOn,
    });

    return {
      ...(hasTitle ? { title: item.title.trim() } : {}),
      description,
      block,
      extraLabels: extraLabels.map((s) => s.trim()),
      identifier,
      dependsOn,
      area,
      parallel,
    };
  });
}

/** TEAM-NUMBER → { teamKey, number } */
function parseIssueIdentifier(identifier) {
  const m = String(identifier)
    .trim()
    .toUpperCase()
    .match(/^([A-Z][A-Z0-9]*)-(\d+)$/);
  if (!m) return null;
  return { teamKey: m[1], number: parseInt(m[2], 10) };
}

async function fetchIssueByIdentifier(identifier) {
  const parsed = parseIssueIdentifier(identifier);
  if (!parsed) {
    throw new Error(
      `Invalid identifier "${identifier}". Expected format like DEV-51.`
    );
  }
  const data = await gql(
    `query($teamKey: String!, $number: Float!) {
      issues(
        filter: {
          team: { key: { eq: $teamKey } }
          number: { eq: $number }
        }
        first: 1
      ) {
        nodes {
          id
          identifier
          title
          description
          labels { nodes { id } }
        }
      }
    }`,
    { teamKey: parsed.teamKey, number: parsed.number }
  );
  const issue = data.issues?.nodes?.[0];
  if (!issue) {
    throw new Error(`Issue not found: ${identifier} (team ${parsed.teamKey})`);
  }
  return issue;
}

/**
 * Текущий тикет зависит от deps: для каждого dep создаём dep —blocks→ current.
 * В Linear: type blocks = issue блокирует relatedIssue (зависимый не закрыть раньше).
 */
async function createBlockingRelation(blockerId, blockedId) {
  const data = await gql(
    `mutation($input: IssueRelationCreateInput!) {
      issueRelationCreate(input: $input) {
        success
        issueRelation { id type }
      }
    }`,
    {
      input: {
        issueId: blockerId,
        relatedIssueId: blockedId,
        type: "blocks",
      },
    }
  );
  if (!data.issueRelationCreate?.success) {
    throw new Error(
      `issueRelationCreate failed: ${JSON.stringify(data.issueRelationCreate)}`
    );
  }
}

async function resolveTeam() {
  if (teamIdEnv) {
    const data = await gql(
      `query($id: String!) {
        team(id: $id) {
          id
          key
          name
          states {
            nodes { id name type }
          }
        }
      }`,
      { id: teamIdEnv }
    );
    if (!data.team) {
      throw new Error(`Team not found for LINEAR_TEAM_ID=${teamIdEnv}`);
    }
    return data.team;
  }

  const data = await gql(
    `query($key: String!) {
      teams(filter: { key: { eq: $key } }) {
        nodes { id key name states { nodes { id name type } } }
      }
    }`,
    { key: teamKey }
  );
  const team = data.teams.nodes[0];
  if (!team) {
    throw new Error(`Team with key "${teamKey}" not found`);
  }
  return team;
}

/** Кэш «teamId::labelName» → label id (после find/create) */
const labelIdCache = new Map();

/** Один раз загружаем лейблы команды, потом дополняем после create */
let teamLabelMap = null;
let teamLabelMapForId = null;

async function fetchTeamLabelMap(teamId) {
  const data = await gql(
    `query {
      issueLabels(first: 250) {
        nodes { id name team { id } }
      }
    }`
  );

  const byName = new Map();
  for (const n of data.issueLabels?.nodes || []) {
    if (n.team?.id === teamId) {
      byName.set(n.name.toLowerCase(), n.id);
    }
  }
  return byName;
}

async function getOrLoadTeamLabelMap(teamId) {
  if (teamLabelMap && teamLabelMapForId === teamId) {
    return teamLabelMap;
  }
  teamLabelMap = await fetchTeamLabelMap(teamId);
  teamLabelMapForId = teamId;
  return teamLabelMap;
}

async function ensureLabel(teamId, name) {
  const cacheKey = `${teamId}::${name}`;
  if (labelIdCache.has(cacheKey)) {
    return labelIdCache.get(cacheKey);
  }

  const map = await getOrLoadTeamLabelMap(teamId);
  const existing = map.get(name.toLowerCase());
  if (existing) {
    labelIdCache.set(cacheKey, existing);
    return existing;
  }

  const createData = await gql(
    `mutation($input: IssueLabelCreateInput!) {
      issueLabelCreate(input: $input) {
        success
        issueLabel { id name }
      }
    }`,
    {
      input: {
        teamId,
        name,
        color: labelColorForName(name),
      },
    }
  );

  const created = createData.issueLabelCreate?.issueLabel;
  if (!createData.issueLabelCreate?.success || !created?.id) {
    console.warn(
      `  (warn) Could not create label "${name}" — skip label, only title/description used`
    );
    return null;
  }

  map.set(name.toLowerCase(), created.id);
  labelIdCache.set(cacheKey, created.id);
  return created.id;
}

async function resolveLabelIds(teamId, block, extraLabels, options = {}) {
  const { addDependsOn = false } = options;
  const names = [];
  if (block) names.push(block);
  for (const l of extraLabels) {
    if (!names.includes(l)) names.push(l);
  }
  if (addDependsOn && !names.includes(LABEL_DEPENDS_ON)) {
    names.push(LABEL_DEPENDS_ON);
  }
  const ids = [];
  for (const name of names) {
    const id = await ensureLabel(teamId, name);
    if (id) ids.push(id);
  }
  return ids;
}

/**
 * Добавить лейбл «блокирует других» на тикет-блокер (merge с уже существующими лейблами).
 */
async function addBlocksOthersLabelToBlocker(teamId, blockerIssue, dryRun) {
  if (!AUTO_BLOCKER_LABELS || dryRun) return;
  const lid = await ensureLabel(teamId, LABEL_BLOCKS_OTHERS);
  if (!lid) return;
  const existing = new Set(
    (blockerIssue.labels?.nodes || []).map((n) => n.id).filter(Boolean)
  );
  if (existing.has(lid)) return;
  existing.add(lid);
  const { issueUpdate } = await gql(
    `mutation($id: String!, $input: IssueUpdateInput!) {
      issueUpdate(id: $id, input: $input) {
        success
        issue { identifier }
      }
    }`,
    { id: blockerIssue.id, input: { labelIds: [...existing] } }
  );
  if (issueUpdate?.success) {
    console.log(
      `  → label "${LABEL_BLOCKS_OTHERS}" on blocker ${blockerIssue.identifier}`
    );
  }
}

function parseCliArgs() {
  const raw = process.argv.slice(2);
  const dryRun = raw.includes("--dry-run");
  const args = raw.filter((a) => a !== "--dry-run");

  let stateName =
    process.env.LINEAR_TARGET_STATE || local.LINEAR_TARGET_STATE || null;
  let cycleName =
    process.env.LINEAR_TARGET_CYCLE || local.LINEAR_TARGET_CYCLE || null;

  const positional = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--state" && args[i + 1]) {
      stateName = args[i + 1];
      i++;
      continue;
    }
    if (a.startsWith("--state=")) {
      stateName = a.slice("--state=".length);
      continue;
    }
    if (a === "--cycle" && args[i + 1]) {
      cycleName = args[i + 1];
      i++;
      continue;
    }
    if (a.startsWith("--cycle=")) {
      cycleName = a.slice("--cycle=".length);
      continue;
    }
    positional.push(a);
  }

  const jsonPath = positional[0] || defaultJsonPath;
  return { jsonPath, dryRun, stateName, cycleName };
}

(async () => {
  if (!apiKey || apiKey.includes("ВСТАВЬ")) {
    console.error(
      "Set LINEAR_API_KEY in scripts/linear-config.local.js (see linear-config.local.example.js) or env."
    );
    process.exit(1);
  }

  const { jsonPath, dryRun, stateName, cycleName } = parseCliArgs();
  let tickets;
  try {
    tickets = loadTickets(jsonPath);
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }

  if (tickets.length === 0) {
    console.error("No tickets in file (empty array).");
    process.exit(1);
  }

  let team;
  try {
    team = await resolveTeam();
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }

  const stateId = pickStateId(team.states, stateName);
  if (stateName && !stateId) {
    console.error(`State not found in team: ${stateName}`);
    process.exit(1);
  }

  let cycleId = null;
  try {
    cycleId = await resolveCycleId(team.id, cycleName);
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }

  console.log(`Team: ${team.name} (${team.key || team.id})`);
  console.log(`File: ${path.resolve(jsonPath)}`);
  console.log(`Tickets: ${tickets.length}`);
  console.log(
    `Target state: ${stateName || "(default backlog/triage/todo)"}`
  );
  if (cycleName) {
    console.log(`Target cycle: ${cycleName}`);
  }
  if (dryRun) {
    console.log("Mode: DRY-RUN (no writes to Linear)\n");
  } else {
    console.log("");
  }

  for (let i = 0; i < tickets.length; i++) {
    const ticket = tickets[i];
    const {
      title,
      description,
      block,
      extraLabels,
      identifier,
      dependsOn,
    } = ticket;

    let labelIds = [];
    try {
      labelIds = await resolveLabelIds(team.id, block, extraLabels, {
        addDependsOn: AUTO_DEPENDENCY_LABELS && dependsOn.length > 0,
      });
    } catch (e) {
      console.warn(
        `  (warn) Labels failed (${e.message}), issue without label updates`
      );
    }

    if (identifier) {
      let existing;
      try {
        existing = await fetchIssueByIdentifier(identifier);
      } catch (e) {
        console.error(`[${identifier}] ${e.message}`);
        process.exit(1);
      }

      const updateInput = {
        description,
        ...(title ? { title } : {}),
        ...(labelIds.length ? { labelIds } : {}),
        ...(stateId ? { stateId } : {}),
        ...(cycleId ? { cycleId } : {}),
      };

      if (dryRun) {
        console.log(
          `${i + 1}/${tickets.length} UPDATE ${identifier} ${existing.title || ""}`
        );
        console.log(`  → would set description (${description.length} chars)`);
        if (title) console.log(`  → would set title: ${title}`);
        if (labelIds.length) console.log(`  → would set ${labelIds.length} label(s)`);
        if (stateId) console.log(`  → would set state`);
        if (cycleId) console.log(`  → would set cycle`);
      } else {
        const { issueUpdate } = await gql(
          `mutation($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) {
              success
              issue { identifier title url }
            }
          }`,
          { id: existing.id, input: updateInput }
        );
        if (!issueUpdate.success) {
          console.error(`Failed update: ${identifier}`);
          process.exit(1);
        }
        console.log(
          `${i + 1}/${tickets.length} ${issueUpdate.issue.identifier} ${issueUpdate.issue.url} (updated)`
        );
      }

      if (dependsOn.length) {
        for (const depId of dependsOn) {
          if (dryRun) {
            console.log(`  [dry-run] relation: ${depId} blocks ${identifier}`);
            continue;
          }
          try {
            const blocker = await fetchIssueByIdentifier(depId);
            await createBlockingRelation(blocker.id, existing.id);
            console.log(`  → relation: ${depId} blocks ${identifier}`);
            await addBlocksOthersLabelToBlocker(team.id, blocker, dryRun);
          } catch (e) {
            console.warn(`  (warn) Relation ${depId} → ${identifier}: ${e.message}`);
          }
        }
      }
      continue;
    }

    if (!title) {
      console.error(`Ticket at index ${i}: title required for create`);
      process.exit(1);
    }

    const input = {
      teamId: team.id,
      ...(stateId ? { stateId } : {}),
      ...(cycleId ? { cycleId } : {}),
      title,
      description,
      ...(labelIds.length ? { labelIds } : {}),
    };

    if (dryRun) {
      console.log(`${i + 1}/${tickets.length} CREATE (dry-run): ${title}`);
      continue;
    }

    const { issueCreate } = await gql(
      `mutation($input: IssueCreateInput!) {
        issueCreate(input: $input) {
          success
          issue { id identifier title url }
        }
      }`,
      { input }
    );

    if (!issueCreate.success) {
      console.error(`Failed: ${title}`);
      process.exit(1);
    }

    const createdIssue = issueCreate.issue;
    if (dependsOn.length && createdIssue?.id) {
      for (const depId of dependsOn) {
        try {
          const blocker = await fetchIssueByIdentifier(depId);
          await createBlockingRelation(blocker.id, createdIssue.id);
          console.log(
            `  → relation: ${depId} blocks ${createdIssue.identifier}`
          );
          await addBlocksOthersLabelToBlocker(team.id, blocker, dryRun);
        } catch (e) {
          console.warn(
            `  (warn) Relation ${depId} → ${createdIssue.identifier}: ${e.message}`
          );
        }
      }
    }

    const labelInfo =
      block || extraLabels.length
        ? ` [labels: ${[block, ...extraLabels].filter(Boolean).join(", ")}]`
        : "";
    console.log(
      `${i + 1}/${tickets.length} ${createdIssue.identifier} ${createdIssue.url}${labelInfo}`
    );
  }

  console.log("\nDone.");
})();
