# media-sorter

A manually invoked movies-only Bash CLI for sending prepared movie rips from a Mac to a Linux Jellyfin library over SSH and rsync. Prepare one folder per movie and finish writing files before starting. No metadata lookup, renaming, watchers, Jellyfin API, or server administration.

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
verify the host through ordinary SSH first. Both preflight and transfer use
`BatchMode=yes` and a 10-second connection timeout, preserving SSH's host-key
policy. The CLI does not install or configure Tailscale.

Settings are four plain text lines (source, server, username, destination) at
`~/.config/media-sorter/config`. `MEDIA_SORTER_CONFIG` overrides that location.
They are never executed as shell code. Setup requires a terminal and explicit
`yes` before saving; `--source` does not rewrite saved settings. Paths must be
absolute and settings cannot contain newlines. No passwords are stored.

The source layout is `Movies/Movie Title (Year)/chosen-file.mkv`; destination
layout preserves `Movie Title (Year)/chosen-file.mkv`. Only immediate movie
folders and their immediate MKVs are candidates. Nested files (including bonus
content) are ignored. Every immediate MKV remains
selectable, including names such as `Extra Ordinary (2019).mkv`, `bonus.mkv`, or
`sample.mkv`. Candidates are offered largest first, showing filename and size in
bytes. Size is only a heuristic: the largest file can still be an extra.

For each candidate, `Send this file? [Y/n]` accepts Enter, Y, or Yes to approve
(case insensitive). N or No offers the next candidate in that movie folder.
Invalid input repeats the prompt; rejecting every candidate skips the movie.
After the first approval, no more candidates are offered for that folder, even
if that transfer fails or conflicts. At most one file per movie is selected per
run. EOF (Ctrl-D) stops the batch with nonzero status and never approves a file;
any earlier completed transfers remain. Preview uses `Preview this file? [Y/n]`
and makes no remote library changes. `--apply` is still required to send files.
Review carefully: titles and main-feature identity are not inferred or verified.

Preview checks connectivity, capabilities, destination access, and collisions
without writing remote library files. Apply stages one approved file at a time
in a unique hidden directory inside its movie folder. An atomic hard link
publishes the finished file without replacing an existing final path; this
requires hard-link support on the destination filesystem. Staging data is cleaned
up after ordinary completion or failure. Abrupt interruption can leave a hidden
`.media-sorter-*` directory containing partial data; inspect and remove only
that abandoned staging directory before retrying. Originals are always retained.
Existing final paths are conflicts, not verified duplicates. Source enumeration,
movie enumeration, candidate inspection, and transfer failures
return nonzero and are reported. A failed source scan stops the batch; a failed
movie scan skips that folder and continues the remaining movies. Partial scan
results are never offered for approval. Jellyfin indexing is not checked or triggered.

### Development verification

```sh
bash -n bin/media-sorter
python3 -m unittest discover -s tests -v
```

Tests use temporary media, a pseudo-terminal, and local SSH/rsync fixtures.
When rsync is installed (or `MEDIA_SORTER_TEST_RSYNC` points to its executable),
an additional test runs the real rsync client/server protocol over a local
remote-shell fixture; otherwise that test is explicitly skipped. CI installs
rsync and runs this test as well on Ubuntu and macOS. The macOS job
explicitly runs the CLI with Apple's `/bin/bash` (3.2); local remote-command
fixtures simulate the Linux destination, including GNU `ln -T`.
Coverage includes multiple movies and candidates, movie-title selection, nested
bonus exclusion, scan failures, input edge cases, transport options, preview,
source retention, collisions, unusual paths, and failed transfers.
Tests do not establish real SSH connectivity or access to your server.

## Remaining work and limits

This is a small MVP. TV/both commands are rejected. Duration-based suggestions,
TV identification and ordering remain future work. A user has reported a
successful Mac-to-Linux test; automated checks use local fixtures, not a real server. The destination must support hard links; publication does not overwrite
existing files. Interruptions may leave staging data as described above.
