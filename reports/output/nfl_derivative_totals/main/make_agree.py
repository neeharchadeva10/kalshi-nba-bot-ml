"""Writes ../main_out/agree_snapshots.csv: 100 random (game, tau, family) snapshots (seed 20261028) from the usable set, with the fair
centre, the derivative median, the gap and the side at g = 1, 2, 3 (or 'none'), for an independent checker.  PRICE SIDE ONLY (no outcomes).
Needs the Kalshi download; refuses before it exists.  Run after the download:
  .venv/Scripts/python.exe reports/output/nfl_derivative_totals/main/make_agree.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ldr_lib as L  # noqa: E402

if __name__ == "__main__":
    if not (L.KALSHI_DIR / "markets.parquet").exists() or not (L.KALSHI_DIR / "candles_1m.parquet").exists():
        raise SystemExit("REFUSED: the Kalshi download (markets.parquet / candles_*.parquet) is not there yet.")
    games = L.N.load_games()
    tabs, _ = L.price_tables(games, L.load_r1())
    out = L.agree_sample(tabs["dec"])
    L.MAIN_OUT.mkdir(exist_ok=True)
    out.to_csv(L.MAIN_OUT / "agree_snapshots.csv", index=False)
    print(f"wrote agree_snapshots.csv: {out[['game_id','tau','family']].drop_duplicates().shape[0]} snapshots, {len(out)} ladder rows")
