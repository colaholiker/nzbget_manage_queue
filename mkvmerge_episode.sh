#!/bin/bash

##############################################################################
### NZBGET POST-PROCESSING SCRIPT                                           ###

# Merge multiple MKV parts for episode and CD-split releases.
#
# This script checks if the NZB name contains an episode tag like E09,
# or if the release is split into CD parts (cd1/cd2 as file or folder name).
# If yes, it merges all non-sample MKV files larger than MIN_SIZE_MB.
#
##############################################################################

TARGET_DIR="$NZBPP_DIRECTORY"
FINAL_NAME="$NZBPP_NZBNAME"
MIN_SIZE_MB=200

# NZBGet: Wenn Download/Unpack nicht erfolgreich war, überspringen
if [ "$NZBPP_TOTALSTATUS" != "SUCCESS" ]; then
    echo "[INFO] NZBGet-Status ist nicht SUCCESS. Überspringe."
    exit 95
fi

cd "$TARGET_DIR" || exit 94

# 1. Sammle alle echten MKVs (ohne Samples) - auch aus Unterordnern (z.B. CD1/, CD2/)
#    Natürliche Sortierung (sort -V), damit cd1 vor cd2 bzw. part1 vor part10 landet.
shopt -s nocasematch
files=()
while IFS= read -r -d '' f; do
    f="${f#./}"
    [[ "$f" == *"sample"* ]] && continue
    [ "$(du -m "$f" | cut -f1)" -lt "$MIN_SIZE_MB" ] && continue
    files+=( "$f" )
done < <(find . -maxdepth 2 -type f -iname '*.mkv' ! -name "${FINAL_NAME}.mkv" -print0 | sort -zV)

# 2. Trigger prüfen: Episoden-Tag im Paketnamen ODER CD-Split in Datei-/Ordnernamen
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

    # 3. Nur mergen, wenn tatsächlich mehr als eine Datei existiert
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
        echo "[INFO] Nur eine Datei gefunden. Kein Merge nötig."
        exit 95
    fi
else
    echo "[INFO] Kein Episoden-Tag (E??) und kein CD-Split gefunden. Vermutlich Staffel-Paket. Überspringe."
    exit 95
fi
