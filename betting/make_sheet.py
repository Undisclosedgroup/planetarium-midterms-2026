#!/usr/bin/env python3
"""Planetarium midterms 2026: build the Kalshi order sheet from the sealed forecast and Kalshi's order books.

    python3 make_sheet.py FORECAST.json --bankroll 10000 --out orders.csv
    python3 make_sheet.py FORECAST.json --bankroll 10000 --events events.json --books books.json --out orders.csv

The rule is BETTING-RULE.md and is fixed; only prices change between runs. The race matching and fee
arithmetic are make_orders.py's. Every bet buys YES on our pick (our most likely winner), and every stake
is sized to the offers in the order book when the sheet is built, so the sheet fills as placed.
  Contrarian  $200 on our pick in every race Kalshi lists where the sealed forecast disagrees with the
              polls (where_we_disagree_with_the_polls), or where Kalshi favours the other side (our
              chance above 50%, Kalshi's midpoint below 50c), whatever the price, cheapest offers first.
  Model       The rest of B on our pick in every race Kalshi prices at 90c or less, in our ten surest
              calls (make_orders.ANCHORS) and on House control. An offer qualifies when a 50/50 blend of
              our chance and Kalshi's best ask beats that offer's price after fees. Stakes maximise
              expected return on that blend, at most $1,000 a race, with the average loss in the worst 5%
              of outcomes held to $2,600 when outcomes are drawn at Kalshi's best asks. Races share one
              national swing (correlation 0.3 between races, 0.8 for House control). Races resting on
              campaign-only polls (OH-07, KY-06, FL-22) or ranked-choice transfer estimates (AK Sen) move
              only 0.4 / 0.5 of the way from Kalshi's price to our chance in the blend. Model stakes
              under $50 a race are dropped and the rest solved again.
Each race gets one limit order at the highest offer price it uses; the sheet's cost is that price times the
contracts plus fees, so the whole sheet can never cost more than B even if every contract fills at the limit.
"""
import argparse, csv, json, sys, time, urllib.request
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import linprog
from scipy.stats import norm

import make_orders as mo

CONTRARIAN_STAKE = 200
RACE_CAP = 1000
MIN_STAKE = 50
TAIL, TAIL_LOSS = 0.95, 2600
RHO, RHO_HOUSE = 0.3, 0.8
HOUSE = "CONTROLH-2026-D"
BOOK = "https://api.elections.kalshi.com/trade-api/v2/markets/{}/orderbook"
SCENARIOS, SEED = 4000, 1          # outcomes the optimiser sees
CHECK_SCENARIOS, CHECK_SEED = 30000, 11  # outcomes the printed summary is scored on


def fetch_books(tickers):
    books = {}
    for t in tickers:
        for attempt in range(6):
            try:
                with urllib.request.urlopen(BOOK.format(t), timeout=30) as r:
                    books[t] = json.load(r)
                break
            except Exception:
                time.sleep(1 + attempt)
        else:
            sys.exit(f"order book for {t} unreachable; try again in a minute.")
        time.sleep(0.1)
    return books


def offers(book):
    """YES offers as (price, contracts), cheapest first: a NO bid at q is a YES offer at 1 - q."""
    no_bids = (book.get("orderbook_fp") or {}).get("no_dollars") or []
    out = sorted((round(1 - float(q), 4), float(n)) for q, n in no_bids)
    return [(p, n) for p, n in out if 0 < p < 1 and n >= 1]


def best_bid(book):
    yes_bids = (book.get("orderbook_fp") or {}).get("yes_dollars") or []
    return max((float(p) for p, _ in yes_bids), default=None)


def picks(forecast, events):
    by = {}
    for r in mo.match_markets(forecast, events):
        if r["our_chance"] > by.get(r["race"], {"our_chance": -1})["our_chance"]:
            by[r["race"]] = r
    out = [dict(r, side_r=r["ticker"].rsplit("-", 1)[1] == "R", rho=RHO) for r in by.values()]
    if any(m["ticker"] == HOUSE for e in events for m in e.get("markets", [])):
        out.append(dict(race="HOUSE", pick="D: House control", ticker=HOUSE, side_r=False, rho=RHO_HOUSE,
                        our_chance=forecast["chambers"]["house"]["democrats_win"]["share"]))
    else:
        print(f"warning: {HOUSE} not in the Kalshi pull", file=sys.stderr)
    return out


