#!/bin/bash

##############################################################################
### NZBGET POST-PROCESSING SCRIPT                                           ###

# Split double episodes into two files (e.g. Der Bergdoktor).
#
# German broadcasters air two ~45 min episodes in one slot. Scene releases
# number the slot (S16E03) while TVDB/Sonarr number the halves separately
# (S16E05 + S16E06). This script asks Sonarr how the season is actually
# structured, then cuts the MKV at the episode boundary and names the parts
# TVDB style.
#
# Requires: mkvmerge, ffmpeg, ffprobe. Optional but recommended: curl + jq
# for the Sonarr lookup.

##############################################################################
### OPTIONS                                                                 ###

# Sonarr URL, e.g. http://localhost:8989 (leave empty to disable the lookup).
#SonarrUrl=http://localhost:8989

# Sonarr API key (Settings -> General -> Security).
#SonarrApiKey=

# Which series may be touched, and which seasons to assume are split when
# Sonarr cannot be reached.
#
# Format: <name fragment>:<seasons>, several entries separated by ";"
#   seasons   1,2,3   only these seasons (fallback)
#             *       every season (fallback)
#             -       never without Sonarr
#   The name fragment is matched case-insensitively against the NZB name;
#   dots are literal characters, not wildcards.
#
# Examples: Der.Bergdoktor:16,17
#           Der.Bergdoktor:-;Die.Rosenheim-Cops:3
#
#SplitSeasons=Der.Bergdoktor:16

# Manual exceptions, they override Sonarr.
# Format: <scene tag>=<ep1>+<ep2>, several entries separated by ";"
# Example: S16E03=05+06;S17E01=01+02
#EpisodeOverride=

# Dry run: only log what would happen, change nothing (yes, no).
#DryRun=yes

# split = cut into two files, rename = only rename to SxxEyy-Ezz (split, rename).
#Mode=split

# Ignore MKV files smaller than this (MB) - samples, extras, trailers.
#MinSizeMB=200

# Look for the episode boundary within +/- this many minutes around the middle.
#SearchWindowMin=6

# Refuse the cut if either half would be shorter than this (minutes).
#MinPartMin=20

# Hard safety brake: never touch a file shorter than this (minutes).
# A single episode must never be split, no matter what Sonarr says.
#MinDoubleMin=70

### NZBGET POST-PROCESSING SCRIPT                                           ###
##############################################################################

TARGET_DIR="$NZBPP_DIRECTORY"
FINAL_NAME="$NZBPP_NZBNAME"

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

SONARR_URL="${NZBPO_SONARRURL:-}"
SONARR_API_KEY="${NZBPO_SONARRAPIKEY:-}"

# split  = Datei zerschneiden -> zwei Dateien S16E05 / S16E06
# rename = nur umbenennen zu S16E05-E06 (Sonarr/Plex erkennen Doppelfolgen nativ)
MODE=$(printf '%s' "${NZBPO_MODE:-split}" | tr '[:upper:]' '[:lower:]')
if [ "$MODE" != "split" ] && [ "$MODE" != "rename" ]; then
    echo "[WARN] Option Mode='$MODE' unbekannt - nutze split." >&2
    MODE="split"
fi

# yes = nichts anfassen, nur den erkannten Schnittpunkt loggen.
# Erst nach einem erfolgreichen Testlauf auf "no" setzen!
# Alles außer einem eindeutigen "no" gilt als Trockenlauf - ein Tippfehler
# darf nicht dazu führen, dass scharf geschnitten wird.
case "$(printf '%s' "${NZBPO_DRYRUN:-yes}" | tr '[:upper:]' '[:lower:]')" in
    no|false|0) DRY_RUN="no"  ;;
    *)          DRY_RUN="yes" ;;
esac

# Welche Serien angefasst werden dürfen und welche Staffeln ohne Sonarr als
# Doppelfolgen-Staffeln gelten. Siehe OPTIONS-Block oben für das Format.
# Der Wert hier ist nur der Default, wenn die NZBGet-Option leer ist.
SPLIT_SEASONS="${NZBPO_SPLITSEASONS:-Der.Bergdoktor:16}"

