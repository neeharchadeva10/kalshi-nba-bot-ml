# picks: Claude recommends, you approve
1. Claude writes a ticket: tickets/<id>.json (ticker, side, count, max_price, reason, created_utc).
2. Preview (read-only, sends nothing): `KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/picks/approve.py <id>`
3. To place it, type `/send <id>` in Claude Code (user-only skill: .claude/skills/send), or run the same command
   with `--send` yourself (prefix with `!`). Only you do this step.
4. One IOC buy per ticket (fills now or not at all). A ticket id can be used once.
5. Hard limits live in approve.py: series KXNBAGAME/KXNBASPREAD/KXNFLGAME/KXNFLSPREAD, count 1..10,
   $3.00 max cost per ticket and $3.00 total (your 2026-10-20 max loss), ticket expires after 30 min.
6. Preflight aborts if the live ask is above max_price, size is short, or you already hold/rest on the ticker.
7. Every step is logged to logs/picks_orders.jsonl (no keys).
8. Tests: `.venv/Scripts/python.exe -m pytest reports/output/picks/test_approve.py`