def our_pick_wins(c, prob, k, seed):
    """k draws of which picks win; prob[i] is the chance pick i wins. One shared national swing."""
    rng = np.random.default_rng(seed)
    side_r = np.array([x["side_r"] for x in c])
    rho = np.array([x["rho"] for x in c])
    p_r = np.where(side_r, prob, 1 - prob)
    z = np.sqrt(rho) * rng.standard_normal((k, 1)) + np.sqrt(1 - rho) * rng.standard_normal((k, len(c)))
    return (z < norm.ppf(np.clip(p_r, 1e-4, 1 - 1e-4))) == side_r


def solve(mu, ret, fixed, ub, race_of, room, model_money):
    """Maximise mu . x with 0 <= x <= ub, sum x = model_money, each race's x within its room,
    and CVaR(loss of fixed + x) <= TAIL_LOSS."""
    k, n = ret.shape
    races = sorted(set(race_of[ub > 0]))
    a_cap = np.zeros((len(races), n + 1 + k))
    for row, i in enumerate(races):
        a_cap[row, :n] = race_of == i
    c = np.concatenate([-mu, [0], np.zeros(k)])
    a_tail = np.hstack([-ret, -np.ones((k, 1)), -np.eye(k)])
    a_cvar = np.concatenate([np.zeros(n), [1], np.full(k, 1 / ((1 - TAIL) * k))])[None, :]
    res = linprog(c, A_ub=np.vstack([a_tail, a_cvar, a_cap]),
                  b_ub=np.concatenate([ret @ fixed, [TAIL_LOSS], [room[i] for i in races]]),
                  A_eq=np.concatenate([np.ones(n), [0], np.zeros(k)])[None, :], b_eq=[model_money],
                  bounds=[(0, u) for u in ub] + [(None, None)] + [(0, None)] * k, method="highs")
    return res.x[:n] if res.success else None


def build(forecast, events, books, bankroll):
    disagree = {d["id"] for d in forecast["where_we_disagree_with_the_polls"]}
    c = []
    for x in picks(forecast, events):
        book = offers(books.get(x["ticker"], {}))
        if not book:
            continue
        x["ask"], x["bid"], x["book"] = book[0][0], best_bid(books[x["ticker"]]), book
        if x["ask"] > 0.90 and x["race"] not in mo.ANCHORS and x["race"] != "HOUSE":
            continue
        mid = (x["ask"] + (x["bid"] or x["ask"])) / 2
        x["contrarian"] = x["race"] in disagree or (x["our_chance"] > 0.5 and mid < 0.5)
        x["blend"] = x["ask"] + 0.5 * mo.DOWNWEIGHT.get(x["race"], 1.0) * (x["our_chance"] - x["ask"])
        c.append(x)
    # one variable per offer level; the contrarian $200 takes the cheapest offers first
    race_of, price, size, fixed, ub = [], [], [], [], []
    for i, x in enumerate(c):
        left = CONTRARIAN_STAKE if x["contrarian"] else 0.0
        for p, n in x["book"]:
            unit = p + mo.fee(p)
            take = min(left, n * unit)
            left -= take
            model_ok = x["our_chance"] > 0.5 and x["blend"] - unit > 0
            if take == 0 and not model_ok:
                continue
            race_of.append(i); price.append(p); size.append(n); fixed.append(take)
            ub.append(n * unit - take if model_ok else 0.0)
            if left <= 0 and not model_ok:
                break
    race_of, price, size, fixed, ub = map(np.array, (race_of, price, size, fixed, ub))
    pay = 1 / (price + mo.fee(price)) - 1  # profit per dollar staked if the pick wins
    blend = np.array([x["blend"] for x in c])
    mu = blend[race_of] * (1 + pay) - 1
    wins = our_pick_wins(c, np.array([x["ask"] for x in c]), SCENARIOS, SEED)
    ret = np.where(wins[:, race_of], pay, -1.0)
    budget = bankroll - fixed.sum()
    while True:
        room = {i: RACE_CAP - fixed[race_of == i].sum() for i in range(len(c))}
        x = solve(mu, ret, fixed, ub, race_of, room, budget)
        if x is None:
            sys.exit("no stakes meet the rule at today's prices; nothing written")
        x = np.where(x > 0.5, x, 0)
        small = [i for i in range(len(c)) if 0 < x[race_of == i].sum() < MIN_STAKE]
        if small:
            ub[np.isin(race_of, small)] = 0
            continue
        orders = sheet(c, race_of, price, size, fixed + x, fixed, x)
        over = sum(o["cost"] for o in orders) - bankroll
        if over <= 0:
            return c, race_of, price, fixed + x, orders
        budget -= over + 0.5  # the limit-price cost bound ran over B; take it back out of the model money


