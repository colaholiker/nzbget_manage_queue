#!/usr/bin/env python3
#
# Prioritize one Sonarr series in the download queue (shortest one by default).
#
##############################################################################
### NZBGET QUEUE SCRIPT                                                    ###

# Prioritize one of the Sonarr series currently downloading (shortest first).
#
# On a queue event this script asks Sonarr which series the queued downloads
# belong to, then prioritizes the download(s) of the series that comes first in
# the configured sort order (by default the series with the fewest episodes in
# total, the "shortest" one), so it finishes first.
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

# Seconds to wait for a single Sonarr/NZBGet request (minimum 1).
#
# Queue scripts run synchronously: while this script waits for a socket,
# NZBGet's queue stands still. Keep this short so an unreachable Sonarr does
# not look like a frozen NZBGet.
#RequestTimeout=10

# Seconds this script may spend in total before it gives up (0 = no limit).
#
# Upper bound for how long NZBGet can be blocked by this script, across all
# requests. When the budget runs out the queue is left as it is.
#TotalTimeout=30

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

# Order in which the target series is picked (comma separated keys).
#
# The first key decides, the following ones break ties. Prefix a key with "-"
# to reverse it (biggest/last first). Known keys:
#   episodes  - number of episodes the series has in total;
#   remaining - MB the series still has to download;
#   size      - total MB of the series' queued downloads;
#   downloads - number of queued downloads of the series;
#   year      - year the series started;
#   title     - series title (see IgnoreLeadingArticles).
#
# Examples: episodes, title       - shortest series first (default);
#           remaining, title      - series closest to being finished first;
#           -year, episodes       - newest series first;
#           -size, title          - biggest download first.
#SortOrder=episodes, title

# Ignore leading articles when sorting series alphabetically (yes, no).
#
# When series are sorted by title, a leading article is stripped first, so
# "The Boys" sorts under B and "Der Tatortreiniger" under T.
#IgnoreLeadingArticles=yes

# Leading articles to ignore (comma separated, case-insensitive).
#
# Only used when IgnoreLeadingArticles is enabled.
#SortArticles=the, a, an, der, die, das, den, dem, des

# Skip series with less than this many MB left to download.
#
# A series whose queued downloads together have less than this much remaining
# (but more than nothing) is considered almost finished and is skipped in
# favour of the next shortest series. Set to 0 to disable.
#MinRemainingMB=10

# Maximum number of series kept on the boost priority at the same time.
#
# The target series always keeps the boost; the remaining slots go to the
# almost finished series (see MinRemainingMB) closest to the finish line.
# Everything else is reset to normal priority. Set to 0 for no limit.
#MaxPrioritizedSeries=3

# Maximum number of downloads kept on the boost priority at the same time.
#
# MaxPrioritizedSeries counts series, not downloads - a single series with a
# whole season queued can still put the boost on dozens of entries. This is the
# hard cap on the individual queue entries: the target series' downloads are
# filled in first (in queue order), then those of the almost finished series.
# Downloads beyond the cap are reset to normal priority. Set to 0 for no limit.
#MaxPrioritizedDownloads=0

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
import time
import urllib.error
import urllib.request

#  NZBGet exit codes for queue scripts.
SUCCESS = 93
ERROR = 94

# Default priority assigned to the target series (keep in sync with the
# #MatchPriority default documented in the OPTIONS section above).
DEFAULT_PRIORITY = "100"

# NZBGet's "normal" priority - other eligible series are reset to this.
NORMAL_PRIORITY = "0"

# Default limit of simultaneously prioritized series (keep in sync with the
# #MaxPrioritizedSeries default documented in the OPTIONS section above).
DEFAULT_MAX_PRIORITIZED = "3"

# Default limit of simultaneously prioritized downloads, 0 = no limit (keep in
# sync with the #MaxPrioritizedDownloads default documented above).
DEFAULT_MAX_PRIORITIZED_DOWNLOADS = "0"

# Group statuses that still compete for bandwidth. Everything else (post-
# processing, par-repair, unpacking, ...) has already been downloaded, so its
# priority is meaningless and it must not be mistaken for "almost finished".
ACTIVE_STATUSES = ("QUEUED", "PAUSED", "DOWNLOADING", "FETCHING")

# Default socket and overall timeouts (keep in sync with the #RequestTimeout
# and #TotalTimeout defaults documented in the OPTIONS section above).
DEFAULT_REQUEST_TIMEOUT = "10"
DEFAULT_TOTAL_TIMEOUT = "30"


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


