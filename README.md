# nzbget_manage_queue

A [NZBGet](https://nzbget.com) **scan script** that prioritizes downloads whose
name contains one of a configurable list of *needles* (keywords).

When a new nzb is added, its name is checked against the needle list. On a match
the download gets a higher priority and can optionally be moved to the top of
the queue. Everything else is left untouched.

## How it works

NZBGet calls a scan script whenever a new file appears in the incoming nzb
directory (added via the web UI, the RPC API, or dropped into the folder). The
script receives the name in the environment variable `NZBNP_NZBNAME` and can
change the priority by printing `[NZB] PRIORITY=<value>` to standard output.

See the official [scan scripts documentation](https://nzbget.github.io/scan-scripts).

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
| `MatchPriority` | Priority to assign on a match                                | `100`       |
| `MatchMode`     | `substring` (plain text) or `regex` (Python regular expr.)   | `substring` |
| `CaseSensitive` | Match case sensitively (`yes` / `no`)                        | `no`        |
| `MoveToTop`     | Move matched downloads to the top of the queue (`yes` / `no`)| `no`        |

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
