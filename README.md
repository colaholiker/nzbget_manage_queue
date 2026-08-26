# nzbget_manage_queue

A [NZBGet](https://nzbget.com) **scan/queue script** that prioritizes downloads
whose name contains one of a configurable list of *needles* (keywords).

When an nzb is added, its name is checked against the needle list. On a match
the download gets a higher priority and can optionally be moved to the top of
the queue. Everything else is left untouched.

Independently of the needles, downloads below a configurable size can be given
the *force* priority — see [`ForceBelowMB`](#forcing-small-downloads).

## How it works

The script runs in two contexts:

- **Scan** — NZBGet calls it whenever a new file appears in the incoming nzb
  directory (web UI, RPC API, or dropped into the folder). It receives the name
  in `NZBNP_NZBNAME` and sets the priority by printing `[NZB] PRIORITY=<value>`.
- **Queue** — NZBGet calls it on queue events (`NZBNA_EVENT`). This also covers
  url downloads, whose final name is only known once the url has been fetched
  (`URL_COMPLETED`). Matching entries are re-prioritized through the RPC-API.

See the official
[scan scripts](https://nzbget.github.io/scan-scripts) and
[queue scripts](https://nzbget.github.io/queue-scripts) documentation.

## Installation

1. Copy `PrioritizeNeedles.py` into your NZBGet `ScriptDir`
   (Settings → PATHS → ScriptDir).
2. In the NZBGet web UI, open **Settings → EXTENSION SCRIPTS**. The script
   appears as `PrioritizeNeedles`.
3. Configure its options (see below).
4. Enable it as a scan script: **Settings → EXTENSION SCRIPTS → ScanScript**
   (older versions) or add it to the extension's `Scan` phase (newer versions).

Requirements: NZBGet 13.0 or later and Python 3.

## Options

| Option          | Description                                                  | Default     |
|-----------------|--------------------------------------------------------------|-------------|
| `NeedleList`    | Comma-separated needles, e.g. `1080p, ubuntu, -PROPER-`      | *(empty)*   |
| `NeedleFile`    | Path to a file with one needle per line (for longer lists)   | *(empty)*   |
| `MatchPriority` | Priority to assign on a match                                | `100`       |
| `ForceBelowMB`  | Force downloads smaller than this many MB (`0` disables)     | `0`         |
| `MatchMode`     | `substring` (plain text) or `regex` (Python regular expr.)   | `substring` |
| `MoveToTop`     | Move matched downloads to the top of the queue (`yes` / `no`)| `no`        |
| `ApplyToQueue`  | Also re-prioritize nzbs already in the queue (`yes` / `no`)   | `no`        |
| `MinInterval`   | Seconds between two full queue scans (`0` = every event)      | `0`         |
| `StateFile`     | Where the time of the last full queue scan is stored         | `${QueueDir}/PrioritizeNeedles.state` |
| `QueueEvents`   | Queue events to react to (comma-separated)                   | `NZB_ADDED, URL_COMPLETED` |

Matching **ignores case, spaces and dots** on both sides, so the needle
`some movie` matches the name `Some.Movie.1080p`. In `regex` mode the pattern is
applied to the already-normalized name (no spaces or dots), so avoid matching
those characters explicitly.

NZBGet's option fields are single-line, so for a longer list of needles use
`NeedleFile` — a plain text file with **one needle per line**. Blank lines and
lines starting with `#` are ignored. Needles from `NeedleList` and `NeedleFile`
are combined.

The path can be absolute (`/config/needles.txt`) or relative — a **relative
path is resolved against MainDir**. NZBGet directory tokens also work:
`${MainDir}`, `${ScriptDir}`, `${ConfigDir}`, `${DestDir}`, `${NzbDir}`, …
For example `${ScriptDir}/needles.txt` keeps the list next to the script.
If the file is missing, the script logs the resolved path it tried and
continues without those needles.

```
# /config/needles.txt
1080p
ubuntu
-PROPER-
S\d+E\d+
```

### Forcing small downloads

Set `ForceBelowMB` to a size in MB and every download **below** that size gets
NZBGet's `900` (*force*) priority, so it runs even while the queue is paused —
a 20 MB subtitle pack no longer waits behind a 50 GB release. No needle has to
match: this applies to every download the script looks at, and `NeedleList` /
`NeedleFile` may stay empty. If a download is both small **and** matches a
needle, force wins, being the higher of the two priorities.

`0` (the default) disables the rule and keeps the script needle-only.

Two limitations worth knowing:

- **Queue script only.** While an nzb is being *scanned*, NZBGet has not parsed
  it yet and does not report a size, so the rule cannot be applied there. Make
  sure the extension is also enabled as a **queue script** (see below); the
  scan run logs a reminder when `ForceBelowMB` is set.
- **Only downloads the script sees.** Without `ApplyToQueue` that is the nzb
  that triggered the event. Enable `ApplyToQueue` to have the whole queue
  checked against the size threshold on every event.

The size compared is the download's **total** size as reported by NZBGet, not
what is left of it — a nearly finished 50 GB download is not "small".

### Applying to the whole queue

By default the script only affects the nzb being added. Enable `ApplyToQueue`
to also re-check the **entire download queue** on every add: the script queries
NZBGet's RPC-API (`listgroups`) and re-prioritizes all matching entries
(`editqueue`). This catches downloads that were already queued before a needle
was configured — and, with `ForceBelowMB` set, all small entries already in the
queue.

It uses the RPC connection settings NZBGet passes to the script
(`ControlIP`/`ControlPort`/`ControlUsername`/`ControlPassword`), so no extra
configuration is needed. If the queue can't be reached, a warning is logged and
the newly added nzb is still handled normally.

#### Limiting how often the queue is scanned

Queue events arrive in bursts — adding a whole season fires one `NZB_ADDED` per
nzb, and each of them re-reads the entire queue and rewrites priorities the
previous scan had already set. Set `MinInterval` to a number of seconds and a
scan closer than that to the last one is skipped.

Only the scan over the whole queue is affected: the nzb that triggered the event
(or is being scanned) is still prioritized immediately, so nothing loses its
priority — an entry that was *already* queued before its needle existed just has
to wait for the next event after the interval.

The time of the last scan has to survive between runs (NZBGet starts a fresh
process per event), so it is kept in `StateFile` — a small JSON file, by default
`${QueueDir}/PrioritizeNeedles.state`, supporting the same directory tokens as
`NeedleFile`. It is only written when `MinInterval` is set. If it is missing or
unreadable the scan simply runs, so a broken state file can never disable
`ApplyToQueue` for good.

### Queue events

When enabled as a **queue script**, the extension reacts to the events listed in
`QueueEvents`. Available events:

| Event | When it fires | Notes |
|-------|---------------|-------|
| `NZB_ADDED`       | nzb added to the queue                | recommended |
| `URL_COMPLETED`   | a url download became a real nzb      | recommended (final name known) |
| `NZB_DOWNLOADED`  | nzb finished downloading              | priority no longer matters |
| `FILE_DOWNLOADED` | a single file finished                | **fires very often** |
| `NZB_DELETED`     | nzb removed from the queue            | — |
| `NZB_MARKED`      | nzb marked (dupe/good/bad/…)          | — |

For the configured events only the **triggering nzb** is re-prioritized. If
`ApplyToQueue` is also enabled, the **whole queue** is re-checked on each of
those events instead.

Enable it as a queue script under **Settings → EXTENSION SCRIPTS → QueueScript**
(or add it to the extension's `Queue` phase in newer versions).

### Priority values

NZBGet's predefined priorities: `-100` (very low), `-50` (low), `0` (normal),
`50` (high), `100` (very high), `900` (force — downloads even while NZBGet is
paused). Any integer is accepted.

## Example

With `NeedleList = 1080p, ubuntu` and `MatchPriority = 900`:

- `Ubuntu.24.04.iso.nzb` → matches `ubuntu` → priority set to `900`
- `Movie.1080p.nzb`      → matches `1080p` → priority set to `900`
- `Random.720p.nzb`      → no match → left unchanged

With `ForceBelowMB = 100` on top of that, a queued `Random.720p` of 40 MB is
forced to `900` despite matching no needle, while a 4 GB one stays untouched.

## Testing without NZBGet

The script reads its input from environment variables, so you can exercise it
from a shell:

```bash
NZBOP_SCRIPTDIR=/tmp \
NZBNP_NZBNAME="Ubuntu.24.04.1080p.iso.nzb" \
NZBPO_NEEDLELIST="1080p, debian" \
NZBPO_MOVETOTOP=yes \
python3 PrioritizeNeedles.py
```

Expected output:

```
[INFO] 'Ubuntu.24.04.1080p.iso.nzb' matched needle '1080p' - setting priority to 100.
[NZB] PRIORITY=100
[NZB] TOP=1
```

To exercise the **queue** path, set `NZBNA_*` instead of `NZBNP_*` (this talks to
a running NZBGet via RPC, so point the control vars at your instance):

```bash
NZBOP_SCRIPTDIR=/tmp \
NZBNA_EVENT=NZB_ADDED \
NZBNA_NZBID=42 \
NZBNA_NZBNAME="Ubuntu.24.04.1080p" \
NZBPO_NEEDLELIST="1080p" \
NZBOP_CONTROLIP=127.0.0.1 NZBOP_CONTROLPORT=6789 \
NZBOP_CONTROLUSERNAME=nzbget NZBOP_CONTROLPASSWORD=tegbzn6789 \
python3 PrioritizeNeedles.py
```
