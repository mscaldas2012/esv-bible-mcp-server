const fs = require('fs');
const path = require('path');
const core = require('@actions/core');
const github = require('@actions/github');
const exec = require('@actions/exec');
const Anthropic = require('@anthropic-ai/sdk');

const CHANGELOG_HEADER = [
  '# Changelog',
  '',
  'All notable changes to this project will be documented in this file.',
  '',
].join('\n');

const VERSION_HEADING_RE = /^##\s*\[(\d+)\.(\d+)\.(\d+)\]/m;

function bumpVersion(version, bump) {
  let [major, minor, patch] = version.split('.').map(Number);
  if (bump === 'major') {
    major += 1;
    minor = 0;
    patch = 0;
  } else if (bump === 'minor') {
    minor += 1;
    patch = 0;
  } else {
    patch += 1;
  }
  return `${major}.${minor}.${patch}`;
}

function readChangelog(changelogPath) {
  if (!fs.existsSync(changelogPath)) {
    return CHANGELOG_HEADER;
  }
  return fs.readFileSync(changelogPath, 'utf8');
}

// The changelog file itself is the source of truth for the current version.
// If a human edits the top version entry by hand, that edited value is what
// gets read back here on the next run, so manual overrides are respected
// automatically without any separate state to keep in sync.
function findCurrentVersion(changelogContent, baseVersion) {
  const match = changelogContent.match(VERSION_HEADING_RE);
  if (!match) {
    return null;
  }
  return `${match[1]}.${match[2]}.${match[3]}`;
}

function insertEntry(changelogContent, entry) {
  const match = changelogContent.match(VERSION_HEADING_RE);
  if (match) {
    const idx = changelogContent.indexOf(match[0]);
    return changelogContent.slice(0, idx) + entry + '\n' + changelogContent.slice(idx);
  }
  const trimmed = changelogContent.endsWith('\n') ? changelogContent : changelogContent + '\n';
  return trimmed + (trimmed.endsWith('\n\n') ? '' : '\n') + entry + '\n';
}

async function getPullRequestDiff(octokit, owner, repo, pullNumber) {
  const response = await octokit.rest.pulls.get({
    owner,
    repo,
    pull_number: pullNumber,
    mediaType: { format: 'diff' },
  });
  return typeof response.data === 'string' ? response.data : String(response.data);
}

async function lastCommitIsFromBot(octokit, owner, repo, pullNumber, botName) {
  const { data: commits } = await octokit.rest.pulls.listCommits({
    owner,
    repo,
    pull_number: pullNumber,
    per_page: 1,
  });
  if (commits.length === 0) return false;
  const last = commits[commits.length - 1];
  const authorName = last.commit && last.commit.author && last.commit.author.name;
  const committerName = last.commit && last.commit.committer && last.commit.committer.name;
  return authorName === botName || committerName === botName;
}

async function classifyChanges({ apiKey, model, prTitle, prBody, diff, currentVersion }) {
  const anthropic = new Anthropic({ apiKey });

  const MAX_DIFF_CHARS = 60000;
  let truncatedDiff = diff;
  if (diff.length > MAX_DIFF_CHARS) {
    truncatedDiff = diff.slice(0, MAX_DIFF_CHARS) + '\n\n[diff truncated for length]';
  }

  const tool = {
    name: 'record_changelog_entry',
    description: 'Records the semantic version bump and changelog bullet points for this pull request.',
    input_schema: {
      type: 'object',
      properties: {
        bump: {
          type: 'string',
          enum: ['major', 'minor', 'patch'],
          description:
            'major = incompatible/breaking change. minor = new backwards-compatible functionality. patch = backwards-compatible bug fix or small internal change.',
        },
        bump_reason: {
          type: 'string',
          description: 'One sentence explaining why this bump level was chosen.',
        },
        changes: {
          type: 'array',
          items: { type: 'string' },
          description: 'High-level, user-facing bullet points describing the changes, Keep a Changelog style. No trailing punctuation, start with a verb.',
        },
      },
      required: ['bump', 'changes'],
    },
  };

  const message = await anthropic.messages.create({
    model,
    max_tokens: 1024,
    system:
      'You are a release-notes assistant. Given a pull request title, description, and diff, you determine the ' +
      'correct semantic version bump (major.minor.patch) and write concise, high-level changelog bullet points ' +
      'describing what changed from a user/consumer perspective. Ignore purely internal churn (formatting, ' +
      'comments, test-only changes) when deciding the bump, but still call out user-facing consequences. ' +
      'Only use the record_changelog_entry tool to respond.',
    tools: [tool],
    tool_choice: { type: 'tool', name: 'record_changelog_entry' },
    messages: [
      {
        role: 'user',
        content:
          `Current version: ${currentVersion}\n\n` +
          `PR title: ${prTitle}\n\n` +
          `PR description:\n${prBody || '(no description provided)'}\n\n` +
          `Diff:\n\`\`\`diff\n${truncatedDiff}\n\`\`\``,
      },
    ],
  });

  const toolUse = message.content.find((block) => block.type === 'tool_use');
  if (!toolUse) {
    throw new Error('Claude did not return a tool_use block with the changelog classification.');
  }
  return toolUse.input;
}

