#!/usr/bin/env python3
"""Planetarium midterms 2026: place the Kalshi order sheet built by make_orders.py.

    python3 place_orders.py orders.csv --check      # live prices and balance, no orders
    python3 place_orders.py orders.csv --go         # place every order
    python3 place_orders.py orders.csv --reconcile  # fill in filled_contracts / fill_price

Auth: KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH (PEM, RSA or Ed25519).
Rules enforced here (BETTING-RULE.md): buy YES on our pick only; limit orders at the sheet
price, never higher; skip any market whose ask is now above our chance, except contrarian bets,
which the rule places whatever the price; total cost capped at
--max-spend and at the account balance; unfilled orders expire 11:59 PM ET Mon 2 Nov.
client_order_id is fixed per ticker, so a rerun can't place the same order twice.
"""
import argparse, base64, csv, json, os, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

HOST = "https://api.elections.kalshi.com"
PREFIX = "/trade-api/v2"
EXPIRES = int(datetime(2026, 11, 3, 4, 59, tzinfo=timezone.utc).timestamp())  # 11:59 PM EST, 2 Nov
TAG = "plnt26"


def load_key():
    from cryptography.hazmat.primitives import serialization
    with open(os.environ["KALSHI_PRIVATE_KEY_PATH"], "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def sign(key, ts, method, path):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    msg = (ts + method + path.split("?")[0]).encode()
    if isinstance(key, Ed25519PrivateKey):
        sig = key.sign(msg)
    else:
        sig = key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                       hashes.SHA256())
    return base64.b64encode(sig).decode()