def sheet(c, race_of, price, size, stake, fixed, model):
    orders = []
    for i in range(len(c)):
        lv = np.where(race_of == i)[0]
        n = [min(int(stake[j] // (price[j] + mo.fee(price[j]))), int(size[j])) for j in lv]
        total = sum(n)
        if total == 0:
            continue
        limit = max(price[j] for j, k in zip(lv, n) if k > 0)
        cost = mo.order_cost(total, limit)
        layers = "+".join(l for l, on in (("contrarian", fixed[lv].sum() > 0), ("model", model[lv].sum() > 0)) if on)
        x = c[i]
        orders.append(dict(race=x["race"], pick=x["pick"], ticker=x["ticker"], side="YES", limit=round(float(limit), 4),
                           contracts=total, cost=cost, stake=round(float(stake[lv].sum()), 2),
                           our_chance=round(x["our_chance"], 4), profit_if_right=round(total - cost, 2), layers=layers,
                           target_stake=round(float(stake[lv].sum()), 2), already_spent=0.0))
    return sorted(orders, key=lambda o: -o["cost"])


def summary(c, race_of, price, stake):
    pay = 1 / (price + mo.fee(price)) - 1
    lines = []
    for name, key in (("halfway", "blend"), ("Kalshi right", "ask"), ("model right", "our_chance")):
        wins = our_pick_wins(c, np.array([x[key] for x in c]), CHECK_SCENARIOS, CHECK_SEED)
        pnl = np.where(wins[:, race_of], pay, -1.0) @ stake
        lines.append(f"  {name:12s} mean {pnl.mean():+7,.0f}  median {np.median(pnl):+7,.0f}  1-in-20 {np.percentile(pnl, 5):+7,.0f}"
                     f"  P(profit) {np.mean(pnl > 0):4.0%}  P(lose >$3K) {np.mean(pnl < -3000):5.1%}")
    lines.append(f"  every pick wins: +${(stake * pay).sum():,.0f}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("forecast")
    ap.add_argument("--out", default="orders.csv")
    ap.add_argument("--bankroll", type=float, required=True)
    ap.add_argument("--events", help="saved Kalshi events JSON instead of a live pull (markets only; prices come from the books)")
    ap.add_argument("--books", help="saved order books JSON instead of a live pull")
    args = ap.parse_args()
    forecast = json.load(open(args.forecast))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.events:
        events = json.load(open(args.events))
    else:
        events = mo.fetch_events()
        json.dump(events, open(f"events-{stamp}.json", "w"))
    if args.books:
        books = json.load(open(args.books))
    else:
        tickers = sorted({x["ticker"] for x in picks(forecast, events)})
        books = fetch_books(tickers)
        json.dump(books, open(f"books-{stamp}.json", "w"))
        print(f"order books saved to books-{stamp}.json")
    c, race_of, price, stake, orders = build(forecast, events, books, args.bankroll)
    fields = ["race", "pick", "ticker", "side", "limit", "contracts", "cost", "stake", "our_chance", "profit_if_right",
              "layers", "target_stake", "already_spent", "filled_contracts", "fill_price"]
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for o in orders:
            w.writerow({k: o.get(k, "") for k in fields})
    print(f"prices {'from ' + args.books if args.books else 'pulled ' + stamp}")
    print(f"forecast {forecast['about']['version']} generated {forecast['about']['generated_at']}")
    print(f"bankroll ${args.bankroll:,.0f}; {len(orders)} orders, cost at the limit prices ${sum(o['cost'] for o in orders):,.2f} -> {args.out}")
    print(f"  contrarian: {sum('contrarian' in o['layers'] for o in orders)} races; model: {sum('model' in o['layers'] for o in orders)}")
    print(summary(c, race_of, price, stake))


if __name__ == "__main__":
    main()
