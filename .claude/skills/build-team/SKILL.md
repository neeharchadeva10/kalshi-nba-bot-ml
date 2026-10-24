---
name: build-team
description: Step 7 — combine strategies that passed every test (honest, unseen, coin flip) into one portfolio and show the combined results, worst losing stretch, and how much smoother it is than any single strategy. Use once two or more strategies survive.
argument-hint: "<log #s of surviving strategies>"
---

# Step 7 — Build a team

His prompt: "Take the strategies that passed every test and combine them into one portfolio. Show me the combined
results, the worst losing stretch, and how much smoother it is than any single strategy." (He calls his five
strategies "the syndicate": ace, king, queen, jack and 10.)

Strategies: $ARGUMENTS

1. **Only strategies that passed steps 3, 4 and 5** in `docs/RESEARCH_LOG.md`. If fewer than 2 qualify, say so and
   stop. As of 2026-10-19, none have.
2. Build each strategy's daily net P&L series at the real size on the unseen period, from the honest-test engine.
3. **Report:**
   - each strategy alone vs the combined team: total net, dollars per week, win days, worst losing stretch (in
     dollars and days), and the worst single day;
   - the correlation of daily P&L between strategies (low is good);
   - capital needed at once, since Kalshi ties up the price paid per contract.
4. **Respect the account:** the live account is about $29 with a $5 account-wide loss cap and $1 max per trade. Show
   whether the team fits. If it doesn't, scale it down in `/right-size`.
5. Combining doesn't create an edge. It only smooths it. A team of strategies that each lose still loses.
6. Log the team as its own entry (`/log-idea`), listing its members. Next: `/paper-trade` on the whole team.
