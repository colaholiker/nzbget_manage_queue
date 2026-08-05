#!/usr/bin/env python3
#
# Prioritize downloads whose name contains a configured needle.
#
##############################################################################
### NZBGET SCAN/QUEUE SCRIPT                                               ###

# Prioritize downloads matching a list of needles.
#
# When an nzb is added, its name is checked against a configurable list of
# needles (keywords). If any needle is found in the name, the download is
# given a higher priority (and optionally moved to the top of the queue).
#
# Independently of the needles, downloads below a configurable size can be
# given NZBGet's "force" priority (see ForceBelowMB), so a small download
# never has to wait behind a huge one.
#
# Runs both as a SCAN script (on the nzb being added) and as a QUEUE script
# (on queue events like NZB_ADDED / URL_COMPLETED, also covering url downloads).

##############################################################################
### OPTIONS                                                                ###

# List of needles (comma separated).
#
# Downloads whose name contains any of these needles are prioritized.
# Example: 1080p, ubuntu, -PROPER-
# For longer lists use NeedleFile instead (or in addition).
#NeedleList=

# Path to a file with needles, one per line.
#
# Use this for longer lists. Blank lines and lines starting with '#' are
# ignored. Needles from NeedleList and NeedleFile are combined.
#
# A relative path is resolved against MainDir. NZBGet directory tokens are
# supported: ${MainDir}, ${ScriptDir}, ${ConfigDir}, ${DestDir}, ${NzbDir}, ...
# Examples: needles.txt   ${ScriptDir}/needles.txt   /config/needles.txt
#NeedleFile=

# Priority to assign on a match.
#
# NZBGet's predefined values: -100 (very low), -50 (low), 0 (normal),
# 50 (high), 100 (very high), 900 (force - downloads even while paused).
# Any integer is accepted.
#MatchPriority=100

# Force downloads smaller than this many MB (0 disables).
#
# A download whose total size is below this threshold gets NZBGet's "force"
# priority (900), so it downloads even while the queue is paused. No needle
# has to match - every download the script looks at is checked. Being small
# wins over a needle match, force being the higher priority of the two.
#
# This only works while running as a QUEUE script: when an nzb is scanned its
# size is not known yet.
#ForceBelowMB=0

# How needles are matched against the name (substring, regex).
#
#  substring - the needle is a plain text fragment (default);
#  regex     - the needle is a Python regular expression.
#
# Matching always ignores case, spaces and dots on both sides, so the needle
# "some movie" matches the name "Some.Movie.1080p".
#MatchMode=substring

# Move matched downloads to the top of the queue (yes, no).
#MoveToTop=no

# Also apply to nzbs already in the queue (yes, no).
#
# When enabled, every time a new nzb is added the whole download queue is
# re-checked via NZBGet's RPC-API and all matching entries are (re-)prioritized.
# This lets you catch downloads that were already queued before a needle was
# added. Uses the RPC connection settings NZBGet passes to the script.
#ApplyToQueue=no

# Queue events to react to (comma separated).
#
# Only used when the script runs as a QUEUE script. Possible events:
#   NZB_ADDED       - nzb was added to the queue (recommended);
#   URL_COMPLETED   - a url download finished and became a real nzb (recommended);
#   NZB_DOWNLOADED  - nzb finished downloading;
#   FILE_DOWNLOADED - a single file finished (fires very often!);
#   NZB_DELETED     - nzb was removed from the queue;
#   NZB_MARKED      - nzb was marked (dupe/good/bad/...).
#
# For most events only the triggering nzb is (re-)prioritized. Enable
# ApplyToQueue to instead re-check the whole queue on each of these events.
#QueueEvents=NZB_ADDED, URL_COMPLETED

### NZBGET SCAN/QUEUE SCRIPT                                               ###
##############################################################################

import base64
import json
import os
import re
import sys
import urllib.request

# NZBGet exit codes for scan scripts.
SUCCESS = 93
ERROR = 94

# Default priority assigned on a match (keep in sync with the #MatchPriority
# default documented in the OPTIONS section above).
DEFAULT_PRIORITY = "100"

# NZBGet's "force" priority - such downloads run even while the queue is
# paused. Assigned to downloads below ForceBelowMB.
FORCE_PRIORITY = "900"


def log_info(message):
    print("[INFO] %s" % message)


def log_detail(message):
    print("[DETAIL] %s" % message)


def log_warning(message):
    print("[WARNING] %s" % message)