# Manuelle Ausnahmen, schlagen alles andere - auch Sonarr.
EPISODE_OVERRIDE="${NZBPO_EPISODEOVERRIDE:-}"

# kleinere MKVs gelten als Sample/Extra
MIN_SIZE_MB=$(num_opt "${NZBPO_MINSIZEMB:-}" 200 MinSizeMB)

# +/- Minuten um die Mitte, in denen der Schnittpunkt gesucht wird
SEARCH_WINDOW_MIN=$(num_opt "${NZBPO_SEARCHWINDOWMIN:-}" 6 SearchWindowMin)

# Plausibilitätsgrenze pro Teil nach dem Schnitt
MIN_PART_MIN=$(num_opt "${NZBPO_MINPARTMIN:-}" 20 MinPartMin)

# Harte Bremse: Kürzer als das kann keine Doppelfolge sein. Greift auch dann,
# wenn Sonarr etwas anderes behauptet oder die Staffelliste falsch ist.
MIN_DOUBLE_MIN=$(num_opt "${NZBPO_MINDOUBLEMIN:-}" 70 MinDoubleMin)

# --- Konfiguration auswerten ------------------------------------------------

trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

# Sucht den ersten Eintrag, dessen Namensfragment im Paketnamen vorkommt.
# Gibt dessen Staffelliste aus. Rückgabe 1 = keine Serie passt.
match_series_seasons() {
    local cfg="$1" name="$2" entry pat seasons hit=1
    shopt -s nocasematch
    while IFS= read -r entry; do
        entry=$(trim "$entry")
        [ -z "$entry" ] && continue
        if [[ "$entry" == *:* ]]; then
            pat=$(trim "${entry%%:*}")
            seasons=$(trim "${entry#*:}")
        else
            pat="$entry"
            seasons="*"
        fi
        [ -z "$pat" ] && continue
        if [[ "$name" == *"$pat"* ]]; then
            printf '%s' "$seasons"
            hit=0
            break
        fi
    done < <(printf '%s\n' "$cfg" | tr ';' '\n')
    shopt -u nocasematch
    return "$hit"
}

# Steht die Staffel in der Liste? "*" = alle, "-" oder leer = keine.
season_allowed() {
    local list="$1" want="$2" s
    [ "$list" = "*" ] && return 0
    [ -z "$list" ] && return 1
    [ "$list" = "-" ] && return 1
    for s in ${list//,/ }; do
        [[ "$s" =~ ^[0-9]+$ ]] || continue
        [ "$(( 10#$s ))" -eq "$(( 10#$want ))" ] && return 0
    done
    return 1
}

# Sucht "S16E03=05+06" und gibt "05 06" aus. Rückgabe 1 = kein Eintrag.
lookup_override() {
    local cfg="$1" want="$2" entry k v hit=1
    want=$(printf '%s' "$want" | tr '[:lower:]' '[:upper:]')
    while IFS= read -r entry; do
        entry=$(trim "$entry")
        [ -z "$entry" ] && continue
        [[ "$entry" == *=* ]] || continue
        k=$(trim "${entry%%=*}")
        k=$(printf '%s' "$k" | tr '[:lower:]' '[:upper:]')
        [ "$k" = "$want" ] || continue
        v=$(trim "${entry#*=}")
        v="${v//+/ }"
        v="${v//,/ }"
        printf '%s' "$v"
        hit=0
        break
    done < <(printf '%s\n' "$cfg" | tr ';' '\n')
    return "$hit"
}

# --- Hilfsfunktionen --------------------------------------------------------

sec_to_ts() {
    awk -v s="$1" 'BEGIN {
        h = int(s/3600); s -= h*3600
        m = int(s/60);   s -= m*60
        printf "%02d:%02d:%06.3f\n", h, m, s
    }'
}

duration_of() {
    ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$1" 2>/dev/null
}

