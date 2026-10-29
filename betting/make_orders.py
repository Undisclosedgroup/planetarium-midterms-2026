#!/usr/bin/env python3
"""Planetarium midterms 2026: build the Kalshi order sheet from the locked forecast and live prices.

Usage:
    python3 make_orders.py FORECAST.json --bankroll 1400 --out orders.csv
    python3 make_orders.py FORECAST.json --bankroll 10000 --placed orders-filled.csv --out topup.csv

The rule (BETTING-RULE.md) is fixed; only prices and the bankroll change between runs.
Stakes are shares of the bankroll B; the three layers add up to B (40% / 10% / 50%).
  Layer 1  40% of B split evenly across our pick in every race Kalshi prices at 90c or less.
  Layer 2  Extra on our pick, quarter-Kelly on a 50/50 blend of our chance and Kalshi's mid,
           where the blend beats the ask by 4c or more after fees, price 8-92c,
           open interest >= $20,000, our chance > 50%; per-race cap 7.5% of B before scaling;
           races resting on campaign-only polls or ranked-choice transfer estimates
           weighted 0.4 / 0.5; the layer scaled to 10% of B in total.
  Layer 3  5% of B on each of the ten anchors.
With --placed, the sheet lists only the top-up per market: target stake minus what was already spent there.
"""
import argparse, csv, json, math, re, sys, time, unicodedata, urllib.parse, urllib.request
from datetime import datetime, timezone

API = "https://api.elections.kalshi.com/trade-api/v2/events"
BASE_SHARE = 0.40
CONVICTION_SHARE = 0.10
ANCHOR_SHARE = 0.05
CAP_SHARE = 0.075
ANCHORS = ["NY-GOV", "MA-GOV", "IL-GOV", "MN-H05", "TN-GOV", "AL-GOV", "AR-SEN", "SD-SEN", "ID-SEN", "TN-SEN"]
DOWNWEIGHT = {"OH-H07": 0.4, "KY-H06": 0.4, "FL-H22": 0.4, "AK-SEN": 0.5}


def fee(p):
    """Kalshi taker fee per contract, in dollars (0.07 x P x (1-P))."""
    return 0.07 * p * (1 - p)


def order_cost(n, p):
    """Cost of n contracts at price p, with Kalshi's fee rounded up to the cent per order."""
    return round(n * p + math.ceil(round(0.07 * n * p * (1 - p) * 100, 6)) / 100, 2)