def log_error(message):
    print("[ERROR] %s" % message)


def get_option(name, default=""):
    """Read a script option (env var NZBPO_<NAME>)."""
    return os.environ.get("NZBPO_" + name.upper(), default)


def get_bool_option(name, default=False):
    value = get_option(name, "yes" if default else "no").strip().lower()
    return value in ("yes", "true", "1", "on")


def parse_needles(raw):
    """Split the comma separated needle list, dropping blanks."""
    return [n.strip() for n in raw.split(",") if n.strip()]


def resolve_path(path):
    """Resolve a NeedleFile path.

    Supports NZBGet directory tokens (${MainDir}, ${ScriptDir}, ${ConfigDir},
    ${DestDir}, ...) and normal environment variables. A relative path is
    resolved against MainDir.
    """
    if not path:
        return path

    tokens = {
        "MainDir": os.environ.get("NZBOP_MAINDIR", ""),
        "DestDir": os.environ.get("NZBOP_DESTDIR", ""),
        "InterDir": os.environ.get("NZBOP_INTERDIR", ""),
        "NzbDir": os.environ.get("NZBOP_NZBDIR", ""),
        "QueueDir": os.environ.get("NZBOP_QUEUEDIR", ""),
        "TempDir": os.environ.get("NZBOP_TEMPDIR", ""),
        "ScriptDir": os.environ.get("NZBOP_SCRIPTDIR", ""),
        # NZBGet exposes the config file, not its directory.
        "ConfigDir": os.path.dirname(os.environ.get("NZBOP_CONFIGFILE", "")),
    }
    for name, value in tokens.items():
        if value:
            path = path.replace("${%s}" % name, value)

    path = os.path.expanduser(os.path.expandvars(path))

    # Relative paths are taken relative to MainDir (fall back to CWD).
    if not os.path.isabs(path) and tokens["MainDir"]:
        path = os.path.join(tokens["MainDir"], path)

    return os.path.normpath(path)


def read_needle_file(path):
    """Read needles from a file, one per line.

    Blank lines and lines starting with '#' are ignored.
    """
    if not path:
        return []
    resolved = resolve_path(path)
    if not os.path.isfile(resolved):
        log_warning("NeedleFile not found: '%s' (from '%s') - ignoring." % (resolved, path))
        return []
    needles = []
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    needles.append(stripped)
    except OSError as exc:
        log_warning("Could not read NeedleFile '%s': %s" % (resolved, exc))
    return needles


def normalize(text):
    """Lower-case and drop spaces and dots so separators don't matter."""
    return re.sub(r"[\s.]+", "", text).lower()


def name_matches(nzb_name, needles, mode):
    """Return the first needle that matches, or None.

    Matching ignores case, spaces and dots on both sides.
    """
    haystack = normalize(nzb_name)
    for needle in needles:
        if mode == "regex":
            try:
                if re.search(needle, haystack, re.IGNORECASE):
                    return needle
            except re.error as exc:
                log_warning("Skipping invalid regex needle '%s': %s" % (needle, exc))
        else:  # substring
            if normalize(needle) in haystack:
                return needle
    return None


def rpc_call(method, params):
    """Call an NZBGet JSON-RPC method using the connection info NZBGet passes."""
    host = os.environ.get("NZBOP_CONTROLIP", "127.0.0.1")
    if host in ("0.0.0.0", ""):
        host = "127.0.0.1"
    port = os.environ.get("NZBOP_CONTROLPORT", "6789")
    username = os.environ.get("NZBOP_CONTROLUSERNAME", "")
    password = os.environ.get("NZBOP_CONTROLPASSWORD", "")

    url = "http://%s:%s/jsonrpc" % (host, port)
    body = json.dumps({"method": method, "params": params, "id": 1}).encode("utf-8")
    request = urllib.request.Request(url, data=body)
    request.add_header("Content-Type", "application/json")
    if username or password:
        token = base64.b64encode(("%s:%s" % (username, password)).encode("utf-8")).decode("ascii")
        request.add_header("Authorization", "Basic " + token)

    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload.get("result")


def to_int(value, default=0):
    """Convert a value to int, falling back to default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def editqueue(command, param, ids):
    """Run editqueue, trying the modern 3-arg signature then the legacy 4-arg one."""
    try:
        return rpc_call("editqueue", [command, str(param), ids])
    except Exception:  # noqa: BLE001 - fall back to the older signature
        return rpc_call("editqueue", [command, 0, str(param), ids])


def get_group_priority(group):
    """Return a queue group's current priority."""
    return to_int(group.get("Priority", group.get("MinPriority", 0)))


