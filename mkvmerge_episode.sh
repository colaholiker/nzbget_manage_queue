#!/bin/bash

##############################################################################
### NZBGET POST-PROCESSING SCRIPT                                           ###

# Merge multiple MKV parts for episode releases only.
#
# This script checks if the NZB name contains an episode tag like E09.
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

# PRÜFUNG: Enthält der Paketname ein "E" gefolgt von zwei Zahlen? (z.B. E09)
if [[ "$FINAL_NAME" =~ E[0-9]{2} ]]; then
    echo "[INFO] Episoden-Tag erkannt im Paket: $FINAL_NAME"

    # 1. Sammle alle echten MKVs (ohne Samples)
    shopt -s nocaseglob
    files=()
    for f in *.mkv; do
        [ -e "$f" ] || continue
        [[ "$f" == *"sample"* ]] && continue
        [ "$(du -m "$f" | cut -f1)" -lt "$MIN_SIZE_MB" ] && continue
        files+=( "$f" )
    done
    shopt -u nocaseglob

    # 2. Nur mergen, wenn tatsächlich mehr als eine Datei existiert
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
    echo "[INFO] Kein Episoden-Tag (E??) gefunden. Vermutlich Staffel-Paket. Überspringe."
    exit 95
fi