# Default leading articles ignored when sorting titles (English/German).
DEFAULT_SORT_ARTICLES = "the, a, an, der, die, das, den, dem, des"


def sort_title(text, articles):
    """Return a lower-cased title with a leading article stripped for sorting.

    ``articles`` is a set of lower-cased words; if empty, nothing is stripped.
    """
    title = (text or "").strip().lower()
    if not articles:
        return title
    match = re.match(r"^(\w+)\s+(.*)$", title)
    if match and match.group(1) in articles:
        return match.group(2)
    return title


# Sort keys usable in the SortOrder option, mapped to the candidate field they
# read (keep in sync with the #SortOrder documentation above).
SORT_KEYS = {
    "episodes": "total",
    "remaining": "remaining",
    "size": "size",
    "downloads": "downloads",
    "year": "year",
    "title": "sortname",
}

# Default order: shortest series first, ties broken alphabetically (keep in
# sync with the #SortOrder default documented in the OPTIONS section above).
DEFAULT_SORT_ORDER = "episodes, title"


def parse_sort_order(raw):
    """Parse SortOrder into a list of (key, reverse) pairs.

    Unknown keys are dropped with a warning; if nothing usable is left the
    default order is used.
    """
    keys = []
    for token in parse_list(raw):
        reverse = token.startswith("-")
        name = token.lstrip("+-").strip().lower()
        if name not in SORT_KEYS:
            log_warning("Unknown SortOrder key '%s' - ignoring it." % token)
            continue
        keys.append((name, reverse))
    if not keys:
        if raw.strip():
            log_warning("No usable SortOrder key in '%s', using '%s'." % (raw, DEFAULT_SORT_ORDER))
        return [(name, False) for name in parse_list(DEFAULT_SORT_ORDER)]
    return keys


def describe_sort_order(sort_keys):
    """Render the parsed sort order the way it is written in the option."""
    return ", ".join(("-" if reverse else "") + name for name, reverse in sort_keys)


def sort_candidates(candidates, sort_keys):
    """Return the candidate ids ordered by the configured sort keys.

    Sorting runs once per key from the least to the most significant one; since
    Python's sort is stable that yields the same result as one combined key,
    but it also allows reversing individual keys (which negating cannot do for
    titles).
    """
    ordered = list(candidates)
    for name, reverse in reversed(sort_keys):
        field = SORT_KEYS[name]
        ordered.sort(key=lambda sid, f=field: candidates[sid][f], reverse=reverse)
    return ordered


# --------------------------------------------------------------------------
# Time budget
# --------------------------------------------------------------------------
#
# NZBGet calls queue scripts synchronously - it waits for us before it carries
# on with the queue. Every second spent waiting for a socket therefore looks
# like a hung NZBGet, and an unreachable Sonarr used to block it for a full
# minute per request. Every request is capped individually and all of them
# together, so the worst case is bounded no matter what fails.

_request_timeout = to_int(DEFAULT_REQUEST_TIMEOUT)
_total_timeout = 0
_deadline = None


class TimeBudgetExceeded(Exception):
    """Raised when the script has used up its total time budget."""


def start_time_budget(request_timeout, total_timeout):
    """Arm the per-request timeout and the overall deadline."""
    global _request_timeout, _total_timeout, _deadline
    _request_timeout = max(1, request_timeout)
    _total_timeout = max(0, total_timeout)
    _deadline = time.monotonic() + _total_timeout if _total_timeout > 0 else None


def next_timeout():
    """Timeout for the next request, capped by what is left of the budget."""
    if _deadline is None:
        return _request_timeout
    left = _deadline - time.monotonic()
    if left <= 0:
        raise TimeBudgetExceeded(
            "TotalTimeout of %d second(s) is used up" % _total_timeout)
    return min(_request_timeout, left)