def get_group_size_mb(group):
    """Return a queue group's total size in MB, or 0 if NZBGet doesn't report it.

    The exact size comes as two 32-bit halves; 'FileSizeMB' is truncated and
    reports 0 for anything below 1 MB, which would make a tiny download look
    like one whose size is still unknown.
    """
    if "FileSizeLo" in group or "FileSizeHi" in group:
        size = (to_int(group.get("FileSizeHi"), 0) << 32) + to_int(group.get("FileSizeLo"), 0)
        return size / (1024.0 * 1024.0)
    return float(to_int(group.get("FileSizeMB"), 0))


def target_priority(group, needle, priority, force_below_mb):
    """Decide which priority a queue group should get.

    Returns a (priority, reason) pair, or (None, None) when the group is none
    of our business. A small download is forced even without a needle match;
    force being the higher priority, it also wins over one.
    """
    if force_below_mb > 0:
        size_mb = get_group_size_mb(group)
        # A size of 0 means NZBGet doesn't know it yet - that is not "small".
        if 0 < size_mb < force_below_mb:
            return FORCE_PRIORITY, "is only %.1f MB (below ForceBelowMB %d)" % (size_mb, force_below_mb)
    if needle is not None:
        return priority, "matched needle '%s'" % needle
    return None, None


def get_group_by_nzbid(nzbid):
    """Return the queue group for a specific NZBID, or None."""
    try:
        groups = rpc_call("listgroups", [0])
    except Exception as exc:  # noqa: BLE001
        log_warning("Could not read the queue via RPC: %s" % exc)
        return None

    for group in groups or []:
        if to_int(group.get("NZBID"), -1) == nzbid:
            return group
    return None


def apply_to_queue(needles, mode, priority, move_to_top, force_below_mb):
    """Re-prioritize every eligible nzb already present in the download queue."""
    try:
        groups = rpc_call("listgroups", [0])
    except Exception as exc:  # noqa: BLE001
        log_warning("Could not read the queue via RPC: %s" % exc)
        return

    count = 0
    for group in groups or []:
        name = group.get("NZBName") or group.get("NZBNicename") or ""
        nzbid = group.get("NZBID")
        if not name or nzbid is None:
            continue
        needle = name_matches(name, needles, mode)
        target, reason = target_priority(group, needle, priority, force_below_mb)
        if target is None:
            continue
        current_priority = get_group_priority(group)
        if current_priority == to_int(target):
            log_detail("Queue: '%s' already has priority %s - leaving it untouched." % (name, target))
            continue
        try:
            editqueue("GroupSetPriority", target, [nzbid])
            if move_to_top:
                editqueue("GroupMoveTop", 0, [nzbid])
        except Exception as exc:  # noqa: BLE001
            log_warning("Could not update queue entry '%s': %s" % (name, exc))
            continue
        count += 1
        log_info("Queue: '%s' %s - priority set to %s." % (name, reason, target))

    log_detail("Queue scan finished - %d entr%s updated." % (count, "y" if count == 1 else "ies"))


def handle_scan(needles, mode, priority, move_to_top, force_below_mb):
    """SCAN context: influence the nzb being added via stdout commands."""
    nzb_name = os.environ["NZBNP_NZBNAME"]

    # 1. Handle the nzb currently being added. ForceBelowMB cannot be applied
    #    here - while an nzb is scanned NZBGet does not know its size yet.
    if force_below_mb > 0:
        log_detail("ForceBelowMB is only applied by the queue script - the size of "
                   "'%s' is not known while scanning." % nzb_name)
    matched = name_matches(nzb_name, needles, mode)
    if matched is None:
        log_detail("No needle matched '%s'." % nzb_name)
    else:
        log_info("'%s' matched needle '%s' - setting priority to %s." % (nzb_name, matched, priority))
        # Tell NZBGet to change the priority of the nzb being added.
        print("[NZB] PRIORITY=%s" % priority)
        if move_to_top:
            log_detail("Moving '%s' to the top of the queue." % nzb_name)
            print("[NZB] TOP=1")

    # 2. Optionally re-prioritize nzbs already in the queue (via RPC).
    if get_bool_option("ApplyToQueue", False):
        apply_to_queue(needles, mode, priority, move_to_top, force_below_mb)

    return SUCCESS


