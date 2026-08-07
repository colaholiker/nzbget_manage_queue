#!/bin/bash

##############################################################################
# XEM-Mapping-Tabelle aus Sonarr erzeugen
#
# Liest alle Staffeln einer Serie aus Sonarr, gruppiert die Folgen nach
# Sendedatum und leitet daraus ab, welche Scene-Nummer auf welche
# TVDB-Nummern zeigt. Ein Sendetermin = eine Scene-Folge.
#
# Aufruf:
#   SONARR_URL=http://localhost:8989 \
#   SONARR_API_KEY=deinkey \
#   ./xem_mapping.sh "Der Bergdoktor"
#
# Optional: --csv     nur die CSV-Zeilen ausgeben
#           --season N  nur eine Staffel
#
# Ändert nichts - reines Leseskript.
##############################################################################

SONARR_URL="${SONARR_URL:-http://localhost:8989}"
SONARR_API_KEY="${SONARR_API_KEY:-}"

want_csv=0
only_season=""
term=""

while [ $# -gt 0 ]; do
    case "$1" in
        --csv)    want_csv=1; shift ;;
        --season) only_season="$2"; shift 2 ;;
        -h|--help)
            sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)        term="$1"; shift ;;
    esac
done

if [ -z "$term" ]; then
    echo "Aufruf: SONARR_API_KEY=... $0 \"Serienname\" [--csv] [--season N]" >&2
    exit 2
fi
if [ -z "$SONARR_API_KEY" ]; then
    echo "SONARR_API_KEY ist nicht gesetzt." >&2
    exit 2
fi
for t in curl jq awk; do
    command -v "$t" >/dev/null 2>&1 || { echo "$t fehlt." >&2; exit 2; }
done

sonarr() {
    curl -fsS --max-time 30 -H "X-Api-Key: ${SONARR_API_KEY}" "$@"
}

# --- Serie suchen -----------------------------------------------------------

term_lc=$(printf '%s' "$term" | tr '[:upper:]' '[:lower:]')

matches=$(sonarr "${SONARR_URL%/}/api/v3/series" \
          | jq -r --arg t "$term_lc" '
                .[]
                | select( ((.title | ascii_downcase) | contains($t))
                          or any(.alternateTitles[]?; (.title | ascii_downcase) | contains($t)) )
                | "\(.id)\t\(.tvdbId)\t\(.title)"')

if [ -z "$matches" ]; then
    echo "Keine Serie gefunden, die '$term' enthält." >&2
    exit 1
fi
if [ "$(printf '%s\n' "$matches" | wc -l)" -gt 1 ]; then
    echo "Mehrdeutig, bitte genauer angeben:" >&2
    printf '%s\n' "$matches" | cut -f3 | sed 's/^/  - /' >&2
    exit 1
fi

series_id=$(printf '%s' "$matches" | cut -f1)
tvdb_id=$(printf '%s' "$matches" | cut -f2)
series_title=$(printf '%s' "$matches" | cut -f3)

# --- Folgen holen -----------------------------------------------------------

episodes=$(sonarr -G --data-urlencode "seriesId=${series_id}" \
               "${SONARR_URL%/}/api/v3/episode" \
           | jq -r '.[]
                    | select(.seasonNumber > 0)
                    | select(.airDate != null and .airDate != "")
                    | "\(.seasonNumber)\t\(.episodeNumber)\t\(.airDate)\t\(.title)"')

if [ -z "$episodes" ]; then
    echo "Sonarr liefert keine Folgen mit Sendedatum für '$series_title'." >&2
    exit 1
fi

# Namensfragment für die NZBGet-Option SplitSeasons
name_fragment=$(printf '%s' "$series_title" | tr ' ' '.')

# --- Auswerten --------------------------------------------------------------

printf '%s\n' "$episodes" \
  | LC_ALL=C sort -t "$(printf '\t')" -k1,1n -k3,3 -k2,2n \
  | awk -F '\t' \
        -v csv="$want_csv" \
        -v only="$only_season" \
        -v title="$series_title" \
        -v tvdb="$tvdb_id" \
        -v frag="$name_fragment" '
    {
        s = $1 + 0; ep = $2 + 0; ad = $3; ti = $4
        if (only != "" && s != only + 0) next

        if (s != cur) { cur = s; grp = 0; prev = ""; order[++nseasons] = s }
        if (ad != prev) { grp++; prev = ad }

        k = s SUBSEP grp
        n = ++count[k]
        eps[k] = eps[k] (n > 1 ? " " : "") ep
        date[k] = ad
        if (n == 1) {
            # Alternation statt Zeichenklasse: "–" ist mehrere Bytes und
            # wuerde in [-–—] byteweise zerlegt den Titel zerschneiden.
            sub(/[[:space:]]*(-|–|—)?[[:space:]]*[Tt]eil[[:space:]]*1[[:space:]]*$/, "", ti)
            name[k] = ti
        }
        groups[s] = grp
        total[s]++
    }
    END {
        if (nseasons == 0) { print "Keine Daten." > "/dev/stderr"; exit 1 }

        if (!csv) {
            printf "\n=== %s  (TVDB %s) ===\n", title, tvdb
        }

        split_list = ""
        for (i = 1; i <= nseasons; i++) {
            s = order[i]
            g = groups[s]

            if (g == total[s]) {
                if (!csv)
                    printf "\nStaffel %02d   %2d Folgen / %2d Sendetermine   ->  1:1, kein Mapping nötig\n", \
                           s, total[s], g
                continue
            }

            split_list = split_list (split_list == "" ? "" : ",") s

            if (!csv)
                printf "\nStaffel %02d   %2d Folgen / %2d Sendetermine   ->  MAPPING NÖTIG\n", \
                       s, total[s], g

            for (j = 1; j <= g; j++) {
                k = s SUBSEP j
                cnt = count[k]
                m = split(eps[k], e, " ")

                if (csv) {
                    for (x = 1; x <= m; x++)
                        printf "%d,%d,%d\n", s, j, e[x]
                    continue
                }

                line = sprintf("  scene S%02dE%02d  ->  tvdb", s, j)
                for (x = 1; x <= m; x++)
                    line = line sprintf(" S%02dE%02d%s", s, e[x], (x < m ? " +" : ""))

                flag = ""
                if (cnt != 2) { flag = sprintf("   [!! %d Folgen an einem Termin]", cnt); warned++ }
                printf "%-46s  %s  %s%s\n", line, date[k], name[k], flag
            }
        }

        if (!csv) {
            printf "\n--- Fuer NZBGet ---\n"
            if (split_list == "")
                printf "SplitSeasons=%s:-      (keine Staffel braucht einen Split)\n", frag
            else
                printf "SplitSeasons=%s:%s\n", frag, split_list
            if (warned)
                printf "\n[!] %d Sendetermin(e) mit ungleich 2 Folgen - die Zeilen oben pruefen.\n", warned
            printf "\nMaschinenlesbar (season,scene_ep,tvdb_ep): nochmal mit --csv aufrufen.\n"
        }
    }'
