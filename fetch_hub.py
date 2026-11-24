#!/usr/bin/env python3
"""Match-Hub data fetcher: pulls QHL, NLB, Mobiliar Handball Cup (Männer), SPL1 and SPL2 (Frauen)
from the public handball.ch matchcenter GraphQL API, plus EHF European League and EHF European Cup
(Männer) from the public eurohandball.com API, and writes the hub_*.json files.

Usage: python3 fetch_hub.py [out_dir]
Writes out_dir/hub_meta.json, hub_games.json, hub_players_<comp>.json
"""
import json, os, ssl, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

API = "https://www.handball.ch/Umbraco/Api/MatchCenter/Query"
SEASON = 2026
COMPS = {
    "qhl": {"name": "Quickline Handball League", "short": "QHL", "league": 1110},
    "nlb": {"name": "Nationalliga B", "short": "NLB", "league": 1120},
    "cup": {"name": "Mobiliar Handball Cup", "short": "Cup", "cup": 1000928},
    "spl1": {"name": "Swiss Premium League 1", "short": "SPL1", "league": 2112},
    "spl2": {"name": "Swiss Premium League 2", "short": "SPL2", "league": 2122},
}

_ctx = ssl.create_default_context()
_ca = os.path.expanduser("~/.ccr/ca-bundle.crt")
if os.path.exists(_ca):
    _ctx.load_verify_locations(_ca)


def gql(query, variables=None, tries=3):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(API, data=body, headers={
                "Content-Type": "application/json", "Accept-Language": "de",
                "User-Agent": "MatchHub/1.0"})
            with urllib.request.urlopen(req, timeout=40, context=_ctx) as r:
                d = json.load(r)
            if d.get("errors"):
                raise RuntimeError(d["errors"])
            return d["data"]
        except Exception as e:  # noqa
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


GAME_FIELDS = """objectId,gameDateTime,isLive,homeTeamId,homeTeamName,homeTeamShortName,homeTeamScore,
homeTeamScoreInterval,homeTeamClubId,awayTeamClubId,awayTeamId,awayTeamName,awayTeamShortName,awayTeamScore,awayTeamScoreInterval,venueName,gameStatusId,gameStatus,spectators"""

STAND_FIELDS = "teamId,name,rank,games,won,drawn,lost,goalsFor,goalsAgainst,goalDifference,points,lastFive"

PLAYER_Q = """query($g:Int,$l:Boolean){gamePlayerStats(gameId:$g,isLive:$l){teamId,teamName,playerId,playerName,
dressNr,function,totalScore,totalShots,totalScore7m,totalShots7m,technicalErrors,total2Minutes,totalSuspension,
totalWarnings,totalSaves,totalShotsGK,totalSaves7m,totalShots7mGK}}"""


def menu_groups():
    d = gql("query($s:Int){menuItem(seasonId:$s){objectId,objectType,menuItem{objectId,objectType,name,"
            "menuItem{objectId,objectType,name}}}}", {"s": SEASON})["menuItem"]
    out = {}
    for org in d:
        for lg in org.get("menuItem") or []:
            if lg["objectType"] == "league":
                out[lg["objectId"]] = [(g["objectId"], g["name"]) for g in (lg.get("menuItem") or [])
                                       if g["objectType"] == "group"]
    return out


def collect():
    groups = menu_groups()
    games, standings, rounds = [], {}, {}
    for key, c in COMPS.items():
        if c.get("ehf"):
            continue
        if "league" in c:
            grps = groups.get(c["league"], [])
        else:
            cd = gql("query($c:Int){cupDefinition(cupId:$c){groups{groupId,name,roundDate}}}", {"c": c["cup"]})
            names = {g["groupId"]: g["name"] for g in cd["cupDefinition"][0]["groups"]}
            rounds[key] = list(names.values())
            gl = gql("query($g:Int){games(groupId:$g){%s,groupId}}" % GAME_FIELDS, {"g": c["cup"]})["games"] or []
            for g in gl:
                g["comp"], g["round"] = key, names.get(g.pop("groupId"), "")
            games += gl
            continue
        rounds[key] = [n for _, n in grps]
        for gid, gname in grps:
            gl = gql("query($g:Int){games(groupId:$g){%s}}" % GAME_FIELDS, {"g": gid})["games"] or []
            for g in gl:
                g["comp"], g["round"] = key, gname
            games += gl
            if "league" in c:
                st = gql("query($g:Int){standing(groupId:$g){%s}}" % STAND_FIELDS, {"g": gid})["standing"] or []
                if st:
                    standings.setdefault(key, []).append({"group": gname, "rows": st})
    return games, standings, rounds