function formatEntry(version, changes) {
  const date = new Date().toISOString().slice(0, 10);
  const bullets = changes.map((c) => `- ${c}`).join('\n');
  return `## [${version}] - ${date}\n${bullets}\n`;
}

async function run() {
  try {
    const apiKey = core.getInput('anthropic-api-key') || process.env.ANTHROPIC_API_KEY;
    const githubToken = core.getInput('github-token') || process.env.GITHUB_TOKEN;
    const changelogPath = process.env.CHANGELOG_PATH || 'CHANGELOG.md';
    const model = process.env.CLAUDE_MODEL || 'claude-sonnet-5';
    const baseVersion = process.env.BASE_VERSION || '0.1.0';
    const commitMessage = process.env.COMMIT_MESSAGE || 'chore: update changelog [skip ci]';
    const botName = process.env.BOT_NAME || 'claude-changelog-bot';
    const botEmail = process.env.BOT_EMAIL || 'claude-changelog-bot@users.noreply.github.com';

    if (!apiKey) throw new Error('Missing anthropic-api-key input / ANTHROPIC_API_KEY env var.');
    if (!githubToken) throw new Error('Missing github-token input / GITHUB_TOKEN env var.');

    const context = github.context;
    const pullRequest = context.payload.pull_request;
    if (!pullRequest) {
      core.info('No pull_request payload found on this event; nothing to do.');
      return;
    }

    const { owner, repo } = context.repo;
    const pullNumber = pullRequest.number;
    const octokit = github.getOctokit(githubToken);

    if (await lastCommitIsFromBot(octokit, owner, repo, pullNumber, botName)) {
      core.info(`Latest commit was authored by ${botName}; skipping to avoid a trigger loop.`);
      return;
    }

    const diff = await getPullRequestDiff(octokit, owner, repo, pullNumber);
    if (!diff.trim()) {
      core.info('Empty diff; nothing to summarize.');
      return;
    }

    const changelogContent = readChangelog(changelogPath);
    const currentVersion = findCurrentVersion(changelogContent, baseVersion) || baseVersion;
    const isFirstEntry = !VERSION_HEADING_RE.test(changelogContent);

    const classification = await classifyChanges({
      apiKey,
      model,
      prTitle: pullRequest.title,
      prBody: pullRequest.body,
      diff,
      currentVersion,
    });
    const changes = classification.changes;

    // With no prior entry to bump from, seed the changelog at the configured
    // base version instead of bumping past it.
    const newVersion = isFirstEntry ? currentVersion : bumpVersion(currentVersion, classification.bump);
    if (!isFirstEntry) {
      core.info(`Bump: ${classification.bump} (${classification.bump_reason || 'no reason given'})`);
    }

    const entry = formatEntry(newVersion, changes);
    const updatedContent = insertEntry(changelogContent, entry);

    fs.mkdirSync(path.dirname(path.resolve(changelogPath)), { recursive: true });
    fs.writeFileSync(changelogPath, updatedContent, 'utf8');
    core.info(`Updated ${changelogPath}: ${currentVersion} -> ${newVersion}`);

    await exec.exec('git', ['config', 'user.name', botName]);
    await exec.exec('git', ['config', 'user.email', botEmail]);
    await exec.exec('git', ['add', changelogPath]);

    const commitResult = await exec.exec('git', ['diff', '--cached', '--quiet'], { ignoreReturnCode: true });
    if (commitResult === 0) {
      core.info('No changes to commit.');
      return;
    }

    await exec.exec('git', ['commit', '-m', commitMessage]);

    const branch = pullRequest.head.ref;
    await exec.exec('git', ['push', 'origin', `HEAD:${branch}`]);

    core.setOutput('version', newVersion);
  } catch (error) {
    core.setFailed(error.message);
  }
}

run();
