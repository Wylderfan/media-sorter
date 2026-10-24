# media-sorter

A manually invoked movies-only Bash CLI for sending prepared movie rips from a Mac to a Linux Jellyfin library over SSH and rsync. Prepare one folder per movie or a loose top-level MKV or MP4, and finish writing files before starting. No metadata lookup, renaming, watchers, Jellyfin API, or server administration.

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

The source can contain movie folders such as `Movies/Movie Title (Year)/chosen-file.mkv`
or loose files such as `Movies/Some Title.mkv`. Both send the approved file directly
into the configured destination: `DEST/chosen-file.mkv` or `DEST/Some Title.mkv`.
Original filenames are preserved; no movie-name destination folders are created.
The exact destination file path is displayed before approval. Printable paths
show spaces, apostrophes, and literal backslashes as they occur in the filename;
these labels are not shell commands to copy and execute. Names containing
non-printable bytes (including terminal controls, tabs, or newlines) retain
shell-escaped display for safety. Filenames and transfer quoting are unchanged. Files with the same
basename from different source folders or loose files share a destination path:
the first successful publication wins and later collisions are reported and skipped,
without overwriting or trying another candidate from an already approved folder.
No title/year metadata or filenames are inferred.
Both `.mkv` and `.mp4` are supported, case insensitively, in movie folders and
as loose root files. Files are copied as-is without conversion.
Each loose movie file gets its own approval; the one-file limit applies per actual
movie subfolder, not to the entire Movies root. Other root files and symlinks
are ignored. Only immediate movie folders and their immediate MKV/MP4 files are candidates. Nested files (including bonus
content) are ignored. Every immediate MKV/MP4 remains
selectable, including names such as `Extra Ordinary (2019).mkv`, `bonus.mkv`, or
`sample.mkv`. Candidates across both formats are offered largest first, showing filename and decimal GB
(1 GB = 1,000,000,000 bytes), rounded to two decimal places. For example,
4,287,390,252 bytes displays as `4.29 GB`. Nonempty files below 0.01 GB show
`<0.01 GB`; empty files show `0.00 GB`. Sorting always uses exact byte counts. Size is only a heuristic: the largest file can still be an extra.

For each candidate, `Send this file? [Y/n]` accepts Enter, Y, or Yes to approve
(case insensitive). N or No offers the next candidate in that movie folder.
Invalid input repeats the prompt; rejecting every candidate skips the movie
once and continues. Declining a loose movie file skips just that movie.
After the first approval, no more candidates are offered for that folder, even
if that transfer fails or conflicts. At most one file per movie is selected per
run. Approvals are collected for the entire batch before any files are transferred.
After selection, the CLI prints the approved count and processes that list without
further approval prompts. EOF (Ctrl-D) during selection cancels the entire batch
with nonzero status: even previously approved files are not transferred. Preview uses `Preview this file? [Y/n]`
and makes no remote library changes. `--apply` is still required to send files.
Review carefully: titles and main-feature identity are not inferred or verified.

Preview checks connectivity, capabilities, destination access, and collisions
without writing remote library files. Apply shows each original filename and its
position in the batch, plus rsync's per-file bytes, percentage, speed, and time
progress (`--progress`). Very small files may finish in one update. Apply stages one approved file at a time
in a unique hidden `.media-sorter-*` directory directly inside the configured
destination. This temporary staging directory is removed after ordinary completion. An atomic hard link
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

### Output and locale troubleshooting

The CLI sets `LC_ALL=C` and `LANG=C` for its own commands and children, and for
remote commands/rsync. This avoids repeated warnings from inheriting a locale
such as `en_CA.UTF-8` that is unavailable on one machine, without editing local
or server configuration or hiding stderr. Bash can still warn once at startup
before script code runs; remote shell startup files can also set an invalid
locale before the command runs. To avoid an inherited local startup warning:

```sh
LC_ALL=C LANG=C ./bin/media-sorter movies --apply
```

A `cannot change locale ... No such file or directory` warning refers to locale
availability, not necessarily a missing movie/destination directory. For a separate
path error, retain its exact line and the selected/destination paths for diagnosis.
The destination must already exist; approved source paths are rechecked before
transfer. Do not move or modify the prepared batch during selection or transfer.

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
Coverage includes mixed-case MKV/MP4 discovery and ranking, multiple movies and candidates, movie-title selection, nested
bonus exclusion, scan failures, input edge cases, transport options, preview,
source retention, collisions, unusual paths, and failed transfers.
Tests do not establish real SSH connectivity or access to your server.

## Remaining work and limits

This is a small MVP. TV/both commands are rejected. Duration-based suggestions,
TV identification and ordering remain future work. A user has reported a
successful Mac-to-Linux test; automated checks use local fixtures, not a real
server. The destination must support hard links; publication does not overwrite
existing files. Interruptions may leave staging data as described above.
