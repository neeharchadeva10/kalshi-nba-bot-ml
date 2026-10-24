"""Step 1-2: map Kalshi players to hoopR athletes, compute pre-game last-10 means, select the 2 nearest strikes per
player-stat-game, write selection.csv (queue-ordered) and selection_report.json."""
import json
import tm_lib as L

box = L.Box()
rows, rep = L.build_selection(box)
rows = L.write_selection(rows, box.reg_end)
rep["reg_end"] = box.reg_end
rep["by_group"] = {g: sum(1 for r in rows if r["group"] == g) for g in (0, 1, 2)}
rep["match_rate_players"] = rep["players_matched"] / rep["n_players"]
(L.HERE / "selection_report.json").write_text(json.dumps(rep, indent=1))
print(json.dumps(rep, indent=1))
