# Planetarium midterms 2026: betting rule

Committed to the lock repo before any order is placed. The forecast it reads is the sealed `Planetarium-Midterms-2026-Forecast.json` (v2.1, sha256 `ab169ff8480e7db5feee83ce8f080baedc5737495be36388a1c0c472410a7c5a`, listed in `v2.1/SHA256SUMS`). The rule is fixed. Only Kalshi's order book changes between runs. `make_sheet.py` turns the rule and the order book into the order sheet, and `place_orders.py` places it.

## The rule

B is the amount committed: $10,000. Every bet buys YES on Planetarium's most likely winner in the race (our pick). We never bet against our pick, never sell, and never spend more than B.

1. **Where we go against the polls or the market: $200 each, whatever the price.** This covers our pick in every race Kalshi lists that is on the sealed forecast's list of places it disagrees with the polls (`where_we_disagree_with_the_polls`). It also covers every race where Kalshi makes the other side the favourite: our chance is above 50% and Kalshi's midpoint is below 50¢. These buy the cheapest offers first. Some are priced at or above our own chance. We bet them anyway, so the list can't be cherry-picked.
2. **The rest goes where our chance beats Kalshi's price.** The rest of B can go on our pick in any race Kalshi prices at 90¢ or less, in our ten surest calls (NY Gov, MA Gov, IL Gov, MN-05, TN Gov, AL Gov, AR Sen, SD Sen, ID Sen, TN Sen), and on Democratic control of the House. An offer in Kalshi's order book qualifies only where a 50/50 blend of our chance and Kalshi's best price beats that offer's price after fees. Stakes are chosen to maximise expected return on that blend, with these limits:
   - At most $1,000 on any race.
   - If Kalshi's prices are right, the average loss in the worst 5% of outcomes is at most $2,600.
   - Races move together. Outcomes share one national swing, with correlation 0.3 between races and 0.8 for House control.
   - Races that rest on polls paid for by a party or campaign (OH-07, KY-06, FL-22) count 0.4 of our edge in the blend. Alaska Senate, which rests on ranked-choice transfer estimates, counts 0.5.
   - Stakes under $50 are dropped and the rest are solved again.

## Placement

- Every stake is sized to the offers in Kalshi's order book when the sheet is built, and the sheet is built just before the orders go in, so the whole amount goes in at once.
- Each race gets one limit order at the highest offer price it uses. The sheet counts every contract at that price, plus fees, so the total can't exceed B.
- Limit orders only, at the sheet price or lower. Anything that doesn't fill at once rests at that price until 11:59 PM ET on 2 Nov, then expires. We don't chase the price.
- If a market's price has risen past our chance, we skip it, except for the $200 bets in step 1.
- If an order is rejected or doesn't fill, its money stays unspent. We don't move it to another race.
- Personal funds. Every order and fill is logged and published with the score.

## Disclosure (for the site and the lock commit)

> Zac Stern, founder of Social Intelligence Labs, is putting $10,000 of personal funds on Planetarium's forecast on Kalshi. He follows the rule above, which was committed before any order. Every bet backs the forecast's favourite, including $200 on every race where the forecast goes against the polls or the market. Positions and fills are published with the results. Planetarium's forecast was sealed before any bet and does not change.
