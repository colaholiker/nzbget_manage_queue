# nzbget_manage_queue

A [NZBGet](https://nzbget.com) **scan/queue script** that prioritizes downloads
whose name contains one of a configurable list of *needles* (keywords).

When an nzb is added, its name is checked against the needle list. On a match
the download gets a higher priority and can optionally be moved to the top of
the queue. Everything else is left untouched.

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
| `MatchMode`     | `substring` (plain text) or `regex` (Python regular expr.)   | `substring` |
| `MoveToTop`     | Move matched downloads to the top of the queue (`yes` / `no`)| `no`        |
| `ApplyToQueue`  | Also re-prioritize nzbs already in the queue (`yes` / `no`)   | `no`        |
| `QueueEvents`   | Queue events to react to (comma-separated)                   | `NZB_ADDED, URL_COMPLETED` |
| `QueueSort`     | Sort the queue after priority changes                        | `priority:desc, age:asc, title:asc` |

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

### Applying to the whole queue

By default the script only affects the nzb being added. Enable `ApplyToQueue`
to also re-check the **entire download queue** on every add: the script queries
NZBGet's RPC-API (`listgroups`) and re-prioritizes all matching entries
(`editqueue`). This catches downloads that were already queued before a needle
was configured.

It uses the RPC connection settings NZBGet passes to the script
(`ControlIP`/`ControlPort`/`ControlUsername`/`ControlPassword`), so no extra
configuration is needed. If the queue can't be reached, a warning is logged and
the newly added nzb is still handled normally.

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

After the priority updates, the queue is re-sorted with `QueueSort`. The
default order is `priority:desc, age:asc, title:asc`, which keeps higher
priority items first, then older items, then alphabetical titles. Set
`QueueSort=` to disable sorting or change the field order/direction to fit
your workflow.

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
