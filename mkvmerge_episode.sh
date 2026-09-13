#!/bin/bash

##############################################################################
### NZBGET POST-PROCESSING SCRIPT                                           ###

# Merge multiple video parts for episode and CD-split releases.
#
# This script checks if the NZB name contains an episode tag like E09,
# or if the release is split into CD parts (cd1/cd2 as file or folder name).
# If yes, it merges all non-sample video files larger than MinSizeMB.
#
# Requires: mkvmerge.

##############################################################################
### OPTIONS                                                                 ###

# Which file extensions count as video parts, separated by ",".
#
# Leading dots are optional, case does not matter (mkv = .MKV). Parts are
# only merged with parts of the same extension - mixing containers in one
# merge fails almost every time. The result is always a MKV, because that
# is the only container mkvmerge writes.
#
#Extensions=mkv,mp4,avi,ts,m2ts,m4v,mpg,mpeg

# Ignore video files smaller than this (MB) - samples, extras, trailers.
#MinSizeMB=200

### NZBGET POST-PROCESSING SCRIPT                                           ###
##############################################################################

TARGET_DIR="$NZBPP_DIRECTORY"
FINAL_NAME="$NZBPP_NZBNAME"

# --- Konfiguration ----------------------------------------------------------

DEFAULT_EXTENSIONS="mkv,mp4,avi,ts,m2ts,m4v,mpg,mpeg"

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

