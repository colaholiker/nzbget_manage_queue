#!/usr/bin/env python3
#
# Prioritize the Sonarr series with the fewest episodes in the download queue.
#
##############################################################################
### NZBGET QUEUE SCRIPT                                                    ###

# Prioritize the shortest Sonarr series currently downloading.
#
# On a queue event this script asks Sonarr which series the queued downloads
# belong to, then prioritizes the download(s) of the series that has the
# fewest episodes in total (the "shortest" series), so it finishes first.
#
# Series carrying a configurable Sonarr tag (default: nop) are ignored and
# never prioritized.
#
# Queue entries are mapped to Sonarr series via Sonarr's own download queue
# (matching NZBGet's NZBID against Sonarr's downloadId, with a name-based
# fallback), so no guessing from the release name is required.

##############################################################################
### OPTIONS                                                                ###

# Sonarr base URL.
#
# Include the URL base if you use one.
# Examples: http://127.0.0.1:8989   http://sonarr.local   http://host/sonarr
#SonarrUrl=http://127.0.0.1:8989

# Sonarr API key (Sonarr: Settings -> General -> Security).
#SonarrApiKey=

# Ignore series carrying this Sonarr tag (leave empty to disable).
#
# Series tagged with this label are never prioritized. Case-insensitive.
#ExcludeTag=nop

# Priority to assign to the winning series' downloads.
#
# NZBGet's predefined values: -100 (very low), -50 (low), 0 (normal),
# 50 (high), 100 (very high), 900 (force - downloads even while paused).
# Any integer is accepted.
#MatchPriority=100

# Move the prioritized downloads to the top of the queue (yes, no).
#MoveToTop=no

# Queue events to react to (comma separated).
#
# Possible events:
#   NZB_ADDED       - nzb was added to the queue (recommended);
#   URL_COMPLETED   - a url download finished and became a real nzb;
#   NZB_DOWNLOADED  - nzb finished downloading;
#   FILE_DOWNLOADED - a single file finished (fires very often!);
#   NZB_DELETED     - nzb was removed from the queue;
#   NZB_MARKED      - nzb was marked (dupe/good/bad/...).
#QueueEvents=NZB_ADDED, URL_COMPLETED

### NZBGET QUEUE SCRIPT                                                    ###
##############################################################################

import json
import os
import re
import sys
import urllib.request

# NZBGet exit codes for queue scripts.
SUCCESS = 93
ERROR = 94

# Default priority assigned to the target series (keep in sync with the
# #MatchPriority default documented in the OPTIONS section above).
DEFAULT_PRIORITY = "100"


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


def parse_list(raw):
    """Split a comma separated option, dropping blanks."""
    return [n.strip() for n in raw.split(",") if n.strip()]


def to_int(value, default=0):
    """Convert a value to int, falling back to default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize(text):
    """Lower-case and drop spaces and dots so separators don't matter."""
    return re.sub(r"[\s.]+", "", text or "").lower()


# --------------------------------------------------------------------------
# NZBGet JSON-RPC
# --------------------------------------------------------------------------

def rpc_call(method, params):
    """Call an NZBGet JSON-RPC method using the connection info NZBGet passes."""
    import base64

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


def editqueue(command, param, ids):
    """Run editqueue, trying the modern 3-arg signature then the legacy 4-arg one."""
    try:
        return rpc_call("editqueue", [command, str(param), ids])
    except Exception:  # noqa: BLE001 - fall back to the older signature
        return rpc_call("editqueue", [command, 0, str(param), ids])


def get_group_priority(group):
    """Return a queue group's current priority."""
    return to_int(group.get("Priority", group.get("MinPriority", 0)))


# --------------------------------------------------------------------------
# Sonarr API (v3)
# --------------------------------------------------------------------------

def sonarr_request(base_url, api_key, path):
    """Perform a GET against the Sonarr v3 API and return the decoded JSON."""
    url = base_url.rstrip("/") + "/api/v3" + path
    request = urllib.request.Request(url)
    request.add_header("X-Api-Key", api_key)
    request.add_header("Accept", "application/json")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def sonarr_queue_records(base_url, api_key):
    """Return Sonarr's download queue records (seriesId / downloadId / title)."""
    data = sonarr_request(
        base_url, api_key,
        "/queue?page=1&pageSize=1000&includeUnknownSeriesItems=false",
    )
    if isinstance(data, dict):
        return data.get("records") or []
    return data or []


def tag_id_for_label(tags, label):
    """Return the Sonarr tag id for a label, or None."""
    wanted = label.strip().lower()
    for tag in tags or []:
        if str(tag.get("label", "")).strip().lower() == wanted:
            return to_int(tag.get("id"), -1)
    return None


def series_total_episodes(series):
    """Total number of episodes of a series (its 'length')."""
    stats = series.get("statistics") or {}
    total = stats.get("totalEpisodeCount")
    if total is None:
        total = stats.get("episodeCount", 0)
    return to_int(total, 0)


def map_group_to_series_id(group, queue_records):
    """Find the Sonarr seriesId for an NZBGet queue group, or None.

    Primary match: NZBGet NZBID == Sonarr downloadId. Fallback: the normalized
    release name equals Sonarr's queue title.
    """
    nzbid = str(to_int(group.get("NZBID"), -1))
    for rec in queue_records:
        download_id = str(rec.get("downloadId") or "").strip()
        if download_id and download_id == nzbid:
            return to_int(rec.get("seriesId"), -1)

    name = normalize(group.get("NZBName") or group.get("NZBNicename") or "")
    if name:
        for rec in queue_records:
            if normalize(rec.get("title") or "") == name:
                return to_int(rec.get("seriesId"), -1)
    return None