def call(method, endpoint, body=None, key=None):
    path = PREFIX + endpoint
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key is not None:
        ts = str(int(time.time() * 1000))
        headers.update({"KALSHI-ACCESS-KEY": os.environ["KALSHI_API_KEY_ID"], "KALSHI-ACCESS-TIMESTAMP": ts,
                        "KALSHI-ACCESS-SIGNATURE": sign(key, ts, method, path)})
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(6):
        req = urllib.request.Request(HOST + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            if e.code == 429:
                time.sleep(1 + attempt)
                if key is not None:  # re-sign with a fresh timestamp
                    ts = str(int(time.time() * 1000))
                    headers.update({"KALSHI-ACCESS-TIMESTAMP": ts, "KALSHI-ACCESS-SIGNATURE": sign(key, ts, method, path)})
                continue
            try:
                return e.code, json.loads(text)
            except ValueError:
                return e.code, {"raw": text}
    return 429, {"error": "rate limited"}


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def money(d, *names):
    """Read a dollar amount from Kalshi's _dollars field, or its cents field."""
    for n in names:
        if d.get(n + "_dollars") is not None:
            return f(d[n + "_dollars"])
        if d.get(n) is not None:
            return f(d[n]) / 100
    return None


def count(d, *names):
    for n in names:
        for k in (n + "_fp", n):
            if d.get(k) is not None:
                return f(d[k])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--go", action="store_true")
    mode.add_argument("--reconcile", action="store_true")
    ap.add_argument("--max-spend", type=float, default=10000)
    ap.add_argument("--log", default="orders-placed.csv")
    ap.add_argument("--tag", default=TAG, help="client_order_id prefix; a top-up on a market already ordered needs a new one")
    args = ap.parse_args()
    rows = list(csv.DictReader(open(args.sheet)))
    key = load_key()

    if args.reconcile:
        log = list(csv.DictReader(open(args.log)))
        by = {r["ticker"]: r for r in rows}
        spent = 0.0
        for o in log:
            if not o.get("order_id"):
                continue
            st, d = call("GET", f"/portfolio/orders/{o['order_id']}", key=key)
            od = d.get("order", d)
            n = count(od, "fill_count") or 0
            cost = (money(od, "taker_fill_cost") or 0) + (money(od, "maker_fill_cost") or 0)
            fees = (money(od, "taker_fees") or 0) + (money(od, "maker_fees") or 0)
            o.update(status=od.get("status", st), filled_contracts=int(n), fill_price=round(cost / n, 4) if n else "",
                     fees=round(fees, 2))
            if o["ticker"] in by:
                by[o["ticker"]].update(filled_contracts=int(n), fill_price=o["fill_price"])
            spent += cost + fees
            time.sleep(0.15)
        for name, data in ((args.log, log), (args.sheet, rows)):
            fields = list(data[0].keys())
            for extra in ("status", "filled_contracts", "fill_price", "fees"):
                if extra not in fields and any(extra in r for r in data):
                    fields.append(extra)
            with open(name, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=fields)
                w.writeheader()
                w.writerows(data)
        filled = sum(1 for o in log if f(o.get("filled_contracts")))
        print(f"{filled}/{len(log)} orders have fills; spent ${spent:,.2f} incl. fees; sheet and log updated")
        return

    st, bal = call("GET", "/portfolio/balance", key=key)
    if st != 200:
        sys.exit(f"balance call failed ({st}): {bal}")
    balance = money(bal, "balance")
    total = sum(f(r["cost"]) for r in rows)
    print(f"balance ${balance:,.2f}; sheet {len(rows)} orders, cost ${total:,.2f}; cap ${args.max_spend:,.0f}")
    if total > min(balance, args.max_spend):
        sys.exit("sheet costs more than the balance or the cap; rebuild it with a smaller --bankroll")

    plan, skipped = [], []
    for r in rows:
        st, d = call("GET", f"/markets/{r['ticker']}")
        m = d.get("market", {})
        ask, ours, limit = f(m.get("yes_ask_dollars")), f(r["our_chance"]), f(r["limit"])
        if st != 200 or m.get("status") not in ("active", "open"):
            skipped.append((r, f"market not open ({st}, {m.get('status')})"))
        elif ask is not None and ask > ours and "contrarian" not in r.get("layers", ""):
            skipped.append((r, f"ask {ask:.3f} above our chance {ours:.3f}"))
        else:
            plan.append((r, ask))
        time.sleep(0.1)
    moved = [(r, a) for r, a in plan if a is not None and a > f(r["limit"])]
    print(f"to place: {len(plan)} (of which {len(moved)} now ask above the sheet limit and will rest at the limit)")
    for r, why in skipped:
        print(f"  skip {r['race']} {r['pick']}: {why}")
    if args.check:
        return

    done = set()
    if os.path.exists(args.log):  # a rerun: keep the earlier log and never resend an order that went through
        done = {o["ticker"] for o in csv.DictReader(open(args.log)) if o.get("order_id")}
    plan = [(r, a) for r, a in plan if r["ticker"] not in done]
    new_log = not os.path.exists(args.log)
    with open(args.log, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["race", "pick", "ticker", "limit", "contracts", "client_order_id", "http",
                                           "order_id", "status", "note"])
        if new_log:
            w.writeheader()
        for r, why in skipped:
            w.writerow(dict(race=r["race"], pick=r["pick"], ticker=r["ticker"], limit=r["limit"],
                            contracts=r["contracts"], note="skipped: " + why))
        placed_cost = 0.0
        for i, (r, _) in enumerate(plan):
            if placed_cost + f(r["cost"]) > args.max_spend:
                w.writerow(dict(race=r["race"], ticker=r["ticker"], note="skipped: spend cap"))
                continue
            cid = f"{args.tag}-{r['ticker']}"
            # Create Order (V2): side "bid" buys YES; the legacy /portfolio/orders shape is deprecated
            body = {"ticker": r["ticker"], "side": "bid", "count": str(int(r["contracts"])),
                    "price": f"{f(r['limit']):.4f}", "time_in_force": "good_till_canceled", "expiration_time": EXPIRES,
                    "self_trade_prevention_type": "taker_at_cross", "client_order_id": cid}
            st, d = call("POST", "/portfolio/events/orders", body, key=key)
            od = d if st in (200, 201) else {}
            status = ("executed" if f(od.get("remaining_count")) == 0 else "resting") if od.get("order_id") else ""
            w.writerow(dict(race=r["race"], pick=r["pick"], ticker=r["ticker"], limit=r["limit"], contracts=r["contracts"],
                            client_order_id=cid, http=st, order_id=od.get("order_id", ""), status=status,
                            note="" if st in (200, 201) else json.dumps(d)[:300]))
            fh.flush()
            print(f"[{i + 1}/{len(plan)}] {r['race']} {r['pick']}: {st} {status or d} filled {od.get('fill_count', 0)}")
            if st in (200, 201):
                placed_cost += f(r["cost"])
            elif i == 0:
                sys.exit("first order rejected; nothing else sent. Fix the request against the API docs and rerun.")
            time.sleep(0.25)
    print(f"done; log in {args.log}. Run --reconcile to record fills.")


if __name__ == "__main__":
    main()