def is_unreachable(exc):
    """True if the error means the other side never answered.

    Timeouts, refused connections and DNS failures are all fatal for this run:
    repeating the call would only block NZBGet twice as long. HTTPError is a
    URLError, but it means we did get an answer, so it is not counted here.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(exc, (OSError, TimeBudgetExceeded))


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

    with urllib.request.urlopen(request, timeout=next_timeout()) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload.get("result")


# Set once we know this NZBGet only accepts the legacy editqueue signature, so
# the probe below is paid at most once per run.
_editqueue_legacy = False


def editqueue(command, param, ids):
    """Run editqueue, trying the modern 3-arg signature then the legacy 4-arg one.

    The fallback is only taken when NZBGet actually answered and rejected the
    call. After a timeout or a refused connection a second attempt would just
    double the time NZBGet is blocked by this script.
    """
    global _editqueue_legacy

    if _editqueue_legacy:
        return rpc_call("editqueue", [command, 0, str(param), ids])
    try:
        return rpc_call("editqueue", [command, str(param), ids])
    except Exception as exc:  # noqa: BLE001 - maybe just the wrong signature
        if is_unreachable(exc):
            raise
    result = rpc_call("editqueue", [command, 0, str(param), ids])
    _editqueue_legacy = True
    return result


def get_group_priority(group):
    """Return a queue group's current priority.

    listgroups reports the group priority as 'MaxPriority' ("Max" has purely
    historical reasons); 'MinPriority' is deprecated and reports something
    lower as soon as the group's files carry mixed priorities - which used to
    hide already boosted groups from the reset below.
    """
    for key in ("MaxPriority", "Priority", "MinPriority"):
        if key in group:
            return to_int(group.get(key), 0)
    return 0


def is_active_group(group):
    """True if the group is still waiting for (or using) bandwidth."""
    status = str(group.get("Status") or "").strip().upper()
    return not status or status in ACTIVE_STATUSES


# --------------------------------------------------------------------------
# Sonarr API (v3)
# --------------------------------------------------------------------------

def sonarr_request(base_url, api_key, path):
    """Perform a GET against the Sonarr v3 API and return the decoded JSON."""
    url = base_url.rstrip("/") + "/api/v3" + path
    request = urllib.request.Request(url)
    request.add_header("X-Api-Key", api_key)
    request.add_header("Accept", "application/json")
    with urllib.request.urlopen(request, timeout=next_timeout()) as response:
        return json.loads(response.read().decode("utf-8"))


# Paging for Sonarr's queue endpoint. Big NZBGet queues can hold more entries
# than a single page returns; unfetched records stay unmapped, which used to
# leave their downloads out of the reset below.
SONARR_QUEUE_PAGE_SIZE = 500
SONARR_QUEUE_MAX_PAGES = 100


def sonarr_queue_records(base_url, api_key):
    """Return Sonarr's download queue records (seriesId / downloadId / title)."""
    records = []
    page = 1
    while page <= SONARR_QUEUE_MAX_PAGES:
        data = sonarr_request(
            base_url, api_key,
            "/queue?page=%d&pageSize=%d&includeUnknownSeriesItems=false"
            % (page, SONARR_QUEUE_PAGE_SIZE),
        )
        if isinstance(data, dict):
            batch = data.get("records") or []
            total = to_int(data.get("totalRecords"), 0)
        else:
            batch = data or []
            total = len(batch)
        records.extend(batch)
        if len(batch) < SONARR_QUEUE_PAGE_SIZE or len(records) >= total:
            break
        page += 1
    else:
        log_warning("Sonarr queue has more than %d records - ignoring the rest."
                    % (SONARR_QUEUE_PAGE_SIZE * SONARR_QUEUE_MAX_PAGES))
    return records


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
    except TimeBudgetExceeded:
        raise  # out of time - stop instead of blocking NZBGet any longer
    except Exception as exc:  # noqa: BLE001
        log_warning("Could not update queue entry '%s': %s" % (name, exc))
        return
    log_info("'%s' priority set to %s." % (name, priority))