# Endungsliste normalisieren: ein Eintrag pro Zeile, klein, ohne Punkt und
# ohne Duplikate. Rückgabe 1 = keine brauchbare Endung dabei.
parse_extensions() {
    local raw="$1" ext e seen out=()
    raw="${raw//[;[:space:]]/,}"
    for ext in ${raw//,/ }; do
        ext="${ext#.}"
        ext=$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')
        [ -z "$ext" ] && continue
        if [[ ! "$ext" =~ ^[a-z0-9]+$ ]]; then
            echo "[WARN] Option Extensions: '$ext' ist keine gültige Endung - ignoriert." >&2
            continue
        fi
        seen=0
        for e in "${out[@]}"; do
            [ "$e" = "$ext" ] && { seen=1; break; }
        done
        [ "$seen" -eq 0 ] && out+=( "$ext" )
    done
    [ ${#out[@]} -eq 0 ] && return 1
    printf '%s\n' "${out[@]}"
}

read_extensions() {
    EXTENSIONS=()
    local e
    while IFS= read -r e; do
        EXTENSIONS+=( "$e" )
    done < <(parse_extensions "$1")
}

read_extensions "${NZBPO_EXTENSIONS:-$DEFAULT_EXTENSIONS}"
if [ ${#EXTENSIONS[@]} -eq 0 ]; then
    echo "[WARN] Option Extensions enthält keine gültige Endung - nutze $DEFAULT_EXTENSIONS." >&2
    read_extensions "$DEFAULT_EXTENSIONS"
fi

# kleinere Dateien gelten als Sample/Extra
MIN_SIZE_MB=$(num_opt "${NZBPO_MINSIZEMB:-}" 200 MinSizeMB)

# NZBGet: Wenn Download/Unpack nicht erfolgreich war, überspringen
if [ "$NZBPP_TOTALSTATUS" != "SUCCESS" ]; then
    echo "[INFO] NZBGet-Status ist nicht SUCCESS. Überspringe."
    exit 95
fi

cd "$TARGET_DIR" || exit 94

# 1. Sammle alle echten Videodateien (ohne Samples) - auch aus Unterordnern
#    (z.B. CD1/, CD2/). Natürliche Sortierung (sort -V), damit cd1 vor cd2
#    bzw. part1 vor part10 landet.
#    Die Suche deckt alle konfigurierten Endungen ab; bereits gemergte
#    Ergebnisse ("$FINAL_NAME.<endung>") bleiben außen vor.
find_args=( . -maxdepth 2 -type f '(' )
for i in "${!EXTENSIONS[@]}"; do
    [ "$i" -gt 0 ] && find_args+=( -o )
    find_args+=( -iname "*.${EXTENSIONS[$i]}" )
done
find_args+=( ')' )
for ext in "${EXTENSIONS[@]}"; do
    find_args+=( ! -iname "${FINAL_NAME}.${ext}" )
done
find_args+=( -print0 )

shopt -s nocasematch
files=()
while IFS= read -r -d '' f; do
    f="${f#./}"
    [[ "$f" == *"sample"* ]] && continue
    [ "$(du -m "$f" | cut -f1)" -lt "$MIN_SIZE_MB" ] && continue
    files+=( "$f" )
done < <(find "${find_args[@]}" | sort -zV)

# 2. Auf eine Endung eingrenzen: mkvmerge hängt nur Dateien zusammen, die
#    technisch gleich aufgebaut sind - zwei Container in einem Merge gehen
#    fast immer schief. Es gewinnt die größte Gruppe, bei Gleichstand die in
#    der Options-Reihenfolge weiter vorne stehende Endung.
if [ ${#files[@]} -ge 2 ]; then
    best_ext=""
    best_count=0
    for ext in "${EXTENSIONS[@]}"; do
        count=0
        for f in "${files[@]}"; do
            [[ "${f##*.}" == "$ext" ]] && count=$(( count + 1 ))
        done
        if [ "$count" -gt "$best_count" ]; then
            best_count="$count"
            best_ext="$ext"
        fi
    done

    if [ "$best_count" -lt ${#files[@]} ]; then
        echo "[INFO] Gemischte Dateitypen gefunden - merge nur die .$best_ext-Dateien ($best_count von ${#files[@]})."
        group=()
        for f in "${files[@]}"; do
            [[ "${f##*.}" == "$best_ext" ]] && group+=( "$f" )
        done
        files=( "${group[@]}" )
    fi
fi

# 3. Trigger prüfen: Episoden-Tag im Paketnamen ODER CD-Split in Datei-/Ordnernamen
reason=""
if [[ "$FINAL_NAME" =~ E[0-9]{2} ]]; then
    reason="Episoden-Tag erkannt im Paket: $FINAL_NAME"
else
    for f in "${files[@]}"; do
        if [[ "$f" =~ (^|[^a-z0-9])cd[0-9]+([^a-z0-9]|$) ]]; then
            reason="CD-Split erkannt: $f"
            break
        fi
    done
fi
shopt -u nocasematch

if [ -n "$reason" ]; then
    echo "[INFO] $reason"

    # 4. Nur mergen, wenn tatsächlich mehr als eine Datei existiert
    if [ ${#files[@]} -ge 2 ]; then
        echo "[INFO] Mehrere Teile gefunden (${#files[@]}). Starte mkvmerge..."

        cmd=( mkvmerge -o "${FINAL_NAME}.mkv" )
        for i in "${!files[@]}"; do
            [ "$i" -gt 0 ] && cmd+=( + )
            cmd+=( "${files[$i]}" )
        done

        if "${cmd[@]}"; then
            echo "[INFO] Merge erfolgreich. Lösche Originale."
            for f in "${files[@]}"; do
                rm "$f"
            done
            # Leere Unterordner (z.B. CD1/, CD2/) entfernen
            find . -mindepth 1 -maxdepth 1 -type d -empty -exec rmdir {} \; 2>/dev/null
            exit 93
        else
            echo "[ERROR] Fehler bei mkvmerge!"
            exit 94
        fi
    else
        echo "[INFO] Weniger als zwei passende Dateien gefunden (${#files[@]}). Kein Merge nötig."
        exit 95
    fi
else
    echo "[INFO] Kein Episoden-Tag (E??) und kein CD-Split gefunden. Vermutlich Staffel-Paket. Überspringe."
    exit 95
fi