# --- Sonarr -----------------------------------------------------------------

sonarr_curl() {
    curl -fsS --max-time 20 -H "X-Api-Key: ${SONARR_API_KEY}" "$@"
}

# Serien-ID über den Release-Namen (Sonarr parst ihn selbst)
sonarr_series_id() {
    sonarr_curl -G --data-urlencode "title=${FINAL_NAME}" \
        "${SONARR_URL%/}/api/v3/parse" 2>/dev/null \
      | jq -r '.series.id // empty' 2>/dev/null
}

# Fallback: Serienliste durchsuchen (Haupt- und Alternativtitel)
sonarr_series_id_by_title() {
    sonarr_curl "${SONARR_URL%/}/api/v3/series" 2>/dev/null \
      | jq -r --arg t "$1" '
            .[]
            | select( (.title | ascii_downcase) == $t
                      or any(.alternateTitles[]?; (.title | ascii_downcase) == $t) )
            | .id' 2>/dev/null \
      | head -1
}

# Alle Folgen einer Staffel als TSV: Nummer <TAB> Sendedatum <TAB> Titel
sonarr_episodes() {
    sonarr_curl -G --data-urlencode "seriesId=$1" --data-urlencode "seasonNumber=$2" \
        "${SONARR_URL%/}/api/v3/episode" 2>/dev/null \
      | jq -r '.[]
               | select(.airDate != null and .airDate != "")
               | "\(.episodeNumber)\t\(.airDate)\t\(.title)"' 2>/dev/null
}

# Folgen nach Sendedatum gruppieren und die N-te Sendung herausgreifen.
# Ein Sendetermin = eine Scene-Folge. Enthält er zwei TVDB-Folgen, ist es
# eine Doppelfolge; enthält er eine, passt die Nummerierung bereits.
pick_broadcast() {
    LC_ALL=C sort -t "$(printf '\t')" -k2,2 -k1,1n \
      | awk -F '\t' -v want="$1" '
            { if ($2 != prev) { g++; prev = $2 }
              if (g == want) { nums = nums (nums == "" ? "" : " ") $1
                               tit[++n] = sprintf("E%02d %s (%s)", $1, $3, $2) } }
            END { printf "GROUPS %d\n", g
                  if (nums != "") printf "PICK %s\n", nums
                  for (i = 1; i <= n; i++) printf "TITLE %s\n", tit[i] }'
}

# --- Ablauf -----------------------------------------------------------------

if [ "$NZBPP_TOTALSTATUS" != "SUCCESS" ]; then
    echo "[INFO] NZBGet-Status ist nicht SUCCESS. Überspringe."
    exit 95
fi

cd "$TARGET_DIR" || exit 94

# 1. Ist das eine konfigurierte Serie?
if ! fallback_seasons=$(match_series_seasons "$SPLIT_SEASONS" "$FINAL_NAME"); then
    echo "[INFO] '$FINAL_NAME' steht nicht in SplitSeasons ($SPLIT_SEASONS). Überspringe."
    exit 95
fi

# 2. Staffel/Folge aus dem Paketnamen ziehen
if ! [[ "$FINAL_NAME" =~ [Ss]([0-9]{2})[Ee]([0-9]{2}) ]]; then
    echo "[INFO] Kein SxxEyy im Paketnamen gefunden. Überspringe."
    exit 95
fi
tag="${BASH_REMATCH[0]}"
season="${BASH_REMATCH[1]}"
scene_ep="${BASH_REMATCH[2]}"
key=$(printf 'S%sE%s' "$season" "$scene_ep")

# 3. Mapping bestimmen: Override -> Sonarr -> statische Liste
ep1=""; ep2=""; source_of_truth=""

if override=$(lookup_override "$EPISODE_OVERRIDE" "$key"); then
    read -r ep1 ep2 <<< "$override"
    if [ -z "$ep2" ]; then
        echo "[ERROR] EpisodeOverride für $key braucht zwei Folgen (z.B. $key=05+06)."
        exit 94
    fi
    ep1=$(printf '%02d' "$(( 10#$ep1 ))")
    ep2=$(printf '%02d' "$(( 10#$ep2 ))")
    source_of_truth="manuelles Override"
