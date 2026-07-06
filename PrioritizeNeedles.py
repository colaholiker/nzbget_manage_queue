#!/usr/bin/env python3
#
# Prioritize downloads whose name contains a configured needle.
#
##############################################################################
### NZBGET SCAN SCRIPT                                                     ###

# Prioritize downloads matching a list of needles.
#
# When a new nzb is added, its name is checked against a configurable list
# of needles (keywords). If any needle is found in the name, the download is
# given a higher priority (and optionally moved to the top of the queue).
#
# NOTE: This script only affects the priority at the moment the nzb is added.

##############################################################################
### OPTIONS                                                                ###

# List of needles (comma separated).
#
# Downloads whose name contains any of these needles are prioritized.
# Example: 1080p, ubuntu, -PROPER-
#NeedleList=

# Priority to assign on a match.
#
# NZBGet's predefined values: -100 (very low), -50 (low), 0 (normal),
# 50 (high), 100 (very high), 900 (force - downloads even while paused).
# Any integer is accepted.
#MatchPriority=100

# How needles are matched against the name (substring, regex).
#
#  substring - the needle is a plain text fragment (default);
#  regex     - the needle is a Python regular expression.
#MatchMode=substring

# Match case sensitively (yes, no).
#CaseSensitive=no

# Move matched downloads to the top of the queue (yes, no).
#MoveToTop=no

### NZBGET SCAN SCRIPT                                                     ###
##############################################################################

import os
import re
import sys

# NZBGet exit codes for scan scripts.
SUCCESS = 93
ERROR = 94


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


def name_matches(nzb_name, needles, mode, case_sensitive):
    """Return the first needle that matches, or None."""
    haystack = nzb_name if case_sensitive else nzb_name.lower()
    for needle in needles:
        if mode == "regex":
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                if re.search(needle, nzb_name, flags):
                    return needle
            except re.error as exc:
                log_warning("Skipping invalid regex needle '%s': %s" % (needle, exc))
        else:  # substring
            candidate = needle if case_sensitive else needle.lower()
            if candidate in haystack:
                return needle
    return None


def main():
    # Ensure we are running inside NZBGet as a scan script.
    if "NZBOP_SCRIPTDIR" not in os.environ:
        print("This script is supposed to be called from NZBGet (13.0 or later).")
        return ERROR

    if "NZBNP_NZBNAME" not in os.environ:
        log_error("NZBNP_NZBNAME is not set - this is not a scan-script context.")
        return ERROR

    nzb_name = os.environ["NZBNP_NZBNAME"]

    needles = parse_needles(get_option("NeedleList"))
    if not needles:
        log_detail("NeedleList is empty - nothing to prioritize.")
        return SUCCESS

    mode = get_option("MatchMode", "substring").strip().lower()
    if mode not in ("substring", "regex"):
        log_warning("Unknown MatchMode '%s', falling back to 'substring'." % mode)
        mode = "substring"

    case_sensitive = get_bool_option("CaseSensitive", False)

    matched = name_matches(nzb_name, needles, mode, case_sensitive)
    if matched is None:
        log_detail("No needle matched '%s'." % nzb_name)
        return SUCCESS

    priority = get_option("MatchPriority", "100").strip()
    try:
        priority = str(int(priority))
    except ValueError:
        log_warning("Invalid MatchPriority '%s', using 100." % priority)
        priority = "100"

    log_info("'%s' matched needle '%s' - setting priority to %s." % (nzb_name, matched, priority))
    # Tell NZBGet to change the priority of the nzb being added.
    print("[NZB] PRIORITY=%s" % priority)

    if get_bool_option("MoveToTop", False):
        log_detail("Moving '%s' to the top of the queue." % nzb_name)
        print("[NZB] TOP=1")

    return SUCCESS


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - report any failure to NZBGet
        log_error("Unexpected error: %s" % exc)
        sys.exit(ERROR)
