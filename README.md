# media-sorter

A manually invoked Bash CLI for sending prepared movie rips from a Mac to a Linux Jellyfin library over SSH and rsync. Prepare one folder per movie or a loose top-level MKV, and finish writing files before starting. No metadata lookup, renaming, watchers, Jellyfin API, or server administration.

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
The exact destination file path is displayed before approval. Files with the same
basename from different source folders or loose files share a destination path:
the first successful publication wins and later collisions are reported and skipped,
without overwriting or trying another candidate from an already approved folder.
No title/year metadata or filenames are inferred.
Each loose MKV gets its own approval; the one-file limit applies per actual
movie subfolder, not to the entire Movies root. Non-MKV root files and symlinks
are ignored. Only immediate movie folders and their immediate MKVs are candidates. Nested files (including bonus
content) are ignored. Every immediate MKV remains
selectable, including names such as `Extra Ordinary (2019).mkv`, `bonus.mkv`, or
`sample.mkv`. Candidates are offered largest first, showing filename and size in
bytes. Size is only a heuristic: the largest file can still be an extra.

For each candidate, `Send this file? [Y/n]` accepts Enter, Y, or Yes to approve
(case insensitive). N or No offers the next candidate in that movie folder.
Invalid input repeats the prompt; rejecting every candidate skips the movie
once and continues. Declining a loose MKV skips just that movie.
After the first approval, no more candidates are offered for that folder, even
if that transfer fails or conflicts. At most one file per movie is selected per
run. EOF (Ctrl-D) stops the batch with nonzero status and never approves a file;
any earlier completed transfers remain. Preview uses `Preview this file? [Y/n]`
and makes no remote library changes. `--apply` is still required to send files.
Review carefully: titles and main-feature identity are not inferred or verified.

Preview checks connectivity, capabilities, destination access, and collisions
without writing remote library files. Apply stages one approved file at a time
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

This is a small MVP. The `both` command is rejected. Duration-based suggestions and automatic
TV episode identification remain out of scope. A user has reported a
successful Mac-to-Linux test; automated checks use local fixtures, not a real
server. The destination must support hard links; publication does not overwrite
existing files. Interruptions may leave staging data as described above.


## Experimental TV seasons

```sh
./bin/media-sorter configure-tv
./bin/media-sorter tv                       # preview is the default
./bin/media-sorter tv --source /absolute/path/to/TV
./bin/media-sorter tv --apply               # still asks for each season
```

TV uses a separate four-line file at `${MEDIA_SORTER_CONFIG}.tv`, or
`~/.config/media-sorter/config.tv` by default. Run `configure-tv` explicitly;
existing movie configuration is neither migrated nor modified. Both modes keep
source/server/username/destination settings independent. TV additionally needs
Python **3.8+ locally** and **3.6+ remotely**, plus rsync 3.x on both ends. The
Bash entry point remains compatible with macOS Bash 3.2. Keep `bin/` and `lib/`
together when installing this checkout. The TV destination must be Linux with
libc `renameat2` and filesystem support for `RENAME_NOREPLACE`. Unsupported
publication fails closed; there is no non-atomic fallback.

Supported layouts are `TV/Show Name (Year)/Season 01/` and
`TV/Show Name (Year) Season 01/` (also `TV/Season 01/`). Season labels `Season N`
and `SNN`, optionally preceded by a show name, are recognized only at the end
of the directory name. A directory containing files is also offered as an
unlabeled season; otherwise its immediate directories are offered as seasons
under that show's context. Unlabeled flat seasons containing only subdirectories
are ambiguous: use a recognizable season directory label first. Loose root
files and root symlinks are reported and ignored. No deep show/season search or
episode-name parsing occurs. Empty seasons are skipped. A season containing any
symlink or special file is rejected in full, not silently copied in part.

The parent show directory or the show prefix in a season directory supplies a
suggestion. **Every show and season suggestion must be confirmed or corrected**;
blank/unknown identities require entry, and a year is never invented. Enter a
show name with its year when known, or without a year. Season numbers 0–99 are
accepted (0 permits explicitly supplied specials). The complete target is
`TV_DEST/Show Name (Year)/Season NN/`, with the year omitted when absent. Before
`Send this season? [Y/n]` (or preview), the CLI lists all relative contents and
the regular-file count. This is a file count, not an inferred episode count.
Enter/Y/Yes approves; N/No skips the whole season; invalid input repeats; EOF
stops with failure and does not approve the pending season. No file is renamed.
All regular files, sidecars, empty subdirectories, and relative subtrees inside
an approved season are retained; originals are never removed.

Preview performs read-only SSH capability/path/collision checks and never creates
remote directories. Apply first copies into an exclusive hidden
`.media-sorter-tv-*` directory inside the TV destination. Only a successful copy
with unchanged source inventory is published, using an atomic, exclusive directory
rename. Existing seasons (including empty directories, files, and symlinks) are
conflicts: no overwrite, merge, duplicate verification, or resume. A target that
appears during copying also wins and is untouched. Existing show directories can
hold other seasons. A show path must be a real directory; source/destination
ancestors cannot be symlinks. Absolute configuration paths reject traversal
components and control characters; show/season identities must be single safe
components. SSH commands quote data, and rsync uses protected arguments and the
same SSH options as movies.

Ordinary copy/publication failure removes the private stage and reports failure;
a concurrent season collision is reported as a conflict. Abrupt termination or
lost SSH may leave a hidden stage, and failed publication can leave an empty show
directory. Inspect abandoned stages before removing them manually. This tool
assumes trusted, quiescent source and destination directories: finish ripping
before running it, and do not move, replace, or modify directory ancestors during
transfer. Inventory checks detect ordinary source changes, but are not a snapshot
or protection against a malicious filesystem writer. No remote administration,
Jellyfin refresh/API, metadata service, or real-media test is performed.

TV tests add both layouts, multiple shows/seasons, identity corrections with no
guess, recursive real-rsync transfers, punctuation in names, preview immutability,
rejection/EOF, old configuration compatibility, symlinks/special files, transfer
failure, and a target created during copying. On macOS the local remote fixture
maps Linux exclusive rename to macOS `renamex_np(RENAME_EXCL)`; production still
requires Linux. CI runs the movie and TV suites on Ubuntu and macOS.
