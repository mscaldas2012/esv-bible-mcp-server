# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] - 2026-08-18
- Add automated changelog-updater GitHub Action that uses Claude to summarize PR changes and determine semantic version bumps
- Add CI workflow that runs the changelog updater on PR open, sync, and reopen events for same-repo pull requests
- Seed CHANGELOG.md with initial release history for the changelog automation to build on

## [1.0.0] - 2026-08-18
- Initial release
- Add `search_passages` tool for full-text search across the ESV Bible
- Add `get_passage_text` tool to retrieve authoritative ESV wording for a reference
- Add `get_passage_audio_url` tool to resolve spoken-word audio for a passage
- Add `esv://canon` resource listing the 66 books of the Protestant canon
- Add `explain-passage` prompt to walk through a passage's context and meaning