fi

if [ -z "$ep1" ] && [ -n "$SONARR_URL" ] && [ -n "$SONARR_API_KEY" ]; then
    if ! command -v curl >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
        echo "[WARN] curl oder jq fehlt - Sonarr-Abfrage übersprungen."
    else
        sid=$(sonarr_series_id)
        if [ -z "$sid" ]; then
            guess=$(printf '%s' "${FINAL_NAME%%.$tag*}" | tr '.' ' ' | tr '[:upper:]' '[:lower:]')
            sid=$(sonarr_series_id_by_title "$guess")
        fi

        if [ -z "$sid" ]; then
            echo "[WARN] Serie in Sonarr nicht gefunden."
        else
            info=$(sonarr_episodes "$sid" "$season" | pick_broadcast "$(( 10#$scene_ep ))")
            ngroups=$(awk '/^GROUPS /{ print $2 }' <<< "$info")
            picked=$(awk '/^PICK /{ $1 = ""; sub(/^ /, ""); print }' <<< "$info")
            awk '/^TITLE /{ $1 = ""; sub(/^ /, ""); print "[INFO]   Sonarr: " $0 }' <<< "$info"

            if [ -z "$ngroups" ] || [ "$ngroups" -eq 0 ] 2>/dev/null; then
                echo "[WARN] Sonarr lieferte keine Folgen für Staffel $season."
            elif [ -z "$picked" ]; then
                echo "[WARN] Sendetermin Nr. $(( 10#$scene_ep )) existiert nicht"
                echo "       (Staffel $season hat laut Sonarr nur $ngroups Termine)."
            else
                set -- $picked
                case $# in
                    1)
                        echo "[INFO] Sonarr: Sendetermin $(( 10#$scene_ep )) enthält nur Folge E$(printf '%02d' "$1")."
                        echo "       Nummerierung passt bereits, nichts zu tun. Überspringe."
                        exit 95
                        ;;
                    2)
                        ep1=$(printf '%02d' "$1")
                        ep2=$(printf '%02d' "$2")
                        if [ "$(( 10#$ep2 ))" -ne "$(( 10#$ep1 + 1 ))" ]; then
                            echo "[WARN] Sonarr liefert E$ep1 + E$ep2 - nicht aufeinanderfolgend."
                            echo "       Die TVDB-Sendedaten dieser Staffel sind unstimmig."
                            echo "       Überspringe, sonst landet der falsche Inhalt in der Folge."
                            exit 95
                        fi
                        source_of_truth="Sonarr (Sendedatum-Gruppierung)"
                        ;;
                    *)
                        echo "[WARN] Sendetermin enthält $# Folgen - das kann ich nicht sicher schneiden."
                        echo "       Überspringe. Bei Bedarf EPISODE_OVERRIDE setzen."
                        exit 95
                        ;;
                esac
            fi
        fi
    fi
fi

