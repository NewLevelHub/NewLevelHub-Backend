#!/usr/bin/env node
/**
 * Назначение исполнителей на тикеты Linear из JSON-файла.
 *
 * Конфиг: scripts/linear-config.local.js (LINEAR_API_KEY обязателен)
 *
 * Формат JSON (см. linear-assign-cycle7.json):
 *   {
 *     "assignees": { "baidar": "email@example.com", "beka": "email2@example.com" },
 *     "assignments": [ { "identifier": "DEV-123", "assignee": "baidar" } ]
 *   }
 *
 * Запуск:
 *   node scripts/linear-assign-tickets.js [path/to/assignments.json] [--dry-run]
 */

const fs = require("fs");
const path = require("path");

const API_URL = "https://api.linear.app/graphql";
const defaultJsonPath = path.join(__dirname, "linear-assign-cycle7.json");

function loadLocalConfig() {
  const p = path.join(__dirname, "linear-config.local.js");
  if (!fs.existsSync(p)) return {};
  try {
    delete require.cache[require.resolve(p)];
    return require(p);
  } catch (e) {
    console.error("Failed to load linear-config.local.js:", e.message);
    process.exit(1);
  }
}

const local = loadLocalConfig();
const apiKey = process.env.LINEAR_API_KEY || local.LINEAR_API_KEY || "";

async function gql(query, variables = {}) {
  const res = await fetch(API_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: apiKey },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors) throw new Error(JSON.stringify(json.errors, null, 2));
  return json.data;
}

/** Резолвит Linear userId по email. Кэшируется. */
const userIdCache = new Map();

async function resolveUserId(email) {
  if (userIdCache.has(email)) return userIdCache.get(email);

  const data = await gql(
    `query($email: String!) {
      users(filter: { email: { eq: $email } }, first: 1) {
        nodes { id name email }
      }
    }`,
    { email }
  );

  const user = data.users?.nodes?.[0];
  if (!user) throw new Error(`User not found in Linear: ${email}`);

  userIdCache.set(email, user.id);
  return user.id;
}

/** Находит Linear issue ID по идентификатору (DEV-123). */
async function fetchIssueId(identifier) {
  const m = identifier.trim().toUpperCase().match(/^([A-Z][A-Z0-9]*)-(\d+)$/);
  if (!m) throw new Error(`Bad identifier: ${identifier}`);

  const data = await gql(
    `query($teamKey: String!, $number: Float!) {
      issues(
        filter: { team: { key: { eq: $teamKey } }, number: { eq: $number } }
        first: 1
      ) {
        nodes { id identifier title assignee { email } }
      }
    }`,
    { teamKey: m[1], number: parseInt(m[2], 10) }
  );

  const issue = data.issues?.nodes?.[0];
  if (!issue) throw new Error(`Issue not found: ${identifier}`);
  return issue;
}

function parseArgs() {
  const raw = process.argv.slice(2);
  const dryRun = raw.includes("--dry-run");
  const args = raw.filter((a) => a !== "--dry-run");
  return { jsonPath: args[0] || defaultJsonPath, dryRun };
}

(async () => {
  if (!apiKey || apiKey.includes("ВСТАВЬ")) {
    console.error("Set LINEAR_API_KEY in scripts/linear-config.local.js");
    process.exit(1);
  }

  const { jsonPath, dryRun } = parseArgs();
  const abs = path.resolve(jsonPath);

  if (!fs.existsSync(abs)) {
    console.error(`File not found: ${abs}`);
    process.exit(1);
  }

  const raw = JSON.parse(fs.readFileSync(abs, "utf-8"));
  const { assignees, assignments } = raw;

  if (!assignees || typeof assignees !== "object") {
    console.error('JSON must have an "assignees" object');
    process.exit(1);
  }
  if (!Array.isArray(assignments) || assignments.length === 0) {
    console.error('JSON must have a non-empty "assignments" array');
    process.exit(1);
  }

  console.log(`File: ${abs}`);
  console.log(`Tickets: ${assignments.length}`);
  if (dryRun) console.log("Mode: DRY-RUN (no writes)\n");
  else console.log("");

  // Предварительно резолвим всех пользователей
  console.log("Resolving users...");
  const emailToId = new Map();
  for (const [alias, email] of Object.entries(assignees)) {
    try {
      const id = await resolveUserId(email);
      emailToId.set(alias, id);
      console.log(`  ${alias} → ${email} (${id})`);
    } catch (e) {
      console.error(`  ERROR: ${e.message}`);
      process.exit(1);
    }
  }
  console.log("");

  let ok = 0;
  let skipped = 0;
  let errors = 0;

  for (let i = 0; i < assignments.length; i++) {
    const { identifier, assignee } = assignments[i];
    const idx = `${i + 1}/${assignments.length}`;

    if (!emailToId.has(assignee)) {
      console.error(`${idx} [${identifier}] Unknown assignee alias: "${assignee}"`);
      errors++;
      continue;
    }

    let issue;
    try {
      issue = await fetchIssueId(identifier);
    } catch (e) {
      console.error(`${idx} [${identifier}] ${e.message}`);
      errors++;
      continue;
    }

    const assigneeId = emailToId.get(assignee);
    const alreadyAssigned = issue.assignee?.email === assignees[assignee];

    if (alreadyAssigned) {
      console.log(`${idx} ${identifier} — already assigned to ${assignee}, skip`);
      skipped++;
      continue;
    }

    if (dryRun) {
      console.log(`${idx} ${identifier} "${issue.title}" → would assign to ${assignee}`);
      ok++;
      continue;
    }

    try {
      const { issueUpdate } = await gql(
        `mutation($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
            issue { identifier title url }
          }
        }`,
        { id: issue.id, input: { assigneeId } }
      );

      if (!issueUpdate?.success) {
        console.error(`${idx} ${identifier} — update failed`);
        errors++;
      } else {
        console.log(`${idx} ${identifier} → ${assignee}  ${issueUpdate.issue.url}`);
        ok++;
      }
    } catch (e) {
      console.error(`${idx} ${identifier} — ${e.message}`);
      errors++;
    }
  }

  console.log(`\nDone. Assigned: ${ok}, Skipped: ${skipped}, Errors: ${errors}`);
  if (errors > 0) process.exit(1);
})();
