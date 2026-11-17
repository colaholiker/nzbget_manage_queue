#!/bin/bash

##############################################################################
### NZBGET POST-PROCESSING SCRIPT                                           ###

# Add a missing file extension based on the actual file content.
#
# Some releases contain files without an extension (e.g. "abc123" instead
# of "abc123.mkv"), so Sonarr/Radarr and the other scripts ignore them.
# This script detects the type with "file" (and "ffprobe" for video
# containers "file" does not recognise) and appends the matching extension.
#
# A file counts as "without extension" when the part after the last dot is
# not a known extension - so "Show.S01E01.1080p" is fixed as well.
# Unknown types and plain text are left alone.
#
# Order this script BEFORE mkvmerge_episode / mkvsplit_episode.
#
# Requires: file. Optional: ffprobe (fallback for video containers).

##############################################################################
### OPTIONS                                                                 ###

# Use ffprobe when "file" cannot identify the type (yes, no).
#UseFfprobe=yes

# Ignore files smaller than this (KB) - leftovers, empty files.
#MinSizeKB=100

# Dry run: only log what would be renamed, change nothing (yes, no).
#DryRun=no

# Fail job if previous NZBGet status was not SUCCESS (yes, no).
#
# If set to yes, the script tells NZBGet that the job failed (POSTPROCESS_FAILURE)
# when download, par or unpack had errors, allowing Sonarr/Radarr to trigger
# an automatic redownload.
#FailJobOnError=yes

### NZBGET POST-PROCESSING SCRIPT                                           ###
##############################################################################

TARGET_DIR="$NZBPP_DIRECTORY"

# --- Konfiguration ----------------------------------------------------------

# Positive Ganzzahl aus einer NZBGet-Option lesen, sonst Default.
num_opt() {
    local value="$1" fallback="$2" name="$3"
    if [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -gt 0 ]; then
        printf '%s' "$value"
    else
        [ -n "$value" ] && echo "[WARN] Option $name='$value' ist keine positive Zahl - nutze $fallback." >&2
        printf '%s' "$fallback"
    fi
}

# yes/no-Option lesen, sonst Default.
bool_opt() {
    case "$(printf '%s' "${1:-$2}" | tr '[:upper:]' '[:lower:]')" in
        yes|true|1|on) printf 'yes' ;;
        *)             printf 'no'  ;;
    esac
}

USE_FFPROBE=$(bool_opt "${NZBPO_USEFFPROBE:-}" yes)
DRY_RUN=$(bool_opt "${NZBPO_DRYRUN:-}" no)
FAIL_JOB_ON_ERROR=$(bool_opt "${NZBPO_FAILJOBONERROR:-}" yes)
MIN_SIZE_KB=$(num_opt "${NZBPO_MINSIZEKB:-}" 100 MinSizeKB)

# Endungen, die als "vorhanden" gelten. Alles andere nach dem letzten Punkt
# (1080p, x264, S01E01, Gruppenname ...) ist Teil des Namens.
KNOWN_EXTENSIONS=" mkv mp4 m4v avi ts m2ts mts mpg mpeg vob mov wmv asf flv webm ogv 3gp
    mp3 flac m4a aac ac3 dts ogg opus wav wma mka
    srt sub idx ass ssa sup vtt
    rar zip 7z gz bz2 xz tar iso img bin cue
    nfo nzb sfv par2 md5 sha1 sha256 txt log url html htm xml json
    jpg jpeg png gif bmp webp tif tiff pdf exe "

# MIME-Typ (file --mime-type) -> Endung
mime_to_ext() {
    case "$1" in
        video/x-matroska)                   echo mkv  ;;
        video/webm)                         echo webm ;;
        video/mp4|video/x-m4v)              echo mp4  ;;
        video/x-msvideo|video/avi)          echo avi  ;;
        video/mp2t)                         echo ts   ;;
        video/mpeg|video/mp2p)              echo mpg  ;;
        video/quicktime)                    echo mov  ;;
        video/x-ms-asf|video/x-ms-wmv)      echo wmv  ;;
        video/x-flv)                        echo flv  ;;
        video/ogg)                          echo ogv  ;;
        video/3gpp)                         echo 3gp  ;;
        audio/x-matroska)                   echo mka  ;;
        audio/mpeg)                         echo mp3  ;;
        audio/flac|audio/x-flac)            echo flac ;;
        audio/mp4|audio/x-m4a)              echo m4a  ;;
        audio/ogg)                          echo ogg  ;;
        audio/x-wav|audio/wav)              echo wav  ;;
        audio/aac|audio/x-hx-aac-adts)      echo aac  ;;
        audio/vnd.dolby.dd-raw|audio/ac3)   echo ac3  ;;
        application/x-rar|application/vnd.rar|application/x-rar-compressed) echo rar ;;
        application/zip)                    echo zip  ;;
        application/x-7z-compressed)        echo 7z   ;;
        application/x-iso9660-image)        echo iso  ;;
        application/pdf)                    echo pdf  ;;
        image/jpeg)                         echo jpg  ;;
        image/png)                          echo png  ;;
        image/gif)                          echo gif  ;;
        image/webp)                         echo webp ;;
    esac
}