def played(g):
    return g.get("gameStatusId") == 2


# ---------------------------------------------------------------- EHF (eurohandball.com)
# European League und European Cup der Männer aus der öffentlichen API von eurohandball.com.
# Spiele, Spielerstatistiken und Tabellen werden in dasselbe Format gebracht wie die handball.ch-Daten.
import hashlib, re, unicodedata, difflib

EHF = {
    "el": {"name": "EHF European League", "short": "EL", "host": "https://ehfel.eurohandball.com",
           "cid": "0LdFJEv4Xeq34p7ZoHYnNQ"},
    "ec": {"name": "EHF European Cup", "short": "EC", "host": "https://ehfec.eurohandball.com",
           "cid": "0NO-qzR3XKKIL4ING3vbOA"},
}
COMPS.update({k: {"name": v["name"], "short": v["short"], "ehf": True} for k, v in EHF.items()})
EHF_DE = {"Group Phase": "Gruppenphase", "Main Round": "Hauptrunde", "Play-Offs": "Play-offs",
          "Last 16": "Achtelfinal", "Quarter-finals": "Viertelfinal", "Semi-finals": "Halbfinal",
          "Finals": "Final", "Final": "Final", "Round 1": "1. Runde", "Round 2": "2. Runde",
          "Round 3": "3. Runde", "Qualification Round 1": "1. Qualifikationsrunde",
          "Qualification Round 2": "2. Qualifikationsrunde"}
EHF_POS = {"Goalkeeper": "Torhüter", "Left Wing": "Flügel links", "Right Wing": "Flügel rechts",
           "Left Back": "Rückraum links", "Right Back": "Rückraum rechts", "Centre Back": "Rückraum Mitte",
           "Back": "Rückraum", "Line Player": "Kreis", "Pivot": "Kreis"}
EHF_NAT = {"SUI": "Schweiz", "GER": "Deutschland", "AUT": "Österreich", "FRA": "Frankreich", "ITA": "Italien",
           "ESP": "Spanien", "POR": "Portugal", "DEN": "Dänemark", "SWE": "Schweden", "NOR": "Norwegen",
           "ISL": "Island", "FIN": "Finnland", "FAR": "Färöer", "NED": "Niederlande", "BEL": "Belgien",
           "LUX": "Luxemburg", "POL": "Polen", "CZE": "Tschechien", "SVK": "Slowakei", "HUN": "Ungarn",
           "ROU": "Rumänien", "BUL": "Bulgarien", "GRE": "Griechenland", "TUR": "Türkei", "CRO": "Kroatien",
           "SLO": "Slowenien", "SRB": "Serbien", "BIH": "Bosnien und Herzegowina", "MNE": "Montenegro",
           "MKD": "Nordmazedonien", "KOS": "Kosovo", "ALB": "Albanien", "UKR": "Ukraine", "LTU": "Litauen",
           "LAT": "Lettland", "EST": "Estland", "ISR": "Israel", "CYP": "Zypern", "GBR": "Grossbritannien",
           "IRL": "Irland", "GEO": "Georgien", "ARM": "Armenien", "AZE": "Aserbaidschan", "MDA": "Moldau",
           "BLR": "Belarus", "RUS": "Russland", "BRA": "Brasilien", "ARG": "Argentinien", "TUN": "Tunesien",
           "EGY": "Ägypten", "ALG": "Algerien", "JPN": "Japan", "KOR": "Südkorea", "CUB": "Kuba",
           "USA": "USA", "CAN": "Kanada", "CHI": "Chile", "URU": "Uruguay", "IRI": "Iran", "QAT": "Katar",
           "BRN": "Bahrain", "KSA": "Saudi-Arabien", "ANG": "Angola", "CMR": "Kamerun", "SEN": "Senegal",
           "MAR": "Marokko", "CPV": "Kap Verde", "MLT": "Malta", "AND": "Andorra", "MON": "Monaco"}
EHF_BIO, EHF_PHOTO = {}, {}