def prioritize_nzbid(name, nzbid, needle, priority, move_to_top, force_below_mb):
    """Set the priority of a single queued nzb via RPC."""
    group = get_group_by_nzbid(nzbid)
    if group is None:
        # Without the group there is no size to check, so only a needle match
        # can still be acted upon.
        if needle is None:
            log_warning("Could not read queue entry '%s' - cannot check its size." % name)
            return
        log_warning("Could not determine current priority for queue entry '%s'." % name)
        editqueue("GroupSetPriority", priority, [nzbid])
        return

    target, reason = target_priority(group, needle, priority, force_below_mb)
    if target is None:
        log_detail("No needle matched '%s'." % name)
        return

    current_priority = get_group_priority(group)
    if current_priority == to_int(target):
        log_detail("Queue: '%s' %s but already has priority %s - leaving it untouched." % (name, reason, target))
        return

    log_info("Queue: '%s' %s - priority set to %s." % (name, reason, target))
    editqueue("GroupSetPriority", target, [nzbid])
    if move_to_top:
        editqueue("GroupMoveTop", 0, [nzbid])


def handle_queue(needles, mode, priority, move_to_top, force_below_mb):
    """QUEUE context: react to a queue event."""
    event = os.environ.get("NZBNA_EVENT", "")
    wanted = [e.upper() for e in parse_needles(get_option("QueueEvents", "NZB_ADDED, URL_COMPLETED"))]
    if event.upper() not in wanted:
        log_detail("Ignoring queue event '%s' (not in QueueEvents)." % event)
        return SUCCESS

    # ApplyToQueue re-checks the entire queue; otherwise only the event's nzb.
    if get_bool_option("ApplyToQueue", False):
        apply_to_queue(needles, mode, priority, move_to_top, force_below_mb)
        return SUCCESS

    name = os.environ.get("NZBNA_NZBNAME", "")
    nzbid = os.environ.get("NZBNA_NZBID", "")
    if not name or not nzbid:
        log_detail("Queue event '%s' without name/id - nothing to do." % event)
        return SUCCESS

    # An unmatched name is only worth a queue lookup when its size can still
    # earn it the force priority.
    needle = name_matches(name, needles, mode)
    if needle is None and force_below_mb <= 0:
        log_detail("No needle matched '%s'." % name)
        return SUCCESS

    try:
        prioritize_nzbid(name, int(nzbid), needle, priority, move_to_top, force_below_mb)
    except Exception as exc:  # noqa: BLE001
        log_warning("Could not update queue entry '%s': %s" % (name, exc))
    return SUCCESS


def main():
    # Ensure we are running inside NZBGet.
    if "NZBOP_SCRIPTDIR" not in os.environ:
        print("This script is supposed to be called from NZBGet (13.0 or later).")
        return ERROR

    needles = parse_needles(get_option("NeedleList"))
    needles.extend(read_needle_file(get_option("NeedleFile").strip()))

    force_below_mb = to_int(get_option("ForceBelowMB", "0").strip(), 0)
    if force_below_mb < 0:
        force_below_mb = 0

    # Without needles the script still has work to do as long as small
    # downloads are to be forced.
    if not needles and force_below_mb <= 0:
        log_detail("No needles configured (NeedleList/NeedleFile) and ForceBelowMB disabled"
                   " - nothing to prioritize.")
        return SUCCESS

    mode = get_option("MatchMode", "substring").strip().lower()
    if mode not in ("substring", "regex"):
        log_warning("Unknown MatchMode '%s', falling back to 'substring'." % mode)
        mode = "substring"

    priority = get_option("MatchPriority", DEFAULT_PRIORITY).strip()
    try:
        priority = str(int(priority))
    except ValueError:
        log_warning("Invalid MatchPriority '%s', using %s." % (priority, DEFAULT_PRIORITY))
        priority = DEFAULT_PRIORITY

    move_to_top = get_bool_option("MoveToTop", False)
    # Dispatch by context: SCAN sets NZBNP_*, QUEUE sets NZBNA_*.
    if "NZBNP_NZBNAME" in os.environ:
        return handle_scan(needles, mode, priority, move_to_top, force_below_mb)
    if "NZBNA_EVENT" in os.environ:
        return handle_queue(needles, mode, priority, move_to_top, force_below_mb)

    log_error("Unknown context: neither scan (NZBNP_) nor queue (NZBNA_) variables set.")
    return ERROR


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - report any failure to NZBGet
        log_error("Unexpected error: %s" % exc)
        sys.exit(ERROR)
