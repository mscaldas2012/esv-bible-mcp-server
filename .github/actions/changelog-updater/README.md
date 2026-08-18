# Claude Changelog Updater

A GitHub Action that uses Claude to read a pull request's diff and automatically
maintain `CHANGELOG.md`: it decides the semantic version bump (`major.minor.patch`),
writes a high-level bullet summary of the change, and pushes the update as a commit
on the PR branch.

## How versioning works

- The changelog file itself is the source of truth. On every run the action reads
  the most recent `## [X.Y.Z]` heading in `CHANGELOG.md` to determine the current
  version — there is no separate state file.
- This means if you manually edit the version number in the changelog (e.g. to
  correct a bump, or to jump to a specific release version), the next PR will bump
  **from your edited value**, not from whatever the bot last generated.
- If the changelog has no version entries yet, it's seeded at `base-version`
  (default `0.1.0`) instead of bumping past it.
- Claude classifies the bump as:
  - **major** — incompatible / breaking change
  - **minor** — new backwards-compatible functionality
  - **patch** — backwards-compatible bug fix or small internal change

## Usage

Add a workflow to the target repo. The PR branch must be checked out (not the
merge ref) and the token needs write access so the action can push the commit
back onto the PR:

```yaml
name: Update changelog

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: write
  pull-requests: read

concurrency:
  group: changelog-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  changelog:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.ref }}
          repository: ${{ github.event.pull_request.head.repo.full_name }}
          fetch-depth: 0

      - uses: mscaldas2012/github-actions/changelog-updater@main
        with:
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

> For PRs from forks, `GITHUB_TOKEN` cannot push to the fork's branch. Either
> restrict this workflow to same-repo PRs or use a PAT with write access to forks
> you trust.

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `anthropic-api-key` | yes | — | Anthropic API key (store as a secret). |
| `github-token` | no | `${{ github.token }}` | Needs `contents:write` and `pull-requests:read`. |
| `changelog-path` | no | `CHANGELOG.md` | Path to the changelog file. |
| `model` | no | `claude-sonnet-5` | Claude model id to use. |
| `base-version` | no | `0.1.0` | Seed version when the changelog has no prior entries. |
| `commit-message` | no | `chore: update changelog [skip ci]` | Commit message for the changelog push. |
| `bot-name` | no | `claude-changelog-bot` | Git author/committer name; also used to detect and skip the bot's own commits so the workflow doesn't trigger itself in a loop. |
| `bot-email` | no | `claude-changelog-bot@users.noreply.github.com` | Git author/committer email. |

## Outputs

| Output | Description |
|---|---|
| `version` | The version written to the changelog for this PR. |

## Loop protection

Pushing a commit to the PR branch would normally re-trigger the `synchronize`
event. Before doing any work, the action checks whether the PR's latest commit
was already authored by `bot-name` and exits early if so.