def contracts_for(stake, p):
    n = math.floor(stake / (p + fee(p)))
    while n > 0 and order_cost(n, p) > stake:
        n -= 1
    return n


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def norm(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def fetch_events():
    events, cursor = [], ""
    for _ in range(200):
        url = API + "?status=open&limit=200&with_nested_markets=true"
        if cursor:
            url += "&cursor=" + urllib.parse.quote(cursor)
        for attempt in range(8):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    d = json.load(r)
                if "error" in d:
                    raise RuntimeError(d["error"])
                break
            except Exception:
                time.sleep(2 + 2 * attempt)
        else:
            sys.exit("Kalshi API unreachable; try again in a minute.")
        events += d.get("events", [])
        cursor = d.get("cursor", "")
        if not cursor or not d.get("events"):
            break
        time.sleep(1.2)
    return events


def side_of(rule):
    r = (rule or "").lower()
    if "democratic party" in r:
        return "D"
    if "republican party" in r:
        return "R"
    return None


def match_markets(forecast, events):
    contests = {c["id"]: c for c in forecast["contests"]}
    rows = []
    for e in events:
        et = e["event_ticker"]
        m = re.match(r"^(SENATE|GOVPARTY)([A-Z]{2})(S?)-26$", et)
        h = re.match(r"^KXHOUSERACE-([A-Z]{2})(\d{2}|AL)-26$", et)
        if not (m or h):
            continue
        if m:
            st = m.group(2)
            cid = st + ("-SEN" if m.group(1) == "SENATE" else "-GOV")
            if m.group(1) == "SENATE" and (m.group(3) == "S" or st in ("OH", "FL")):
                cid = st + "-SEN-S"
        else:
            cid = f"{h.group(1)}-H{'00' if h.group(2) == 'AL' else h.group(2)}"
        c = contests.get(cid)
        if not c:
            continue
        for mk in e.get("markets", []):
            ya, na, yb = fnum(mk.get("yes_ask_dollars")), fnum(mk.get("no_ask_dollars")), fnum(mk.get("yes_bid_dollars"))
            if not ya or not na:
                continue
            nm = norm(mk.get("yes_sub_title"))
            s = side_of(mk.get("rules_primary"))
            party_market = bool(s) and (h or m.group(1) == "GOVPARTY" or "party" in nm)
            if party_market:
                chance = sum(x["chance"] for x in c["candidates"] if x["side"] == s)
                label = f"{s}: {mk.get('yes_sub_title')}"
            else:
                cand = None
                for x in c["candidates"]:
                    lead = norm(x["name"].split("/")[0]).replace(",", " ").split()
                    sur = [w for w in lead if len(w) > 2 and w not in ("jr.", "sr.", "iii")]
                    if sur and any(w in nm.split() for w in sur):
                        cand = x
                        break
                if not cand:
                    continue
                chance, label = cand["chance"], cand["name"]
            rows.append(dict(race=cid, pick=label, ticker=mk["ticker"], our_chance=round(chance, 4),
                             ask=ya, bid=yb, open_interest=fnum(mk.get("open_interest_fp")) or 0))
    return rows


def build(rows, bankroll, placed=None):
    by = {}
    for r in rows:
        by.setdefault(r["race"], []).append(r)
    pick = {cid: max(rs, key=lambda x: x["our_chance"]) for cid, rs in by.items()}
    orders = {}

    def add(r, amt, layer):
        o = orders.setdefault(r["ticker"], dict(race=r["race"], pick=r["pick"], ticker=r["ticker"], side="YES",
                                                our_chance=r["our_chance"], limit=r["ask"], stake=0, layers=[]))
        o["stake"] += amt
        o["layers"].append(layer)

    base = [r for r in pick.values() if r["ask"] <= 0.90]
    for r in base:
        add(r, BASE_SHARE * bankroll / len(base), "1")
    conv = []
    for r in pick.values():
        a = r["ask"]
        mid = (a + (r["bid"] or a)) / 2
        blend = 0.5 * r["our_chance"] + 0.5 * mid
        if r["open_interest"] >= 20000 and 0.08 <= a <= 0.92 and r["our_chance"] > 0.5 and blend - a - fee(a) >= 0.04:
            k = (blend - a) / (1 - a)
            conv.append((r, min(0.25 * k * bankroll, CAP_SHARE * bankroll) * DOWNWEIGHT.get(r["race"], 1.0)))
    total = sum(s for _, s in conv)
    for r, s in conv:
        add(r, s * CONVICTION_SHARE * bankroll / total, "2")
    for cid in ANCHORS:
        if cid in pick:
            add(pick[cid], ANCHOR_SHARE * bankroll, "3")
        else:
            print(f"warning: anchor {cid} has no Kalshi market today", file=sys.stderr)
    out = []
    for o in orders.values():
        a = o["limit"]
        o["target_stake"] = round(o["stake"], 2)
        o["already_spent"] = round((placed or {}).get(o["ticker"], 0.0), 2)
        o["stake"] = round(max(0.0, o["stake"] - o["already_spent"]), 2)
        o["contracts"] = contracts_for(o["stake"], a)
        if o["contracts"] == 0:
            continue
        o["cost"] = order_cost(o["contracts"], a)
        o["profit_if_right"] = round(o["contracts"] - o["cost"], 2)
        o["layers"] = "+".join(o["layers"])
        out.append(o)
    out.sort(key=lambda o: (o["layers"], -o["stake"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("forecast")
    ap.add_argument("--out", default="orders.csv")
    ap.add_argument("--bankroll", type=float, required=True)
    ap.add_argument("--placed", help="earlier sheet with filled_contracts and fill_price filled in")
    ap.add_argument("--events", help="saved Kalshi events JSON instead of a live pull")
    args = ap.parse_args()
    forecast = json.load(open(args.forecast))
    events = json.load(open(args.events)) if args.events else fetch_events()
    placed = {}
    if args.placed:
        for r in csv.DictReader(open(args.placed)):
            n, px = fnum(r.get("filled_contracts")), fnum(r.get("fill_price"))
            if n and px:
                placed[r["ticker"]] = placed.get(r["ticker"], 0.0) + order_cost(int(n), px)
    rows = match_markets(forecast, events)
    orders = build(rows, args.bankroll, placed)
    fields = ["race", "pick", "ticker", "side", "limit", "contracts", "cost", "stake", "our_chance", "profit_if_right",
              "layers", "target_stake", "already_spent", "filled_contracts", "fill_price"]
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for o in orders:
            w.writerow({k: o.get(k, "") for k in fields})
    cost = sum(o["cost"] for o in orders)
    ev = sum(o["our_chance"] * o["profit_if_right"] - (1 - o["our_chance"]) * o["cost"] for o in orders)
    right = sum(o["profit_if_right"] for o in orders)
    anchors_only = sum(o["profit_if_right"] for o in orders if o["layers"] == "3") - sum(o["cost"] for o in orders if o["layers"] != "3")
    print(f"prices pulled {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"forecast {forecast['about']['version']} generated {forecast['about']['generated_at']}")
    print(f"bankroll ${args.bankroll:,.0f}; {len(orders)} orders, cost ${cost:,.2f} -> {args.out}")
    print(f"  layer 1: {sum(1 for o in orders if '1' in o['layers'])} races; layer 2: {sum(1 for o in orders if '2' in o['layers'])}; layer 3: {sum(1 for o in orders if '3' in o['layers'])}")
    print(f"  forecast right everywhere: +${right:,.0f}; anchors win, rest lose: ${anchors_only:,.0f}; expected (our numbers): +${ev:,.0f}")


if __name__ == "__main__":
    main()