# ffprobe format_name -> Endung (nur Video-Container)
ffprobe_to_ext() {
    case "$1" in
        matroska*)      echo mkv ;;
        mov,mp4*)       echo mp4 ;;
        avi)            echo avi ;;
        mpegts)         echo ts  ;;
        mpeg|mpegvideo) echo mpg ;;
        asf)            echo wmv ;;
        flv)            echo flv ;;
    esac
}

detect_ext() {
    local f="$1" mime="" ext="" fmt
    if [ "$HAVE_FILE" = "yes" ]; then
        mime=$(file --brief --mime-type -- "$f" 2>/dev/null | tr '[:upper:]' '[:lower:]')
        ext=$(mime_to_ext "$mime")
    fi
    if [ -z "$ext" ] && [ "$HAVE_FFPROBE" = "yes" ]; then
        fmt=$(ffprobe -v error -show_entries format=format_name \
                -of default=noprint_wrappers=1:nokey=1 -- "$f" 2>/dev/null | head -n1)
        ext=$(ffprobe_to_ext "$fmt")
    fi
    printf '%s' "$ext"
}

has_known_ext() {
    local name="${1##*/}" suffix
    [[ "$name" == *.* ]] || return 1
    suffix=$(printf '%s' "${name##*.}" | tr '[:upper:]' '[:lower:]')
    # Gesplittete Archive (.001, .r00, .part01 ...) nie anfassen
    [[ "$suffix" =~ ^([0-9]+|r[0-9]+|s[0-9]+|z[0-9]+)$ ]] && return 0
    [[ " $(echo $KNOWN_EXTENSIONS) " == *" $suffix "* ]]
}

# --- Ablauf -----------------------------------------------------------------

# NZBGet: Wenn Download/Unpack nicht erfolgreich war, abbrechen oder überspringen
if [ -n "$NZBPP_TOTALSTATUS" ] && [ "$NZBPP_TOTALSTATUS" != "SUCCESS" ]; then
    if [ "$FAIL_JOB_ON_ERROR" = "yes" ]; then
        echo "[ERROR] NZBGet-Status ist nicht SUCCESS ($NZBPP_TOTALSTATUS). Breche Job mit FAILURE ab."
        exit 94
    else
        echo "[INFO] NZBGet-Status ist nicht SUCCESS ($NZBPP_TOTALSTATUS). Überspringe."
        exit 95
    fi
fi

HAVE_FILE="no"
command -v file >/dev/null 2>&1 && HAVE_FILE="yes"
HAVE_FFPROBE="no"
[ "$USE_FFPROBE" = "yes" ] && command -v ffprobe >/dev/null 2>&1 && HAVE_FFPROBE="yes"

if [ "$HAVE_FILE" = "no" ] && [ "$HAVE_FFPROBE" = "no" ]; then
    echo "[ERROR] Weder 'file' noch 'ffprobe' gefunden."
    exit 94
fi
[ "$HAVE_FILE" = "no" ] && echo "[WARN] 'file' nicht gefunden - erkenne nur Video-Container per ffprobe."

[ -n "$TARGET_DIR" ] && cd "$TARGET_DIR" || { echo "[ERROR] Verzeichnis '$TARGET_DIR' nicht gefunden."; exit 94; }

renamed=0
errors=0
while IFS= read -r -d '' f; do
    f="${f#./}"
    name="${f##*/}"
    [[ "$name" == .* ]] && continue
    has_known_ext "$f" && continue

    ext=$(detect_ext "$f")
    if [ -z "$ext" ]; then
        echo "[DETAIL] Kein bekannter Typ für '$f' ($(file --brief --mime-type -- "$f" 2>/dev/null || echo unbekannt)) - bleibt unverändert."
        continue
    fi

    new="$f.$ext"
    if [ -e "$new" ]; then
        echo "[WARN] '$new' existiert bereits - '$f' bleibt unverändert."
        continue
    fi

    if [ "$DRY_RUN" = "yes" ]; then
        echo "[INFO] [DryRun] Würde umbenennen: '$f' -> '$new'"
        renamed=$(( renamed + 1 ))
    elif mv -n -- "$f" "$new"; then
        echo "[INFO] Umbenannt: '$f' -> '$new'"
        renamed=$(( renamed + 1 ))
    else
        echo "[ERROR] Umbenennen fehlgeschlagen: '$f' -> '$new'"
        errors=$(( errors + 1 ))
    fi
done < <(find . -type f -size +"$(( MIN_SIZE_KB - 1 ))"k -print0 | sort -z)

if [ "$errors" -gt 0 ]; then
    exit 94
elif [ "$renamed" -gt 0 ]; then
    if [ "$DRY_RUN" = "yes" ]; then
        echo "[INFO] [DryRun] $renamed Datei(en) würden eine Endung bekommen."
    else
        echo "[INFO] $renamed Datei(en) mit Endung versehen."
    fi
    exit 93
else
    echo "[INFO] Keine Dateien ohne Endung gefunden."
    exit 95
fi
