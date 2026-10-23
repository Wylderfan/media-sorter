# media-sorter

A planned, manually invoked Bash CLI for sending finished movie rips from a Mac to a Linux Jellyfin library using rsync over SSH through an existing Tailscale connection.

> **Status: rough movies-only MVP.** `bin/media-sorter` implements interactive selection, preview, and approved-file transfer. Isolated integration tests pass; real macOS/Linux SSH transfers and Bash 3.2 have not yet been validated.

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


## Running the MVP

Run from this checkout (or add its `bin` directory to your PATH):

```sh
# macOS ships an old rsync; install a current version first:
brew install rsync
./bin/media-sorter configure
./bin/media-sorter movies                  # preview, with per-file approval
./bin/media-sorter movies --source /absolute/path/to/Movies
./bin/media-sorter movies --apply          # approval still required
```

The Linux server also needs rsync 3.x with `--protect-args` support. The destination
library must already exist and be writable. Configure SSH keys/agent access and
verify the host through ordinary SSH first: this CLI uses `BatchMode=yes` and
preserves SSH's host-key policy. It does not install or configure Tailscale.

Settings are four plain text lines (source, server, username, destination) at
`~/.config/media-sorter/config`. `MEDIA_SORTER_CONFIG` overrides that location.
They are never executed as shell code. Setup requires a terminal and explicit
`yes` before saving; `--source` does not rewrite saved settings. Paths must be
absolute and settings cannot contain newlines. No passwords are stored.

The source layout is `Movies/Movie Title (Year)/chosen-file.mkv`; destination
layout preserves `Movie Title (Year)/chosen-file.mkv`. Only immediate movie
folders and their immediate MKVs are candidates. Nested extras are ignored,
as are filenames beginning with conventional extra, bonus, trailer, featurette, or sample
labels followed by a separator (case insensitive). The largest remaining file is merely a suggestion. Choose
a number and type `yes` to approve; empty or invalid input skips the movie.
Review carefully: titles and main-feature identity are not inferred or verified.

Preview checks connectivity, capabilities, destination access, and collisions
without writing remote library files. Apply stages one approved file at a time
in a unique hidden directory inside its movie folder. An atomic hard link
publishes the finished file without replacing an existing final path; this
requires hard-link support on the destination filesystem. Staging data is cleaned
up after ordinary completion or failure. Abrupt interruption can leave a hidden
`.media-sorter-*` directory containing partial data; inspect and remove only
that abandoned staging directory before retrying. Originals are always retained.
Existing final paths are conflicts, not verified duplicates. Transfer failures
return nonzero. Jellyfin indexing is not checked or triggered.

### Development verification

```sh
bash -n bin/media-sorter
python3 -m unittest discover -s tests -v
```

Tests use temporary media, a pseudo-terminal, and local SSH/rsync fixtures.
When rsync is installed (or `MEDIA_SORTER_TEST_RSYNC` points to its executable),
an additional test runs the real rsync client/server protocol over a local
remote-shell fixture; otherwise that test is explicitly skipped. CI installs
rsync and runs this test as well. They
cover preview immutability, explicit skipping, source retention, collisions,
unusual paths, failed transfers, missing destinations, and argument/noninteractive
rejection. They do not establish real SSH connectivity, macOS
Bash 3.2 compatibility, or access to your server. Validate with disposable media
and a scratch destination before using the tool with a real library. The design
sections above retain the original proposal; this section describes implemented
choices. TV/both commands are rejected.