# --------------------------------------------------------------------------
# Prioritizing
# --------------------------------------------------------------------------

def set_priority(name, nzbid, current_priority, priority, move_to_top):
    """Apply the target priority to a single queued nzb via RPC."""
    if current_priority == to_int(priority):
        log_detail("'%s' already has priority %s - leaving it untouched." % (name, priority))
        return
    try:
        editqueue("GroupSetPriority", priority, [nzbid])
        if move_to_top:
            editqueue("GroupMoveTop", 0, [nzbid])
    except Exception as exc:  # noqa: BLE001
        log_warning("Could not update queue entry '%s': %s" % (name, exc))
        return
    log_info("'%s' priority set to %s." % (name, priority))


def prioritize_shortest_series(base_url, api_key, exclude_tag, priority, move_to_top):
    """Prioritize the downloads of the queued Sonarr series with fewest episodes."""
    try:
        series_list = sonarr_request(base_url, api_key, "/series")
        tags = sonarr_request(base_url, api_key, "/tag")
        queue_records = sonarr_queue_records(base_url, api_key)
    except Exception as exc:  # noqa: BLE001
        log_warning("Could not query Sonarr: %s" % exc)
        return

    exclude_tag_id = tag_id_for_label(tags, exclude_tag) if exclude_tag else None
    if exclude_tag and exclude_tag_id is None:
        log_detail("Sonarr tag '%s' does not exist - no series will be excluded." % exclude_tag)

    series_by_id = {to_int(s.get("id"), -1): s for s in (series_list or []) if s.get("id") is not None}

    try:
        groups = rpc_call("listgroups", [0])
    except Exception as exc:  # noqa: BLE001
        log_warning("Could not read the NZBGet queue via RPC: %s" % exc)
        return

    # Collect one candidate per Sonarr series that has a download in the queue.
    candidates = {}
    for group in groups or []:
        nzbid = group.get("NZBID")
        name = group.get("NZBName") or group.get("NZBNicename") or ""
        if nzbid is None or not name:
            continue

        series_id = map_group_to_series_id(group, queue_records)
        if series_id is None or series_id < 0:
            log_detail("Queue entry '%s' is not linked to a Sonarr series - skipping." % name)
            continue

        series = series_by_id.get(series_id)
        if series is None:
            log_detail("Queue entry '%s' maps to unknown Sonarr series %d - skipping." % (name, series_id))
            continue

        if exclude_tag_id is not None:
            series_tags = [to_int(t, -1) for t in (series.get("tags") or [])]
            if exclude_tag_id in series_tags:
                log_detail("'%s' belongs to excluded series '%s' (tag '%s') - skipping."
                           % (name, series.get("title", "?"), exclude_tag))
                continue

        entry = candidates.setdefault(series_id, {
            "title": series.get("title", "?"),
            "total": series_total_episodes(series),
            "groups": [],
        })
        entry["groups"].append((to_int(nzbid, -1), name, get_group_priority(group)))

    if not candidates:
        log_detail("No queued download could be matched to an eligible Sonarr series.")
        return

    # Winner: fewest episodes in total; ties broken alphabetically by series title.
    winner_id = min(candidates, key=lambda sid: (candidates[sid]["total"], candidates[sid]["title"].lower()))
    winner = candidates[winner_id]
    log_info("Target series: '%s' (%d episode(s) total) - prioritizing %d download(s)."
             % (winner["title"], winner["total"], len(winner["groups"])))

    for nzbid, name, current_priority in winner["groups"]:
        set_priority(name, nzbid, current_priority, priority, move_to_top)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def handle_queue(base_url, api_key, exclude_tag, priority, move_to_top):
    """QUEUE context: react to a queue event."""
    event = os.environ.get("NZBNA_EVENT", "")
    wanted = [e.upper() for e in parse_list(get_option("QueueEvents", "NZB_ADDED, URL_COMPLETED"))]
    if event.upper() not in wanted:
        log_detail("Ignoring queue event '%s' (not in QueueEvents)." % event)
        return SUCCESS

    prioritize_shortest_series(base_url, api_key, exclude_tag, priority, move_to_top)
    return SUCCESS


def main():
    # Ensure we are running inside NZBGet.
    if "NZBOP_SCRIPTDIR" not in os.environ:
        print("This script is supposed to be called from NZBGet (13.0 or later).")
        return ERROR

    base_url = get_option("SonarrUrl", "http://127.0.0.1:8989").strip()
    api_key = get_option("SonarrApiKey").strip()
    if not base_url or not api_key:
        log_error("SonarrUrl and SonarrApiKey must be configured.")
        return ERROR

    exclude_tag = get_option("ExcludeTag", "nop").strip()

    priority = get_option("MatchPriority", DEFAULT_PRIORITY).strip()
    try:
        priority = str(int(priority))
    except ValueError:
        log_warning("Invalid MatchPriority '%s', using %s." % (priority, DEFAULT_PRIORITY))
        priority = DEFAULT_PRIORITY

    move_to_top = get_bool_option("MoveToTop", False)

    if "NZBNA_EVENT" in os.environ:
        return handle_queue(base_url, api_key, exclude_tag, priority, move_to_top)

    log_error("Unknown context: queue variables (NZBNA_) not set. This is a QUEUE script.")
    return ERROR


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - report any failure to NZBGet
        log_error("Unexpected error: %s" % exc)
        sys.exit(ERROR)
