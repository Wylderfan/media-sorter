# media-sorter

A planned, manually invoked Bash CLI for sending finished movie rips from a Mac to a Linux Jellyfin library using rsync over SSH through an existing Tailscale connection.

> **Status: design stage.** This repository currently contains documentation, not a working media-transfer tool. All selection, configuration, validation, and transfer features below are planned. There is no install or run procedure yet.

## Planned movies-first workflow

1. Finish ripping and prepare a batch in a known Movies directory on the Mac. Each movie has its own folder with the supplied title and release year; folders may also contain extras and information files. Do not start a batch while its files are still being written.
2. Load saved settings, or offer first-run setup. A source override selects another batch without silently changing the saved default.
3. Validate the source and destination folders and server reachability before transferring.
4. Scan only the selected Movies directory. Suggest a main-feature MKV for each movie folder, then ask the user to **approve or skip** it.
5. Transfer **only explicitly approved files** with rsync over SSH. Do not copy whole folders or every MKV. Preserve the chosen filenames.
6. Retain all Mac originals, skip and report existing destination conflicts, and summarize transferred, skipped, conflicted, and failed items.
7. Leave discovery to Jellyfin's existing library scan behavior. No Jellyfin API calls or forced refresh; a successful transfer does not prove indexing.

The candidate-ranking method is not decided. Size, duration, or title clues may help, but the largest or longest file is not necessarily the main feature. Ambiguity, silence, or unavailable interactive input must never count as approval.

## Planned setup and configuration

The intended environment is a Mac with Bash, SSH, and rsync, plus a reachable Linux destination with rsync and an existing Jellyfin library. Tailscale and SSH access are assumed to be configured already; this project is not a server installer or administration tool.

A separate editable configuration file is planned. First-run prompts will collect:

| Setting | Value to provide |
| --- | --- |
| Movies source | Absolute path to the prepared Movies directory on the Mac |
| Server | Tailscale hostname or SSH alias |
| SSH user | Linux login username |
| Movies destination | Absolute path to the Linux Jellyfin movies library |

Later runs will reuse these settings. A configure command will offer the same prompts to change them, and a per-run source override will select a different batch. The config filename, location, format, and exact command syntax remain undecided; no real paths or credentials are included here.

The implementation design proposes a data-only configuration format, safe cancellation, ordinary SSH authentication without storing passwords, and preserved SSH host verification. Local and remote rsync versions and supported options must be checked rather than assumed to match. Bash 3.2 compatibility is a proposed target, not a tested guarantee.

## Proposed interface — not implemented

These examples illustrate the draft interface only. The executable, command names, and flags do not exist yet.

```text
media-sorter configure
media-sorter movies
media-sorter movies --source <Movies-path>
media-sorter movies --apply
media-sorter --help
```

First-run setup, reconfiguration, source overrides, and approve-or-skip review are agreed requirements. Preview by default and an explicit `--apply` gate are **additional proposals still to review**. If adopted, `--apply` must not bypass approval of each file, and preview must make no remote library changes. Explicitly requested local setup saves are separate from remote media writes.

## Planned safety boundaries

- Keep all source files. Never delete, remove originals, or overwrite existing destination files.
- Skip and report destination collisions without claiming they are verified duplicates.
- Exclude extras, supplementary features, and information files from movie transfers.
- Handle interrupted or partial transfers without presenting an incomplete copy as a finished movie or overwriting an existing final file.
- Report failures accurately, with nonzero exit status for transfer errors.
- Handle filenames and local/remote paths safely, including spaces, apostrophes, Unicode, leading dashes, and shell metacharacters. Do not use `eval`.
- Stop with actionable guidance if available rsync capabilities cannot satisfy the safety requirements; do not weaken those requirements.

These are implementation requirements, not assurances about an existing executable.

## Scope and later work

**First milestone:** movie selection and approved-file transfer.

**Later:** TV identification and ordering, with separate Movies and TV source/destination directories and eventual movies, TV, or both selection. The draft proposes rejecting `tv` and `both` clearly before any transfer in the movies-only MVP, rather than silently processing movies alone.

**Out of scope:** disc ripping, transcoding, automatic metadata lookup, guessed titles, automatic renaming, computer-wide searches, watchers, scheduled jobs, a separate Ready directory, automatic rip-completion detection, and Jellyfin/server administration. Manual invocation declares the prepared batch ready.

## Decisions still open

- Main-feature ranking and the information shown for approval; any duration-inspection dependency.
- Exact input nesting and destination relative layout, based on a representative movie folder.
- Preview-by-default behavior, an additional apply flag, and explicit rejection of unsupported TV modes.
- Final CLI spelling and configuration format/location.
- Actual source/destination paths and SSH settings, to be supplied through setup rather than hard-coded.

## Planned verification

Implementation tests should use temporary fake media and local SSH/rsync fixtures, never a real media library or server. Coverage should include:

- Mixed movie/extras folders, approval, skipping, ambiguous choices, and absent interactive input.
- First-run setup, reconfiguration, source overrides, cancellation, and invalid settings.
- Preview immutability if adopted, source preservation, collisions, and repeated runs.
- Interrupted transfers, unavailable hosts, permission errors, and unsupported rsync capabilities.
- Safe handling of unusual filenames and remote paths.
- Empty input, invalid arguments, unsupported TV modes, and accurate summaries/exit statuses.

There is no application test suite or implemented CLI on the current documentation-only branch. Runnable usage examples and verified installation steps should be added alongside the implementation.
