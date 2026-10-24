# Key NBA trades and moves: the last 3 seasons plus the 2026 offseason

Built 2026-10-23.
- **Source:** data/derived/player_moves.csv, from the free hoopR game rosters. It is refreshed daily (task "KalshiPM
  NBA rosters refresh", 9 AM ET).
- **Cross-checked:** big moves against news reports (links at the bottom).
- **"Date"** = the player's first game for the new team. The trade itself happened between his last old-team game and
  that date.
- **"mpg"** = his minutes per game over his last 20 games before the move. It shows how big a role he had.
- Full lists: docs/PLAYER_MOVES.md.

| Season | Offseason moves | In-season moves | In-season moves of 25+ mpg players |
|---|---|---|---|
| 2023-24 | 165 | 91 | 15 |
| 2024-25 | 135 | 94 | 17 |
| 2025-26 | 156 | 82 | 12 |
| 2026-27 (so far) | 83 (+34 not yet played) | 0 | 0 |

## 2023-24

**Offseason**
- Damian Lillard POR→MIL
- Jrue Holiday MIL→BOS
- Kristaps Porzingis WAS→BOS
- Bradley Beal WAS→PHX
- Chris Paul PHX→GSW
- Fred VanVleet TOR→HOU
- Marcus Smart BOS→MEM
- Deandre Ayton PHX→POR
- James Harden PHI→LAC

**In-season**

| Player | Move | First game |
|---|---|---|
| OG Anunoby | TOR→NYK | Jan 1 |
| RJ Barrett | NYK→TOR | Jan 1 |
| Pascal Siakam | TOR→IND | Jan 19 |
| Bruce Brown | IND→TOR | Jan 18 |
| Terry Rozier | CHA→MIA | Jan 24 |
| Bojan Bogdanovic | DET→NYK | Feb 10 |
| P.J. Washington | CHA→DAL | Feb 10 |
| Daniel Gafford | WAS→DAL | Feb 10 |
| Gordon Hayward | CHA→OKC | Feb 22 |
| Kyle Lowry | MIA→PHI (after a buyout) | Feb 22 |

## 2024-25

**Offseason**
- Mikal Bridges BKN→NYK
- Julius Randle and Donte DiVincenzo NYK→MIN (the Towns trade)
- Paul George LAC→PHI
- DeMar DeRozan CHI→SAC
- Dejounte Murray ATL→NOP
- Alex Caruso CHI→OKC
- Kentavious Caldwell-Pope DEN→ORL

**In-season (the Feb 2025 deadline)**

| Player | Move | First game |
|---|---|---|
| Luka Dončić | DAL→LAL | Feb 10 |
| Anthony Davis | LAL→DAL | Feb 8 |
| De'Aaron Fox | SAC→SAS | Feb 5 |
| Zach LaVine | CHI→SAC | Feb 5 |
| Jimmy Butler | MIA→GSW | Feb 8 |
| Andrew Wiggins | GSW→MIA | Feb 10 |
| Brandon Ingram | NOP→TOR | Feb 26 |
| De'Andre Hunter | ATL→CLE | Feb 10 |
| Kyle Kuzma | WAS→MIL | Feb 7 |

## 2025-26

**Offseason**
- Kevin Durant PHX→HOU
- Desmond Bane MEM→ORL
- Michael Porter Jr. DEN→BKN
- Jrue Holiday BOS→POR
- Anfernee Simons POR→BOS
- Norman Powell LAC→MIA
- John Collins UTA→LAC
- Bradley Beal PHX→LAC
- Damian Lillard MIL→POR
- CJ McCollum NOP→WAS

**In-season**

| Player | Move | When |
|---|---|---|
| Trae Young | ATL→WAS | traded Jan; first Wizards game Mar 5 (was injured) |
| CJ McCollum | WAS→ATL | Jan 11 |
| James Harden | LAC→CLE (traded Feb 3) | Feb 7 |
| Darius Garland | CLE→LAC | Mar 2 |
| Jaren Jackson Jr. | MEM→UTA | Feb 7 |
| Nikola Vučević | CHI→BOS | Feb 4 |
| Coby White | CHI→CHA | Feb 5 |
| Anfernee Simons | BOS→CHI | Feb 5 |
| Ivica Zubac | LAC→IND | Mar 12 |
| Bennedict Mathurin | IND→LAC | Feb 10 |
| Jonathan Kuminga | GSW→ATL | Feb 24 |

## 2026 offseason (this season's rosters)

| Move | Detail |
|---|---|
| Jaylen Brown BOS→PHI | For Paul George + 4 picks (July 2026) |
| Paul George PHI→BOS | Same deal |
| LeBron James LAL→PHI | Signed July 24, 2026 (2 yr) |
| Giannis Antetokounmpo MIL→MIA | With Bobby Portis; Tyler Herro, Kel'el Ware and Jaime Jaquez Jr. → MIL |
| Kawhi Leonard LAC→TOR | For Brandon Ingram, Gradey Dick + picks. Agreed June 30; delayed by the NBA cap investigation, completed Sept 2026. Ingram hasn't played for LAC yet |
| Anthony Davis DAL→WAS | 3-team deal with Charlotte (June 2026); Khris Middleton → DAL |
| LaMelo Ball CHA→MIN | Naz Reid + picks → CHA |

**Also:**
- Julius Randle MIN→BKN
- Rui Hachimura LAL→LAC
- Marcus Smart LAL→HOU
- Kelly Oubre Jr. PHI→IND

**Data quirks to ignore:** a few short-stint players show inflated "mpg" from only a handful of games (e.g. Zavier
Simpson 2023-24).

## News sources used for the cross-check

- [6abc: LeBron signs with the 76ers](https://6abc.com/post/lebron-signs-philadelphia-76ers-stuns-sports-world/19569161/)
- [NBA.com: Sixers to acquire Jaylen Brown](https://www.nba.com/news/reports-sixers-to-acquire-jaylen-brown-from-celtics)
- [theScore: Celtics trade Brown for George and picks](https://m.thescore.com/nba/news/3561427)
- [ABC7: Raptors agree to acquire Kawhi Leonard](https://abc7.com/post/sources-clippers-nearing-deal-send-leonard-raptors/19423186/)
- [Las Vegas Sun / AP: Leonard going back to Toronto](https://lasvegassun.com/news/2026/jun/30/kawhi-leonard-going-back-to-toronto-after-raptors-/)
- [CTV: Leonard trade to be completed after delay](https://www.ctvnews.ca/toronto/sports/article/kawhi-leonard-trade-to-toronto-raptors-to-be-completed-after-long-delay-report-2/)
- [Fox News: Mavericks send Anthony Davis to the Wizards](https://www.foxnews.com/sports/mavericks-send-anthony-davis-wizards-eight-player-trade-report)
- [NBA.com: Wizards–Mavericks Anthony Davis trade](https://api-hub.nba.com/news/wizards-mavericks-anthony-davis-trade)
- [NBC Sports: Heat trade Tyler Herro to Milwaukee](https://www.nbcsports.com/fantasy/basketball/player-news/2026-06-23/report-heat-trade-tyler-herro-to-milwaukee)
- [Daily Mirror: Miami turns on the Heat](https://www.dailymirror.lk/sports/Miami-turns-on-the-Heat/244-344235)
- [Fox News: Clippers trade Harden to the Cavaliers](https://www.foxnews.com/sports/clippers-trade-james-harden-cavaliers-report.print)
- [NBA.com: 2025-26 trade tracker](https://www.nba.com/news/2025-26-nba-trade-tracker)