def ehf_get(host, path, params, tries=3):
    url = host + path + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MatchHub/1.0",
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40, context=_ctx) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def ehf_id(s):
    """Stabile Zahl aus einer EHF-ID (die App arbeitet mit numerischen IDs)."""
    return 10 ** 15 + int(hashlib.sha1(s.encode()).hexdigest()[:11], 16)


def _norm(s):
    s = str(s).translate(str.maketrans({"ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "æ": "ae", "Æ": "Ae",
                                         "ø": "o", "Ø": "O", "ß": "ss", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D"}))
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s)


def _name_key(s):
    return " ".join(sorted(_norm(s).split()))


def _team_key(s):
    stop = {"hc", "hsc", "tsv", "sg", "bsv", "handball", "club", "rtv", "kc", "rk", "hb", "1879", "st"}
    return " ".join(w for w in _norm(s).split() if w not in stop)


def ehf_swiss_map(games):
    """EHF-Teamname -> (handball.ch teamId, clubId) für Schweizer Teams aus QHL/NLB."""
    lev = {"qhl": {}, "nlb": {}}
    for g in games:
        if g.get("comp") in lev:
            lev[g["comp"]][g["homeTeamName"]] = (g["homeTeamId"], g.get("homeTeamClubId"))
            lev[g["comp"]][g["awayTeamName"]] = (g["awayTeamId"], g.get("awayTeamClubId"))
    def find(name):
        for comp in ("qhl", "nlb"):  # erste Mannschaft (QHL) hat Vorrang
            teams = lev[comp]
            full = {_norm(n).strip(): v for n, v in teams.items()}
            if _norm(name).strip() in full:
                return full[_norm(name).strip()]
            keyed = {_team_key(n): v for n, v in teams.items()}
            k = _team_key(name)
            if k in keyed:
                return keyed[k]
            best = difflib.get_close_matches(k, list(keyed), n=1, cutoff=0.6)
            if best:
                return keyed[best[0]]
        return None
    return find


def ehf_round(m):
    c = m["comp"]
    rnd, ph = (c.get("round") or {}).get("name") or "", (c.get("phase") or {}).get("name") or ""
    grp = (c.get("group") or {}).get("name")
    if grp:
        return f"Gruppe {grp}", EHF_DE.get(rnd, rnd)
    if "EHF Finals" in rnd:
        return "Final4 · " + EHF_DE.get(ph, ph), "Final4 · " + EHF_DE.get(ph, ph)
    return EHF_DE.get(rnd, rnd), EHF_DE.get(rnd, rnd)


def ehf_collect(games_hb):
    """Liefert Spiele (handball.ch-Format), Tabellen und Runden für EL und EC."""
    swiss = ehf_swiss_map(games_hb)
    games, standings, rounds = [], {}, {}
    for key, c in EHF.items():
        d = ehf_get(c["host"], "/umbraco/api/competitionapi/GetStandingMatches", {"competitionId": c["cid"]})
        ms = d.get("matches") or []
        order, rlist = {}, []
        legs = {}
        for m in ms:
            ht, at = m["homeTeam"]["team"], m["guestTeam"]["team"]
            if not ht.get("nationAbbreviation") or not at.get("nationAbbreviation"):
                continue  # Platzhalter wie "P01-A" (Gegner noch offen)
            r, phase = ehf_round(m)
            if not r.startswith("Gruppe") and r not in rlist:
                rlist.append(r)
            ids = []
            for t in (ht, at):
                hb = swiss(t["name"]) if t.get("nationAbbreviation") == "SUI" else None
                ids.append((hb[0], hb[1], None) if hb else (ehf_id(t["id"]), None, t.get("logoSmall")))
            sc_h, sc_a = m["homeTeam"]["score"], m["guestTeam"]["score"]
            fin, live = bool(m.get("isFinished")), bool(m.get("isLive"))
            dte = m["venue"]["date"]
            t = dte.get("cet") if dte.get("hasTime") else (dte.get("cet") or "")[:10] + "T00:00:00"
            pl = m["venue"].get("place") or {}
            g = {"objectId": int(m["matchID"]), "gameDateTime": t, "isLive": live,
                 "homeTeamId": ids[0][0], "homeTeamName": ht.get("fullName") or ht["name"], "homeTeamShortName": ht.get("shortName") or ht.get("nationAbbreviation"),
                 "homeTeamClubId": ids[0][1], "homeTeamScore": sc_h.get("goals"), "homeTeamScoreInterval": sc_h.get("goalsHalfTime"),
                 "awayTeamId": ids[1][0], "awayTeamName": at.get("fullName") or at["name"], "awayTeamShortName": at.get("shortName") or at.get("nationAbbreviation"),
                 "awayTeamClubId": ids[1][1], "awayTeamScore": sc_a.get("goals"), "awayTeamScoreInterval": sc_a.get("goalsHalfTime"),
                 "venueName": ", ".join(x for x in (pl.get("hallName"), pl.get("cityName")) if x) or None,
                 "gameStatusId": 2 if fin else 1, "gameStatus": "Gespielt" if fin else ("Live" if live else "Angesetzt"),
                 "spectators": pl.get("spectatorsCount"), "comp": key, "round": r,
                 "_ehf": {"host": c["host"], "url": c["host"] + (m.get("url") or ""), "hl": ids[0][2], "al": ids[1][2],
                          "hn": ht.get("nationAbbreviation"), "an": at.get("nationAbbreviation"),
                          "leg": (m["comp"].get("legName") if not r.startswith("Gruppe") else None),
                          "md": (m["comp"].get("legName") if r.startswith("Gruppe") else None),
                          "pair": m.get("pairId"), "ehfh": ht["id"], "ehfa": at["id"]}}
            games.append(g)
            if g["_ehf"]["leg"] and m.get("pairId"):
                legs.setdefault(m["pairId"], []).append(g)
        # Hin- und Rückspiel: Gesamtskore im Rückspiel
        for pair in legs.values():
            pair.sort(key=lambda x: x["gameDateTime"])
            if len(pair) == 2:
                pair[0]["_ehf"]["leg"], pair[1]["_ehf"]["leg"] = 1, 2
            else:
                for x in pair:
                    x["_ehf"]["leg"] = None
            if len(pair) == 2 and all(x["gameStatusId"] == 2 for x in pair):
                a, b = pair
                hb = (b["homeTeamScore"] or 0) + (a["awayTeamScore"] or 0)
                ab = (b["awayTeamScore"] or 0) + (a["homeTeamScore"] or 0)
                b["_ehf"]["tot"] = [hb, ab]
        # Gruppentabellen aus den Resultaten
        groups = {}
        for g in games:
            if g["comp"] == key and g["round"].startswith("Gruppe"):
                groups.setdefault(g["round"], []).append(g)
        for gname in sorted(groups):
            rows = ehf_table(groups[gname])
            if rows:
                standings.setdefault(key, []).append({"group": gname, "rows": rows})
        rounds[key] = (["Gruppenphase"] if groups else []) + rlist
    return games, standings, rounds


def ehf_table(gs):
    tm = {}
    for g in gs:
        for tid, n in ((g["homeTeamId"], g["homeTeamName"]), (g["awayTeamId"], g["awayTeamName"])):
            tm.setdefault(tid, {"teamId": tid, "name": n, "games": 0, "won": 0, "drawn": 0, "lost": 0,
                                "goalsFor": 0, "goalsAgainst": 0, "points": 0, "_f": []})
    fin = sorted([g for g in gs if g["gameStatusId"] == 2], key=lambda g: g["gameDateTime"])
    for g in fin:
        h, a = tm[g["homeTeamId"]], tm[g["awayTeamId"]]
        hg, ag = g["homeTeamScore"] or 0, g["awayTeamScore"] or 0
        for x, f, o in ((h, hg, ag), (a, ag, hg)):
            x["games"] += 1; x["goalsFor"] += f; x["goalsAgainst"] += o
            res = "w" if f > o else "l" if f < o else "d"
            x["won" if res == "w" else "lost" if res == "l" else "drawn"] += 1
            x["points"] += 2 if res == "w" else 1 if res == "d" else 0
            x["_f"].append(res)

    def h2h(ids):
        sub = {i: [0, 0, 0] for i in ids}
        for g in fin:
            if g["homeTeamId"] in sub and g["awayTeamId"] in sub:
                hg, ag = g["homeTeamScore"] or 0, g["awayTeamScore"] or 0
                sub[g["homeTeamId"]][0] += 2 if hg > ag else 1 if hg == ag else 0
                sub[g["awayTeamId"]][0] += 2 if ag > hg else 1 if hg == ag else 0
                sub[g["homeTeamId"]][1] += hg - ag; sub[g["awayTeamId"]][1] += ag - hg
                sub[g["homeTeamId"]][2] += hg; sub[g["awayTeamId"]][2] += ag
        return sub
    rows = list(tm.values())
    by_pts = {}
    for r in rows:
        by_pts.setdefault(r["points"], []).append(r["teamId"])
    hh = {}
    for ids in by_pts.values():
        if len(ids) > 1:
            n = sum(1 for g in fin if g["homeTeamId"] in ids and g["awayTeamId"] in ids)
            if n == len(ids) * (len(ids) - 1):  # Direktvergleich erst, wenn alle Duelle gespielt sind
                hh.update(h2h(ids))
    rows.sort(key=lambda r: (-r["points"], *[-v for v in hh.get(r["teamId"], [0, 0, 0])],
                             -(r["goalsFor"] - r["goalsAgainst"]), -r["goalsFor"], r["name"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["goalDifference"] = r["goalsFor"] - r["goalsAgainst"]
        r["lastFive"] = "".join(r.pop("_f")[-5:])
    return rows


def ehf_stats(g):
    """Spielerstatistik eines EHF-Spiels im Format von gamePlayerStats (handball.ch)."""
    e = g["_ehf"]
    d = ehf_get(e["host"], "/umbraco/api/matchdetailapi/GetMatchDetails",
                {"matchId": g["objectId"], "culture": "en-US"})
    det = ((d or {}).get("matchDetails") or {}).get("details") or {}
    out = []
    for side, tid, tname in (("homeTeam", g["homeTeamId"], g["homeTeamName"]), ("guestTeam", g["awayTeamId"], g["awayTeamName"])):
        for p in (det.get(side) or {}).get("players") or []:
            if not p.get("isPlayer", True) or p.get("isTeamOfficial"):
                continue
            per, s = p.get("person") or {}, p.get("score") or {}
            pid = ehf_id(p["id"]) if p.get("id") else None
            if not pid:
                continue
            gk = bool(p.get("isGoalkeeper"))
            out.append({"teamId": tid, "teamName": tname, "playerId": pid,
                        "playerName": f"{(per.get('lastName') or '').upper()} {per.get('firstName') or ''}".strip(),
                        "dressNr": p.get("shirtNumber") if str(p.get("shirtNumber") or "").isdigit() else None,
                        "function": "goalkeeper" if gk else "player",
                        "totalScore": s.get("goals"), "totalShots": s.get("shots"), "totalScore7m": 0, "totalShots7m": 0,
                        "technicalErrors": 0, "total2Minutes": s.get("twoMinPenaltiesCount"),
                        "totalSuspension": s.get("redCardsCount"), "totalWarnings": s.get("warningsCount"),
                        "totalSaves": s.get("goalkeeperSaves"), "totalShotsGK": s.get("goalkeeperRecievedShots"),
                        "_k": _name_key(f"{per.get('firstName') or ''} {per.get('lastName') or ''}")})
            dob = per.get("dateOfBirth") or ""
            EHF_BIO[pid] = {"pos": EHF_POS.get(p.get("playingPosition"), p.get("playingPosition")) or ("Torhüter" if gk else None),
                            "yr": int(dob[:4]) if dob[:4].isdigit() and dob[:4] > "1900" else None, "hd": None,
                            "ct": EHF_NAT.get(per.get("nationAbbreviation"), per.get("nationAbbreviation"))}
            ph = (p.get("newPhoto") or p.get("photos") or {})
            if ph.get("hasLargePhotos") and ph.get("w180"):
                EHF_PHOTO[pid] = (tid, ph["w180"])
    return out


def ehf_link_players(stats, games_by_id):
    """Schweizer Spieler in EL/EC mit ihrer handball.ch-ID verknüpfen (gleiches Team, gleicher Name)."""
    idx = {}
    for gid, lines in stats.items():
        if not lines or games_by_id[gid].get("_ehf"):
            continue
        for p in lines:
            idx[(p["teamId"], _name_key(p["playerName"]))] = p["playerId"]
    for gid, lines in stats.items():
        if not lines or not games_by_id[gid].get("_ehf"):
            continue
        for p in lines:
            k = p.pop("_k", None)
            hb = idx.get((p["teamId"], k)) if k else None
            if not hb and k:  # zweite Vornamen: Namensteile als Teilmenge vergleichen
                toks = set(k.split())
                cand = {v for (t, n), v in idx.items() if t == p["teamId"] and (set(n.split()) <= toks or toks <= set(n.split()))}
                hb = cand.pop() if len(cand) == 1 else None
            if hb:
                if p["playerId"] in EHF_PHOTO:
                    EHF_PHOTO[hb] = EHF_PHOTO.pop(p["playerId"])
                EHF_BIO.setdefault(hb, EHF_BIO.get(p["playerId"]))
                p["playerId"] = hb


def main(out_dir="."):
    games, standings, rounds = collect()
    eg, est, er = ehf_collect(games)
    games += eg; standings.update(est); rounds.update(er)
    done = [g for g in games if played(g) or g["isLive"]]

    def fetch(g):
        try:
            if g.get("_ehf"):
                return g["objectId"], ehf_stats(g)
            return g["objectId"], gql(PLAYER_Q, {"g": g["objectId"], "l": bool(g["isLive"])})["gamePlayerStats"] or []
        except Exception:
            return g["objectId"], None

    with ThreadPoolExecutor(6) as ex:
        stats = dict(ex.map(fetch, done))
    ehf_link_players(stats, {g["objectId"]: g for g in games})

    players = {k: {} for k in COMPS}
    game_rows = []
    for g in sorted(games, key=lambda x: x["gameDateTime"] or ""):
        row = {
            "id": g["objectId"], "c": g["comp"], "r": g["round"], "t": g["gameDateTime"],
            "live": bool(g["isLive"]), "st": g.get("gameStatusId"), "sx": g.get("gameStatus"),
            "sp": g.get("spectators"), "h": g["homeTeamName"], "a": g["awayTeamName"],
            "hs": g["homeTeamShortName"], "as": g["awayTeamShortName"], "hid": g["homeTeamId"], "aid": g["awayTeamId"], "hc": g.get("homeTeamClubId"), "ac": g.get("awayTeamClubId"),
            "hg": g["homeTeamScore"], "ag": g["awayTeamScore"],
            "hht": g["homeTeamScoreInterval"], "aht": g["awayTeamScoreInterval"], "v": g["venueName"],
        }
        e = g.get("_ehf")
        if e:
            row["u"] = e["url"]
            for k in ("leg", "md", "tot"):
                if e.get(k):
                    row[k] = e[k]
        if not (played(g) or g["isLive"]):
            row["hg"] = row["ag"] = row["hht"] = row["aht"] = None
        lines = stats.get(g["objectId"])
        if lines:
            best = {}
            for p in lines:
                gk = p["function"] == "goalkeeper"
                key = p["playerId"]
                agg = players[g["comp"]].setdefault(key, {
                    "id": key, "n": p["playerName"], "tm": p["teamName"], "gp": 0, "g": 0, "s": 0, "g7": 0,
                    "s7": 0, "te": 0, "m2": 0, "dq": 0, "yc": 0, "sv": 0, "sg": 0, "gk": 0})
                agg["tm"] = p["teamName"]; agg["tid"] = p["teamId"]
                if p.get("dressNr"):
                    agg["nr"] = p["dressNr"]
                agg.setdefault("log", []).append([g["objectId"], num(p["totalScore"]), num(p["totalShots"]),
                    num(p["totalScore7m"]), num(p["totalShots7m"]), num(p["totalSaves"]), num(p["totalShotsGK"]),
                    num(p["total2Minutes"]), num(p["totalWarnings"]), num(p["totalSuspension"]), num(p["technicalErrors"]), p["teamId"]]) 
                agg["gp"] += 1
                agg["g"] += num(p["totalScore"]); agg["s"] += num(p["totalShots"])
                agg["g7"] += num(p["totalScore7m"]); agg["s7"] += num(p["totalShots7m"])
                agg["te"] += num(p["technicalErrors"]); agg["m2"] += num(p["total2Minutes"])
                agg["dq"] += num(p["totalSuspension"]); agg["yc"] += num(p["totalWarnings"])
                if gk or num(p["totalShotsGK"]):
                    agg["gk"] += 1
                    agg["sv"] += num(p["totalSaves"]); agg["sg"] += num(p["totalShotsGK"])
                # per-game highlights
                if num(p["totalScore"]) > best.get("sc", (None, -1))[1]:
                    best["sc"] = (p, num(p["totalScore"]))
                if num(p["totalSaves"]) > best.get("gk", (None, -1))[1]:
                    best["gk"] = (p, num(p["totalSaves"]))
            if "sc" in best and best["sc"][1] > 0:
                p = best["sc"][0]
                row["ts"] = [p["playerName"], p["teamName"], best["sc"][1], p["playerId"]]
            if "gk" in best and best["gk"][1] > 0:
                p = best["gk"][0]
                pct = round(100 * num(p["totalSaves"]) / num(p["totalShotsGK"])) if num(p["totalShotsGK"]) else None
                row["tg"] = [p["playerName"], p["teamName"], best["gk"][1], pct, p["playerId"]]
        game_rows.append(row)

    # Steckbrief (Position, Jahrgang, Wurfhand, Nation) aus den Kaderlisten der Liga-Teams
    tids = {p["tid"] for k in COMPS for p in players[k].values() if p.get("tid") and p["tid"] < 10 ** 15}
    def roster(t):
        try:
            d = gql("query($t:Int){playerStaff(teamId:$t){player{playerId,year,position,hand,countryName}}}", {"t": t})["playerStaff"]
            d = d[0] if isinstance(d, list) else d
            return (d or {}).get("player") or []
        except Exception:
            return []
    bio = {}
    with ThreadPoolExecutor(6) as ex:
        for lst in ex.map(roster, sorted(tids)):
            for r in lst:
                bio[r["playerId"]] = {"pos": r.get("position"), "yr": r.get("year"), "hd": r.get("hand"), "ct": r.get("countryName")}
    for k in COMPS:
        for pid, p in players[k].items():
            if pid in bio:
                p["b"] = bio[pid]
            elif EHF_BIO.get(pid):
                p["b"] = EHF_BIO[pid]

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {"updatedAt": now, "season": "2026/27", "source": "handball.ch Matchcenter",
            "comps": {k: {"name": v["name"], "short": v["short"], "rounds": rounds.get(k, [])} for k, v in COMPS.items()},
            "standings": standings,
            "counts": {"games": len(game_rows), "withStats": sum(1 for v in stats.values() if v)}}
    os.makedirs(out_dir, exist_ok=True)
    w = lambda name, obj: json.dump(obj, open(os.path.join(out_dir, name), "w"), ensure_ascii=False, separators=(",", ":"))
    w("hub_meta.json", meta)
    w("hub_games.json", {"updatedAt": now, "games": game_rows})
    for k in COMPS:
        w(f"hub_players_{k}.json", {"updatedAt": now, "players": list(players[k].values())})
    # Logo- und Foto-Adressen der EHF-Teams für --logos / --ehf-portraits (wird nicht veröffentlicht)
    ex_logos = {}
    for g in games:
        e = g.get("_ehf")
        if e:
            for tid, u in ((g["homeTeamId"], e["hl"]), (g["awayTeamId"], e["al"])):
                if u:
                    ex_logos[str(tid)] = u
    json.dump({"logos": ex_logos, "photos": {str(k): v for k, v in EHF_PHOTO.items()}},
              open(os.path.join(out_dir, "ehf_extra.json"), "w"), separators=(",", ":"))
    print(json.dumps(meta["counts"]), {k: len(v) for k, v in players.items()})



IMG = "https://www.handball.ch/images/player_portrait/{pid}.png?parentId={tid}&width=200&height=200&rmode=crop&format=jpg"


def get_bytes(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MatchHub/1.0"})
            with urllib.request.urlopen(req, timeout=30, context=_ctx) as r:
                return r.read()
        except Exception:
            time.sleep(1 + i)
    return None


def portraits(out_dir):
    """Spielerporträts aus dem Matchcenter als data/pt/<teamId>.json {playerId: dataURL}."""
    import base64, glob
    pairs = {}
    for f in glob.glob(os.path.join(out_dir, "hub_players_*.json")):
        for p in json.load(open(f))["players"]:
            for l in p.get("log") or []:
                pairs[(p["id"], l[11])] = 1
    blank = {get_bytes(IMG.format(pid=99999999, tid=t)) for t in {t for _, t in list(pairs)[:1]}}
    def one(k):
        b = get_bytes(IMG.format(pid=k[0], tid=k[1]))
        return k, (b if b and b not in blank and len(b) > 1500 else None)
    teams = {}
    with ThreadPoolExecutor(8) as ex:
        for (pid, tid), b in ex.map(one, list(pairs)):
            if b:
                teams.setdefault(tid, {})[str(pid)] = "data:image/jpeg;base64," + base64.b64encode(b).decode()
    d = os.path.join(out_dir, "pt"); os.makedirs(d, exist_ok=True)
    for tid, m in teams.items():
        json.dump(m, open(os.path.join(d, f"{tid}.json"), "w"), separators=(",", ":"))
    json.dump(sorted(teams), open(os.path.join(out_dir, "pt_index.json"), "w"))
    print("portraits", sum(len(m) for m in teams.values()), "teams", len(teams), "pairs", len(pairs))



LOGO = "https://www.handball.ch/images/logo/{tid}.png?fallbackType=club&fallbackId={cid}&height=64&width=64&rmode=pad&format=webp"


def logos(out_dir):
    """Teamlogos als data/logos.json {teamId: dataURL}."""
    import base64
    games = json.load(open(os.path.join(out_dir, "hub_games.json")))["games"]
    teams = {}
    for g in games:
        teams[g["hid"]] = g.get("hc"); teams[g["aid"]] = g.get("ac")
    try:
        ehf = json.load(open(os.path.join(out_dir, "ehf_extra.json")))["logos"]
    except Exception:
        ehf = {}
    def one(k):
        if str(k[0]) in ehf:
            return k, get_bytes(ehf[str(k[0])])
        if k[0] >= 10 ** 15:
            return k, None
        return k, get_bytes(LOGO.format(tid=k[0], cid=k[1] or 0))
    out = {}
    with ThreadPoolExecutor(8) as ex:
        for (tid, _), b in ex.map(one, list(teams.items())):
            if b and len(b) > 300:
                mt = "image/jpeg" if b[:3] == b"\xff\xd8\xff" else "image/png" if b[:4] == b"\x89PNG" else "image/webp"
                out[str(tid)] = f"data:{mt};base64," + base64.b64encode(b).decode()
    json.dump(out, open(os.path.join(out_dir, "logos.json"), "w"), separators=(",", ":"))
    print("logos", len(out), "of", len(teams))


def ehf_portraits(out_dir):
    """Spielerfotos der EHF-Teams als data/pt/<teamId>.json; ergänzt ein vorhandenes pt_index.json."""
    import base64
    photos = json.load(open(os.path.join(out_dir, "ehf_extra.json")))["photos"]
    def one(it):
        pid, (tid, url) = it
        if tid < 10 ** 15:  # Schweizer Teams: Fotos kommen aus dem Matchcenter
            return pid, tid, None
        b = get_bytes(url)
        if b and len(b) > 1500:
            try:  # auf 120×120 zuschneiden, spart rund 80 % Platz
                import io
                from PIL import Image
                im = Image.open(io.BytesIO(b)).convert("RGB"); w, h = im.size; s = min(w, h)
                y = max(0, int((h - s) / 2 - s * .08)); x = (w - s) // 2
                im = im.crop((x, y, x + s, y + s)).resize((120, 120), Image.LANCZOS)
                o = io.BytesIO(); im.save(o, "JPEG", quality=78, optimize=True); b = o.getvalue()
            except Exception:
                pass
            return pid, tid, b
        return pid, tid, None
    teams = {}
    with ThreadPoolExecutor(8) as ex:
        for pid, tid, b in ex.map(one, list(photos.items())):
            if b:
                teams.setdefault(tid, {})[pid] = "data:image/jpeg;base64," + base64.b64encode(b).decode()
    d = os.path.join(out_dir, "pt"); os.makedirs(d, exist_ok=True)
    for tid, m in teams.items():
        f = os.path.join(d, f"{tid}.json")
        if os.path.exists(f):  # Schweizer Team: vorhandene Matchcenter-Fotos behalten
            old = json.load(open(f)); m = {**m, **old}
        json.dump(m, open(f, "w"), separators=(",", ":"))
    ip = os.path.join(out_dir, "pt_index.json")
    idx = set(json.load(open(ip))) if os.path.exists(ip) else set()
    json.dump(sorted(idx | set(teams)), open(ip, "w"))
    print("ehf portraits", sum(len(m) for m in teams.values()), "teams", len(teams))


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "."
    if "--portraits-only" in sys.argv:
        portraits(out)
    elif "--logos-only" in sys.argv:
        logos(out)
    elif "--ehf-portraits-only" in sys.argv:
        ehf_portraits(out)
    else:
        main(out)
        if "--portraits" in sys.argv:
            portraits(out)
        if "--logos" in sys.argv:
            logos(out)
