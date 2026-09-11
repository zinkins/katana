# Changelog

Katana follows semantic versioning after its standalone `1.0` release. Until
then, compatibility promises are scoped explicitly below.

## Unreleased

### Added

- TypeScript Compiler API maps top-level `node:test` `describe(...)`
  registrations as ordered, selectable symbols and can move safe complete
  registration groups through side-effect modules.

### Fixed

- Optional Node bridge modules resolve correctly from the Katana package on
  Windows.
- Human-readable `map` output now reads nested spans and reference names while
  preserving the legacy `outline` and JSON payload formats.
- Structural JavaScript/TypeScript outlines retain scanner state across
  multiline templates, interpolations, comments, and continued strings.
- Adjacent JSDoc comments travel with their declarations instead of being
  included in the preceding symbol; separate file headers stay in the source.
- Text splits preserve explicit relative-import extensions and rebase copied
  imports and generated dependencies for targets in subdirectories.

## 0.2.0 - 2026-08-07

### Added

- Native `.mjs` and `.cjs` discovery through the JavaScript adapter.
- A repository gate enforcing a hard 500-line ceiling.

### Changed

- Split CLI parsing/output, apply I/O/validation, bridge module resolution,
  and source-split tests into cohesive files under the source budget.

## 0.1.0 - 2026-08-06

### Added

- `DocumentIR` v2 and the source-free `katana map` command.
- Python entry-point adapters under `katana.adapters`.
- Project-local command adapters through `.katana/adapters.json`.
- Optional TanStack Markdown AST enrichment.
- Python 3.14 support and standalone CI across Linux, macOS, and Windows.
- Bundled language adapters under `katana.adapters.<stack>`.
- Public toolchain adapters and the `katana.toolchains` entry-point group.

### Changed

- The standalone distribution is named `katana-agent`; the import package,
  CLI, and adapter entry points remain `katana`.

### Compatibility

- Split-plan v1 remains unchanged.
- Existing `schema`, `outline`, `assess`, `plan`, `apply`, `large`, and `project`
  payloads remain compatible.
- Existing adapter ids remain `node_typescript`, `dart_analyzer`, and
  `node_postcss`.
- UTF-8 byte spans and hashes now remain exact for CRLF files, and mechanical
  split output preserves the source newline style.