if [ -z "$ep1" ]; then
    if ! season_allowed "$fallback_seasons" "$season"; then
        echo "[INFO] Ohne Sonarr-Auskunft und Staffel $season steht nicht in der"
        echo "       Notfall-Liste ('$fallback_seasons'). Überspringe."
        exit 95
    fi
    ep1=$(printf '%02d' $(( 10#$scene_ep * 2 - 1 )))
    ep2=$(printf '%02d' $(( 10#$scene_ep * 2 )))
    source_of_truth="Notfall-Liste + 2N-1/2N-Regel"
fi

if [ "$ep1" = "$ep2" ]; then
    echo "[ERROR] Mapping liefert zweimal Folge E${ep1}. Breche ab."
    exit 94
fi

name1="${FINAL_NAME/$tag/S${season}E${ep1}}"
name2="${FINAL_NAME/$tag/S${season}E${ep2}}"
name_both="${FINAL_NAME/$tag/S${season}E${ep1}-E${ep2}}"

echo "[INFO] Scene $key  ->  TVDB S${season}E${ep1} + S${season}E${ep2}   [$source_of_truth]"

# 4. Genau eine echte MKV erwarten
shopt -s nocaseglob nullglob
files=()
for f in *.mkv; do
    [[ "$f" == *"sample"* ]] && continue
    [ "$(du -m "$f" | cut -f1)" -lt "$MIN_SIZE_MB" ] && continue
    files+=( "$f" )
done
shopt -u nocaseglob nullglob

if [ ${#files[@]} -ne 1 ]; then
    echo "[ERROR] Erwarte genau eine MKV, gefunden: ${#files[@]}. Überspringe."
    exit 95
fi
src="${files[0]}"

# 5. Laufzeit-Gegenprobe: eine Einzelfolge wird nie angefasst
duration=$(duration_of "$src")
if [ -z "$duration" ]; then
    echo "[ERROR] Konnte Laufzeit von '$src' nicht lesen (ffprobe da?)."
    exit 94
fi

if ! awk -v d="$duration" -v m="$MIN_DOUBLE_MIN" 'BEGIN { exit !(d >= m*60) }'; then
    echo "[INFO] Laufzeit $(sec_to_ts "$duration") liegt unter ${MIN_DOUBLE_MIN} min."
    echo "       Das ist keine Doppelfolge. Überspringe."
    exit 95
fi

# 6. Nur umbenennen?
if [ "$MODE" = "rename" ]; then
    if [ "$src" = "${name_both}.mkv" ]; then
        echo "[INFO] '$src' heißt bereits richtig. Nichts zu tun."
        exit 95
    fi
    echo "[INFO] Umbenennen: '$src' -> '${name_both}.mkv'"
    if [ "$DRY_RUN" = "yes" ]; then
        echo "[INFO] DRY_RUN aktiv - nichts geändert."
        exit 95
    fi
    mv -- "$src" "${name_both}.mkv" || exit 94
    exit 93
fi

# 7. Schnittpunkt bestimmen
read -r mid lo hi <<< "$(awk -v d="$duration" -v w="$SEARCH_WINDOW_MIN" 'BEGIN {
    m = d/2; l = m - w*60; if (l < 0) l = 0; h = m + w*60
    printf "%.3f %.3f %.3f", m, l, h
}')"

echo "[INFO] Laufzeit $(sec_to_ts "$duration"), Suchfenster $(sec_to_ts "$lo") - $(sec_to_ts "$hi")"

method="Kapitelmarke"
cut=$(ffprobe -v error -show_entries chapter=start_time -of csv=p=0 "$src" 2>/dev/null \
      | awk -v lo="$lo" -v hi="$hi" -v mid="$mid" '
            { t = $1 + 0
              if (t < lo || t > hi) next
              d = t - mid; if (d < 0) d = -d
              if (best == "" || d < best) { best = d; sel = t } }
            END { if (best != "") printf "%.3f\n", sel }')

if [ -z "$cut" ]; then
    method="Schwarzbild+Stille"
    span=$(awk -v lo="$lo" -v hi="$hi" 'BEGIN { printf "%.3f", hi - lo }')
    raw=$(ffmpeg -nostdin -v info -ss "$lo" -t "$span" -i "$src" \
              -vf blackdetect=d=0.4:pix_th=0.10 \
              -af silencedetect=n=-50dB:d=0.4 \
              -f null - 2>&1 \
          | awk -v mid="$mid" -v off="$lo" '
                /black_start:/ {
                    bs = ""; be = ""
                    for (i = 1; i <= NF; i++) {
                        if ($i ~ /^black_start:/) { split($i, p, ":"); bs = p[2] + 0 }
                        if ($i ~ /^black_end:/)   { split($i, p, ":"); be = p[2] + 0 }
                    }
                    if (bs != "" && be != "") { nb++; b1[nb] = bs; b2[nb] = be }
                }
                /silence_start:/ { for (i = 1; i <= NF; i++) if ($i == "silence_start:") ss = $(i+1) + 0 }
                /silence_end:/ {
                    for (i = 1; i <= NF; i++) if ($i == "silence_end:") { ns++; s1[ns] = ss; s2[ns] = $(i+1) + 0 }
                }
                END {
                    midrel = mid - off
                    for (i = 1; i <= nb; i++) {
                        ov = 0
                        for (j = 1; j <= ns; j++) {
                            lap0 = (b1[i] > s1[j] ? b1[i] : s1[j])
                            lap1 = (b2[i] < s2[j] ? b2[i] : s2[j])
                            if (lap1 > lap0) ov += lap1 - lap0
                        }
                        c = (b1[i] + b2[i]) / 2
                        d = c - midrel; if (d < 0) d = -d
                        if (bi == "" || ov > bov || (ov == bov && d < bd)) { bi = i; bov = ov; bd = d; sel = c }
                    }
                    if (bi != "") printf "%.3f\n", sel
                }')

    # Ohne -copyts liefert ffmpeg Zeiten relativ zum -ss-Punkt. Falls doch
    # absolute Zeiten kommen, erkennen wir das an der Größenordnung.
    [ -n "$raw" ] && cut=$(awk -v raw="$raw" -v off="$lo" -v span="$span" 'BEGIN {
        if (off > span && raw > span + 2) printf "%.3f\n", raw
        else                              printf "%.3f\n", raw + off
    }')
fi

if [ -z "$cut" ]; then
    method="exakte Mitte (Notfall-Fallback)"
    cut="$mid"
fi

cut_ts=$(sec_to_ts "$cut")
echo "[INFO] Schnittpunkt: $cut_ts   (Methode: $method)"

if ! awk -v c="$cut" -v d="$duration" -v m="$MIN_PART_MIN" \
        'BEGIN { exit !(c >= m*60 && (d - c) >= m*60) }'; then
    echo "[ERROR] Schnittpunkt unplausibel (Teil kürzer als ${MIN_PART_MIN} min). Breche ab."
    exit 94
fi

if [ "$DRY_RUN" = "yes" ]; then
    echo "[INFO] DRY_RUN aktiv - es wird nicht geschnitten."
    echo "[INFO] Würde erzeugen: ${name1}.mkv  +  ${name2}.mkv"
    exit 95
fi

# 8. Schneiden
tmp=$(mktemp -d "${TARGET_DIR}/.split.XXXXXX") || exit 94
trap 'rm -rf "$tmp"' EXIT

if ! mkvmerge -o "${tmp}/part.mkv" --split "parts:00:00:00-${cut_ts},${cut_ts}-" "$src"; then
    echo "[ERROR] mkvmerge ist fehlgeschlagen. Original bleibt erhalten."
    exit 94
fi

if [ ! -s "${tmp}/part-001.mkv" ] || [ ! -s "${tmp}/part-002.mkv" ]; then
    echo "[ERROR] mkvmerge hat nicht zwei Teile erzeugt. Original bleibt erhalten."
    exit 94
fi

# Das Original erst beiseiteschieben: einer der Zielnamen kann mit der
# Quelldatei identisch sein (z.B. Override S12E03=03+04).
backup="${src}.orig"
mv -- "$src" "$backup" || exit 94

if ! mv -- "${tmp}/part-001.mkv" "${name1}.mkv"; then
    echo "[ERROR] Konnte Teil 1 nicht ablegen. Stelle Original wieder her."
    mv -- "$backup" "$src"
    exit 94
fi
if ! mv -- "${tmp}/part-002.mkv" "${name2}.mkv"; then
    echo "[ERROR] Konnte Teil 2 nicht ablegen. Stelle Original wieder her."
    rm -f -- "${name1}.mkv"
    mv -- "$backup" "$src"
    exit 94
fi
rm -- "$backup"

echo "[INFO] Fertig: ${name1}.mkv + ${name2}.mkv"
exit 93