def prioritize_target_series(base_url, api_key, exclude_tag, priority, move_to_top, min_remaining_mb,
                             sort_articles, sort_keys, max_prioritized, max_downloads):
    """Prioritize the downloads of the first queued Sonarr series in sort order."""
    try:
        series_list = sonarr_request(base_url, api_key, "/series")
        tags = sonarr_request(base_url, api_key, "/tag")
        queue_records = sonarr_queue_records(base_url, api_key)
    except TimeBudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        log_warning("Could not query Sonarr at %s: %s - leaving the queue as it is."
                    % (base_url.rstrip("/"), exc))
        return

    exclude_tag_id = tag_id_for_label(tags, exclude_tag) if exclude_tag else None
    if exclude_tag and exclude_tag_id is None:
        log_detail("Sonarr tag '%s' does not exist - no series will be excluded." % exclude_tag)

    series_by_id = {to_int(s.get("id"), -1): s for s in (series_list or []) if s.get("id") is not None}

    try:
        groups = rpc_call("listgroups", [0])
    except TimeBudgetExceeded:
        raise
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

        # Already downloaded entries (post-processing, par-repair, ...) don't
        # care about their priority and have 0 MB left, which would make their
        # series look almost finished and boost it for nothing.
        if not is_active_group(group):
            log_detail("'%s' is no longer downloading (%s) - skipping."
                       % (name, group.get("Status") or "?"))
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

        title = series.get("title", "?")
        entry = candidates.setdefault(series_id, {
            "title": title,
            "sortname": sort_title(title, sort_articles),
            "total": series_total_episodes(series),
            "year": to_int(series.get("year"), 0),
            "remaining": 0,
            "size": 0,
            "downloads": 0,
            "groups": [],
        })
        # Ignore paused parts (e.g. par2 files NZBGet only fetches on demand);
        # count only what will actually be downloaded.
        remaining_mb = to_int(group.get("RemainingSizeMB"), 0)
        paused_mb = to_int(group.get("PausedSizeMB"), 0)
        active_mb = max(0, remaining_mb - paused_mb)
        entry["remaining"] += active_mb
        entry["size"] += to_int(group.get("FileSizeMB"), 0)
        entry["downloads"] += 1
        entry["groups"].append((to_int(nzbid, -1), name, get_group_priority(group)))
        log_detail("'%s' -> series '%s': %d MB to download (%d MB total, %d MB paused)."
                   % (name, entry["title"], active_mb, remaining_mb, paused_mb))

    if not candidates:
        log_detail("No queued download could be matched to an eligible Sonarr series.")
        return

    # Order the eligible series by the configured SortOrder.
    ordered = sort_candidates(candidates, sort_keys)
    log_detail("Series by SortOrder (%s): %s."
               % (describe_sort_order(sort_keys),
                  ", ".join("'%s'" % candidates[sid]["title"] for sid in ordered)))

    # A series with 0 MB to fetch (everything paused) gains nothing from a
    # boost: it is neither the target nor almost finished, it is just idle.
    def has_work(sid):
        return candidates[sid]["remaining"] > 0

    # Almost finished series stay boosted so they cross the finish line,
    # closest to the finish line first.
    if min_remaining_mb > 0:
        almost_done = sorted(
            (sid for sid in candidates if has_work(sid) and candidates[sid]["remaining"] < min_remaining_mb),
            key=lambda sid: candidates[sid]["remaining"],
        )
    else:
        almost_done = []
    almost_done_set = set(almost_done)

    # The target: the first series in sort order that still has real work left.
    winner_id = next((sid for sid in ordered if has_work(sid) and sid not in almost_done_set), None)

    # Keep the boost on a bounded set of series - the target plus as many
    # almost finished ones as MaxPrioritizedSeries allows. Boosting everything
    # at once (which a long queue used to do) is the same as boosting nothing.
    prioritized = [winner_id] if winner_id is not None else []
    room = len(almost_done) if max_prioritized <= 0 else max(0, max_prioritized - len(prioritized))
    prioritized.extend(almost_done[:room])

    dropped = almost_done[room:]
    if dropped:
        log_detail("MaxPrioritizedSeries=%d reached - %d almost finished series not boosted: %s."
                   % (max_prioritized, len(dropped),
                      ", ".join("'%s'" % candidates[sid]["title"] for sid in dropped)))

    # Flatten the boosted series into the individual queue entries, target
    # first. MaxPrioritizedSeries counts series, so a single series with a whole
    # season queued could still put the boost on the entire queue - this is the
    # cap on the downloads themselves. Within a series the queue order wins,
    # which is the order NZBGet would have fetched them in anyway.
    boosted = [(sid, group) for sid in prioritized for group in candidates[sid]["groups"]]
    if max_downloads > 0 and len(boosted) > max_downloads:
        skipped = boosted[max_downloads:]
        boosted = boosted[:max_downloads]
        log_detail("MaxPrioritizedDownloads=%d reached - %d download(s) not boosted: %s."
                   % (max_downloads, len(skipped),
                      ", ".join("'%s'" % name for _, (_, name, _) in skipped)))
    boosted_ids = {nzbid for _, (nzbid, _, _) in boosted}

    if winner_id is not None:
        winner = candidates[winner_id]
        log_info("Target series: '%s' (%d episode(s) total, %d MB left) - prioritizing %d of %d download(s)."
                 % (winner["title"], winner["total"], winner["remaining"],
                    sum(1 for sid, _ in boosted if sid == winner_id), len(winner["groups"])))

    # The target's downloads may be moved to the top; the almost finished
    # series keep their place in the queue and only get the priority.
    logged = set()
    for series_id, (nzbid, name, current_priority) in boosted:
        if series_id != winner_id and series_id not in logged:
            logged.add(series_id)
            log_detail("Keeping almost finished '%s' (%d MB left) prioritized."
                       % (candidates[series_id]["title"], candidates[series_id]["remaining"]))
        set_priority(name, nzbid, current_priority, priority, move_to_top and series_id == winner_id)

    if not boosted:
        log_detail("No eligible download to prioritize.")

    # Reset everything else back to normal - including the downloads of a
    # boosted series that did not fit into MaxPrioritizedDownloads. Anything
    # raised above normal up to the boost is ours to lower - matching the boost
    # exactly missed leftovers from an earlier MatchPriority and groups
    # reporting a different value. Priorities above the boost (e.g. a manually
    # forced 900) and deliberately lowered ones are left alone.
    boost = to_int(priority)
    normal = to_int(NORMAL_PRIORITY)
    for entry in candidates.values():
        for nzbid, name, current_priority in entry["groups"]:
            if nzbid in boosted_ids:
                continue
            if normal < current_priority <= boost:
                set_priority(name, nzbid, current_priority, NORMAL_PRIORITY, False)
            elif current_priority > boost:
                log_detail("'%s' has priority %d above MatchPriority %d - leaving it untouched."
                           % (name, current_priority, boost))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def handle_queue(base_url, api_key, exclude_tag, priority, move_to_top, min_remaining_mb, sort_articles,
                 sort_keys, max_prioritized, max_downloads):
    """QUEUE context: react to a queue event."""
    event = os.environ.get("NZBNA_EVENT", "")
    wanted = [e.upper() for e in parse_list(get_option("QueueEvents", "NZB_ADDED, URL_COMPLETED"))]
    if event.upper() not in wanted:
        log_detail("Ignoring queue event '%s' (not in QueueEvents)." % event)
        return SUCCESS

    try:
        prioritize_target_series(base_url, api_key, exclude_tag, priority, move_to_top, min_remaining_mb,
                                 sort_articles, sort_keys, max_prioritized, max_downloads)
    except TimeBudgetExceeded as exc:
        # Not an error: nothing is broken, we just refuse to keep NZBGet
        # waiting. The next queue event tries again.
        log_warning("%s - giving up to keep the queue moving." % exc)
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

    start_time_budget(
        to_int(get_option("RequestTimeout", DEFAULT_REQUEST_TIMEOUT).strip(),
               to_int(DEFAULT_REQUEST_TIMEOUT)),
        to_int(get_option("TotalTimeout", DEFAULT_TOTAL_TIMEOUT).strip(),
               to_int(DEFAULT_TOTAL_TIMEOUT)),
    )

    exclude_tag = get_option("ExcludeTag", "nop").strip()

    priority = get_option("MatchPriority", DEFAULT_PRIORITY).strip()
    try:
        priority = str(int(priority))
    except ValueError:
        log_warning("Invalid MatchPriority '%s', using %s." % (priority, DEFAULT_PRIORITY))
        priority = DEFAULT_PRIORITY

    move_to_top = get_bool_option("MoveToTop", False)
    min_remaining_mb = to_int(get_option("MinRemainingMB", "10").strip(), 10)
    max_prioritized = to_int(get_option("MaxPrioritizedSeries", DEFAULT_MAX_PRIORITIZED).strip(),
                             to_int(DEFAULT_MAX_PRIORITIZED))
    if max_prioritized < 0:
        max_prioritized = 0
    max_downloads = to_int(
        get_option("MaxPrioritizedDownloads", DEFAULT_MAX_PRIORITIZED_DOWNLOADS).strip(),
        to_int(DEFAULT_MAX_PRIORITIZED_DOWNLOADS))
    if max_downloads < 0:
        max_downloads = 0

    if get_bool_option("IgnoreLeadingArticles", True):
        sort_articles = {a.lower() for a in parse_list(get_option("SortArticles", DEFAULT_SORT_ARTICLES))}
    else:
        sort_articles = set()

    sort_keys = parse_sort_order(get_option("SortOrder", DEFAULT_SORT_ORDER))

    if "NZBNA_EVENT" in os.environ:
        return handle_queue(base_url, api_key, exclude_tag, priority, move_to_top, min_remaining_mb,
                            sort_articles, sort_keys, max_prioritized, max_downloads)

    log_error("Unknown context: queue variables (NZBNA_) not set. This is a QUEUE script.")
    return ERROR


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - report any failure to NZBGet
        log_error("Unexpected error: %s" % exc)
        sys.exit(ERROR)
