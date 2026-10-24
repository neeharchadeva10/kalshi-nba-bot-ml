# NBA player moves, 2007-08 to 2026-27

Generated 2026-10-23 13:00 UTC by `scripts/nba_player_moves.py`. Source: hoopR game_rosters (per-game rosters, active + inactive), dates from team_box, minutes from player_box. Data file: `data/derived/player_moves.csv`.
As of: GitHub last commit 2026-10-22T11:34:13Z; downloaded 2026-10-23T03:58:29Z; latest game date in data 2026-10-21

## What this is and is not

- A move = a player's team (franchise) differs between two consecutive game-roster appearances. Franchise codes are Kalshi style (Nets in New Jersey and Brooklyn are both BKN, SEA = OKC, NO/NOH/NOK = NOP).
- **The data cannot say trade vs free-agent signing vs waiver vs 10-day contract vs buyout.** Every move is labelled `moved` except `likely trade`: two different players swapping between the same two teams with first appearances on the new teams within 3 days. One-sided trades (player for picks), 3-team trades and trades where one side is not seen are labelled `moved`.
- Dates are bounds. `last_date_old` is the last game sheet on the old team, `first_date_new` the first on the new team; the real transaction is between them. A player who is hurt after a trade can show up on the new team's sheet days or weeks late, and players never listed on a game sheet (long-term injured, two-way, G League) do not show at all.
- For `offseason` moves `first_date_new` is the new team's first game of the next season, so many offseason moves share the same date. The 3-day swap rule therefore over-labels offseason moves as `likely trade` (opening-night coincidences); trust the label for in-season moves only.
- `prior_mpg20` = mean minutes over the last 20 games the player actually played (minutes > 0) strictly before `first_date_new`, any team, regular season + playoffs. It flags key players; it is not usage on the new team.
- Seasons: hoopR season YYYY = the season ending in YYYY (2026 = 2025-26, 2027 = 2026-27). Regular season, play-in and playoffs are included; All-Star and non-NBA teams are dropped.

## Source per season

2008: game_rosters, 2009: game_rosters, 2010: game_rosters, 2011: game_rosters, 2012: game_rosters, 2013: game_rosters, 2014: game_rosters, 2015: game_rosters, 2016: game_rosters, 2017: game_rosters, 2018: game_rosters, 2019: game_rosters, 2020: game_rosters, 2021: game_rosters, 2022: game_rosters, 2023: game_rosters, 2024: game_rosters, 2025: game_rosters, 2026: game_rosters, 2027: game_rosters

## Move counts per season (season of the NEW team)

| season | offseason | in-season | in-season 'likely trade' rows |
|---|---|---|---|
| 2008 (2007-08) | 0 | 87 | 51 |
| 2009 (2008-09) | 100 | 88 | 51 |
| 2010 (2009-10) | 120 | 83 | 51 |
| 2011 (2010-11) | 144 | 99 | 60 |
| 2012 (2011-12) | 124 | 48 | 21 |
| 2013 (2012-13) | 157 | 57 | 19 |
| 2014 (2013-14) | 158 | 70 | 23 |
| 2015 (2014-15) | 137 | 94 | 42 |
| 2016 (2015-16) | 131 | 60 | 17 |
| 2017 (2016-17) | 144 | 60 | 29 |
| 2018 (2017-18) | 153 | 67 | 23 |
| 2019 (2018-19) | 135 | 95 | 43 |
| 2020 (2019-20) | 167 | 64 | 27 |
| 2021 (2020-21) | 145 | 89 | 45 |
| 2022 (2021-22) | 190 | 123 | 39 |
| 2023 (2022-23) | 136 | 72 | 26 |
| 2024 (2023-24) | 165 | 91 | 39 |
| 2025 (2024-25) | 135 | 94 | 31 |
| 2026 (2025-26) | 156 | 82 | 28 |
| 2027 (2026-27) | 83 | 0 | 0 |

(The first season, 2007-08, has no earlier appearances, so no moves are counted into it; its offseason column is 0 by construction. 'likely trade' rows count players, so a clean 1-for-1 swap is 2 rows.)

## Top 15 in-season moves by prior mpg: 2026 (2025-26)

82 in-season moves in this season.

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| James Harden | LAC | CLE | 2026-01-30 | 2026-02-07 | 35.4 | moved |  |
| Trae Young | ATL | WAS | 2026-01-02 | 2026-03-05 | 32.5 | moved |  |
| Jaren Jackson Jr. | MEM | UTA | 2026-02-02 | 2026-02-07 | 32.2 | likely trade | Kyle Anderson |
| CJ McCollum | WAS | ATL | 2026-01-06 | 2026-01-11 | 32.1 | moved |  |
| Nikola Vucevic | CHI | BOS | 2026-02-01 | 2026-02-04 | 31.9 | likely trade | Anfernee Simons |
| Darius Garland | CLE | LAC | 2026-01-14 | 2026-03-02 | 31.4 | moved |  |
| Bennedict Mathurin | IND | LAC | 2026-02-03 | 2026-02-10 | 31.3 | likely trade | Kobe Brown |
| Coby White | CHI | CHA | 2026-02-03 | 2026-02-05 | 29.1 | likely trade | Collin Sexton |
| Ivica Zubac | LAC | IND | 2026-02-02 | 2026-03-12 | 29.1 | moved |  |
| Ayo Dosunmu | CHI | MIN | 2026-02-03 | 2026-02-08 | 27.1 | likely trade | Rob Dillingham |
| Jock Landale | MEM | ATL | 2026-02-02 | 2026-02-05 | 26.0 | moved |  |
| Anfernee Simons | BOS | CHI | 2026-02-03 | 2026-02-05 | 25.6 | likely trade | Nikola Vucevic |
| Dennis Schroder | SAC | CLE | 2026-01-30 | 2026-02-04 | 24.1 | likely trade | De'Andre Hunter |
| Jonathan Kuminga | GSW | ATL | 2026-01-28 | 2026-02-24 | 23.9 | moved |  |
| Khris Middleton | WAS | DAL | 2026-02-03 | 2026-02-10 | 23.6 | likely trade | Jaden Hardy |

## Top 15 in-season moves by prior mpg: 2027 (2026-27) so far

0 in-season moves in this season.

_(none)_

## Top 15 in-season moves by prior mpg: all seasons

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Allen Iverson (2009) | DEN | DET | 2008-11-01 | 2008-11-03 | 41.2 | likely trade | Chauncey Billups |
| Caron Butler (2010) | WAS | DAL | 2010-02-09 | 2010-02-16 | 40.5 | likely trade | Drew Gooden |
| Antawn Jamison (2010) | WAS | CLE | 2010-02-17 | 2010-02-19 | 40.2 | likely trade | Zydrunas Ilgauskas |
| Gerald Wallace (2011) | CHA | POR | 2011-02-22 | 2011-02-25 | 39.0 | likely trade | Sean Marks |
| Pau Gasol (2008) | MEM | LAL | 2008-01-30 | 2008-02-03 | 38.8 | likely trade | Kwame Brown |
| James Harden (2022) | BKN | PHI | 2022-02-08 | 2022-02-25 | 38.1 | moved |  |
| John Salmons (2009) | SAC | CHI | 2009-02-11 | 2009-02-20 | 37.9 | likely trade | Drew Gooden |
| Deron Williams (2011) | UTA | BKN | 2011-02-16 | 2011-02-25 | 37.9 | likely trade | Devin Harris |
| Luol Deng (2014) | CHI | CLE | 2014-01-04 | 2014-01-10 | 37.8 | moved |  |
| Norman Powell (2022) | POR | LAC | 2022-02-02 | 2022-02-06 | 37.6 | likely trade | Eric Bledsoe |
| Raymond Felton (2011) | NYK | DEN | 2011-02-16 | 2011-02-24 | 37.6 | likely trade | Chauncey Billups |
| Shawn Marion (2008) | PHX | MIA | 2007-12-17 | 2007-12-19 | 37.5 | likely trade | Marcus Banks |
| Shawn Marion (2008) | MIA | PHX | 2007-12-19 | 2007-12-19 | 37.5 | likely trade | Marcus Banks |
| Gerald Wallace (2012) | POR | BKN | 2012-03-14 | 2012-03-16 | 37.4 | likely trade | Mehmet Okur |
| Spencer Dinwiddie (2023) | DAL | BKN | 2023-02-04 | 2023-02-09 | 37.2 | likely trade | Kyrie Irving |

## Top 5 in-season moves per earlier season

### 2008 (2007-08)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Pau Gasol | MEM | LAL | 2008-01-30 | 2008-02-03 | 38.8 | likely trade | Kwame Brown |
| Shawn Marion | PHX | MIA | 2007-12-17 | 2007-12-19 | 37.5 | likely trade | Marcus Banks |
| Shawn Marion | MIA | PHX | 2007-12-19 | 2007-12-19 | 37.5 | likely trade | Marcus Banks |
| Jason Kidd | BKN | DAL | 2008-02-13 | 2008-02-20 | 36.6 | likely trade | DeSagana Diop |
| Shawn Marion | PHX | MIA | 2008-02-04 | 2008-02-10 | 35.1 | moved |  |

### 2009 (2008-09)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Allen Iverson | DEN | DET | 2008-11-01 | 2008-11-03 | 41.2 | likely trade | Chauncey Billups |
| John Salmons | SAC | CHI | 2009-02-11 | 2009-02-20 | 37.9 | likely trade | Drew Gooden |
| Jason Richardson | CHA | PHX | 2008-12-08 | 2008-12-12 | 37.1 | likely trade | Raja Bell |
| Shawn Marion | MIA | TOR | 2009-02-12 | 2009-02-18 | 34.9 | likely trade | Jermaine O'Neal |
| Jamal Crawford | NYK | GSW | 2008-11-18 | 2008-11-21 | 34.5 | likely trade | Al Harrington |

### 2010 (2009-10)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Caron Butler | WAS | DAL | 2010-02-09 | 2010-02-16 | 40.5 | likely trade | Drew Gooden |
| Antawn Jamison | WAS | CLE | 2010-02-17 | 2010-02-19 | 40.2 | likely trade | Zydrunas Ilgauskas |
| Raja Bell | CHA | GSW | 2009-11-14 | 2009-11-17 | 35.6 | likely trade | Stephen Jackson |
| Stephen Jackson | GSW | CHA | 2009-11-14 | 2009-11-16 | 35.2 | likely trade | Raja Bell |
| Kevin Martin | SAC | HOU | 2010-02-17 | 2010-02-20 | 34.5 | likely trade | Carl Landry |

### 2011 (2010-11)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Gerald Wallace | CHA | POR | 2011-02-22 | 2011-02-25 | 39.0 | likely trade | Sean Marks |
| Deron Williams | UTA | BKN | 2011-02-16 | 2011-02-25 | 37.9 | likely trade | Devin Harris |
| Raymond Felton | NYK | DEN | 2011-02-16 | 2011-02-24 | 37.6 | likely trade | Chauncey Billups |
| Jeff Green | OKC | BOS | 2011-02-23 | 2011-02-26 | 35.8 | likely trade | Kendrick Perkins |
| Carmelo Anthony | DEN | NYK | 2011-02-16 | 2011-02-23 | 35.8 | likely trade | Raymond Felton |

### 2012 (2011-12)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Gerald Wallace | POR | BKN | 2012-03-14 | 2012-03-16 | 37.4 | likely trade | Mehmet Okur |
| Monta Ellis | GSW | MIL | 2012-03-13 | 2012-03-14 | 35.8 | likely trade | Stephen Jackson |
| Andrew Bogut | MIL | GSW | 2012-03-12 | 2012-03-14 | 32.5 | likely trade | Kwame Brown |
| Nene  | DEN | WAS | 2012-03-15 | 2012-03-21 | 30.3 | moved |  |
| Nick Young | WAS | LAC | 2012-03-15 | 2012-03-15 | 30.1 | likely trade | Brian Cook |

### 2013 (2012-13)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Rudy Gay | MEM | TOR | 2013-01-28 | 2013-02-01 | 36.5 | likely trade | Ed Davis |
| Tayshaun Prince | DET | MEM | 2013-01-27 | 2013-02-01 | 32.2 | moved |  |
| Ed Davis | TOR | MEM | 2013-01-28 | 2013-02-01 | 31.9 | likely trade | Rudy Gay |
| JJ Redick | ORL | MIL | 2013-02-20 | 2013-02-23 | 31.9 | likely trade | Beno Udrih |
| Jose Calderon | TOR | DET | 2013-01-28 | 2013-02-04 | 28.4 | moved |  |

### 2014 (2013-14)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Luol Deng | CHI | CLE | 2014-01-04 | 2014-01-10 | 37.8 | moved |  |
| Rudy Gay | TOR | SAC | 2013-12-08 | 2013-12-09 | 34.5 | moved |  |
| Steve Blake | LAL | GSW | 2014-02-13 | 2014-02-20 | 33.7 | likely trade | MarShon Brooks |
| Evan Turner | PHI | IND | 2014-02-18 | 2014-02-25 | 32.9 | moved |  |
| Jordan Crawford | BOS | GSW | 2014-01-13 | 2014-01-17 | 32.8 | moved |  |

### 2015 (2014-15)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Thaddeus Young | MIN | BKN | 2015-02-11 | 2015-02-20 | 36.2 | moved |  |
| Goran Dragic | PHX | MIA | 2015-02-10 | 2015-02-21 | 34.0 | likely trade | Danny Granger |
| Arron Afflalo | DEN | POR | 2015-02-10 | 2015-02-22 | 33.4 | likely trade | Will Barton |
| Michael Carter-Williams | PHI | MIL | 2015-02-09 | 2015-02-20 | 33.4 | moved |  |
| Brandon Knight | MIL | PHX | 2015-02-11 | 2015-02-20 | 33.0 | likely trade | Miles Plumlee |

### 2016 (2015-16)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Tobias Harris | ORL | DET | 2016-02-05 | 2016-02-19 | 34.2 | likely trade | Ersan Ilyasova |
| Joe Johnson | BKN | MIA | 2016-02-23 | 2016-02-28 | 32.5 | moved |  |
| Jeff Green | MEM | LAC | 2016-02-10 | 2016-02-20 | 30.2 | likely trade | Lance Stephenson |
| Courtney Lee | MEM | CHA | 2016-02-10 | 2016-02-19 | 29.3 | likely trade | P.J. Hairston |
| Ersan Ilyasova | DET | ORL | 2016-02-10 | 2016-02-19 | 27.9 | likely trade | Tobias Harris |

### 2017 (2016-17)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| DeMarcus Cousins | SAC | NOP | 2017-02-15 | 2017-02-23 | 32.3 | likely trade | Tyreke Evans |
| Serge Ibaka | ORL | TOR | 2017-02-13 | 2017-02-24 | 30.8 | moved |  |
| P.J. Tucker | PHX | TOR | 2017-02-15 | 2017-02-24 | 30.8 | likely trade | Jared Sullinger |
| Mason Plumlee | POR | DEN | 2017-02-09 | 2017-02-15 | 29.3 | likely trade | Jusuf Nurkic |
| Jerami Grant | PHI | OKC | 2016-10-29 | 2016-11-02 | 28.3 | likely trade | Ersan Ilyasova |

### 2018 (2017-18)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Blake Griffin | LAC | DET | 2018-01-28 | 2018-02-01 | 34.0 | likely trade | Avery Bradley |
| Avery Bradley | DET | LAC | 2018-01-28 | 2018-02-03 | 31.7 | likely trade | Blake Griffin |
| Tobias Harris | DET | LAC | 2018-01-28 | 2018-02-03 | 31.6 | likely trade | Blake Griffin |
| Eric Bledsoe | PHX | MIL | 2017-10-21 | 2017-11-10 | 31.2 | moved |  |
| Elfrid Payton | ORL | PHX | 2018-02-06 | 2018-02-10 | 28.9 | moved |  |

### 2019 (2018-19)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Justin Holiday | CHI | MEM | 2019-01-02 | 2019-01-05 | 34.9 | likely trade | Wayne Selden |
| Tobias Harris | LAC | PHI | 2019-02-05 | 2019-02-08 | 34.9 | likely trade | Landry Shamet |
| Trevor Ariza | PHX | WAS | 2018-12-13 | 2018-12-18 | 34.3 | likely trade | Kelly Oubre Jr. |
| Jimmy Butler III | MIN | PHI | 2018-11-09 | 2018-11-14 | 34.2 | likely trade | Robert Covington |
| Harrison Barnes | DAL | SAC | 2019-02-06 | 2019-02-08 | 32.6 | likely trade | Justin Jackson |

### 2020 (2019-20)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Andrew Wiggins | MIN | GSW | 2020-02-05 | 2020-02-08 | 34.0 | likely trade | D'Angelo Russell |
| Andre Drummond | DET | CLE | 2020-02-05 | 2020-02-09 | 33.5 | likely trade | Brandon Knight |
| D'Angelo Russell | GSW | MIN | 2020-02-05 | 2020-02-10 | 32.9 | likely trade | Andrew Wiggins |
| Robert Covington | MIN | HOU | 2020-02-03 | 2020-02-06 | 32.0 | moved |  |
| Marcus Morris Sr. | NYK | LAC | 2020-02-03 | 2020-02-09 | 31.9 | likely trade | Maurice Harkless |

### 2021 (2020-21)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| James Harden | HOU | BKN | 2021-01-12 | 2021-01-16 | 36.8 | likely trade | Rodions Kurucs |
| Nikola Vucevic | ORL | CHI | 2021-03-24 | 2021-03-27 | 33.9 | likely trade | Otto Porter Jr. |
| Norman Powell | TOR | POR | 2021-03-24 | 2021-03-26 | 33.9 | likely trade | Rodney Hood |
| Victor Oladipo | HOU | MIA | 2021-03-21 | 2021-03-26 | 33.5 | likely trade | Avery Bradley |
| Gary Trent Jr. | POR | TOR | 2021-03-23 | 2021-03-26 | 33.0 | likely trade | Norman Powell |

### 2022 (2021-22)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| James Harden | BKN | PHI | 2022-02-08 | 2022-02-25 | 38.1 | moved |  |
| Norman Powell | POR | LAC | 2022-02-02 | 2022-02-06 | 37.6 | likely trade | Eric Bledsoe |
| Josh Hart | NOP | POR | 2022-02-06 | 2022-02-09 | 36.5 | likely trade | CJ McCollum |
| Domantas Sabonis | IND | SAC | 2022-02-06 | 2022-02-09 | 35.6 | likely trade | Tristan Thompson |
| Seth Curry | PHI | BKN | 2022-02-08 | 2022-02-14 | 35.5 | likely trade | Paul Millsap |

### 2023 (2022-23)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Spencer Dinwiddie | DAL | BKN | 2023-02-04 | 2023-02-09 | 37.2 | likely trade | Kyrie Irving |
| Kyrie Irving | BKN | DAL | 2023-02-01 | 2023-02-08 | 37.0 | likely trade | Dorian Finney-Smith |
| Mikal Bridges | PHX | BKN | 2023-02-07 | 2023-02-11 | 36.9 | likely trade | T.J. Warren |
| Kevin Durant | BKN | PHX | 2023-01-08 | 2023-03-01 | 35.4 | moved |  |
| D'Angelo Russell | MIN | LAL | 2023-02-07 | 2023-02-11 | 33.2 | moved |  |

### 2024 (2023-24)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Pascal Siakam | TOR | IND | 2024-01-15 | 2024-01-19 | 35.1 | likely trade | Bruce Brown |
| Terry Rozier | CHA | MIA | 2024-01-22 | 2024-01-24 | 35.0 | moved |  |
| OG Anunoby | TOR | NYK | 2023-12-29 | 2024-01-01 | 33.9 | likely trade | RJ Barrett |
| Bojan Bogdanovic | DET | NYK | 2024-02-07 | 2024-02-10 | 33.7 | likely trade | Evan Fournier |
| Gordon Hayward | CHA | OKC | 2023-12-26 | 2024-02-22 | 31.8 | moved |  |

### 2025 (2024-25)

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| De'Aaron Fox | SAC | SAS | 2025-02-01 | 2025-02-05 | 36.6 | moved |  |
| Luka Doncic | DAL | LAL | 2024-12-25 | 2025-02-10 | 35.4 | likely trade | Anthony Davis |
| Zach LaVine | CHI | SAC | 2025-01-27 | 2025-02-05 | 35.1 | likely trade | Kevin Huerter |
| Brandon Ingram | NOP | TOR | 2024-12-07 | 2025-02-26 | 33.5 | moved |  |
| Dennis Schroder | BKN | GSW | 2024-12-13 | 2024-12-19 | 33.5 | likely trade | Reece Beekman |

## 2026-27 offseason moves seen on game sheets so far (top 25 by prior mpg)

83 players have appeared for a different team than last season (the season has just started, so most players have not been on a game sheet yet). 

| player | from_team | to_team | last_date_old | first_date_new | mpg | label | swap_partner |
|---|---|---|---|---|---|---|---|
| Jaylen Brown | BOS | PHI | 2026-05-02 | 2026-10-20 | 36.0 | likely trade | Paul George |
| LeBron James | LAL | PHI | 2026-05-11 | 2026-10-20 | 35.1 | likely trade | Quentin Grimes |
| Cody Williams | UTA | MIN | 2026-04-12 | 2026-10-21 | 34.8 | moved |  |
| Paul George | PHI | BOS | 2026-05-10 | 2026-10-20 | 33.9 | likely trade | Jaylen Brown |
| Julius Randle | MIN | BKN | 2026-05-15 | 2026-10-21 | 33.1 | moved |  |
| Rui Hachimura | LAL | LAC | 2026-05-11 | 2026-10-21 | 32.4 | moved |  |
| Tyler Herro | MIA | MIL | 2026-04-14 | 2026-10-21 | 32.2 | likely trade | Giannis Antetokounmpo |
| Marcus Smart | LAL | HOU | 2026-05-11 | 2026-10-21 | 32.1 | moved |  |
| Kawhi Leonard | LAC | TOR | 2026-04-15 | 2026-10-21 | 31.4 | likely trade | Gradey Dick |
| Anthony Davis | DAL | WAS | 2026-01-08 | 2026-10-21 | 31.2 | moved |  |
| Kelly Oubre Jr. | PHI | IND | 2026-05-10 | 2026-10-21 | 31.1 | moved |  |
| LaMelo Ball | CHA | MIN | 2026-04-17 | 2026-10-21 | 29.5 | likely trade | Naz Reid |
| Walker Kessler | UTA | LAL | 2026-03-09 | 2026-10-21 | 29.4 | likely trade | Jaxson Hayes |
| Miles Bridges | CHA | PHX | 2026-04-17 | 2026-10-21 | 29.2 | likely trade | Royce O'Neale |
| Jerami Grant | POR | MEM | 2026-04-28 | 2026-10-21 | 28.6 | likely trade | Ja Morant |
| Ja Morant | MEM | POR | 2026-01-21 | 2026-10-21 | 28.4 | likely trade | Jerami Grant |
| Jaime Jaquez Jr. | MIA | MIL | 2026-04-14 | 2026-10-21 | 28.1 | likely trade | Giannis Antetokounmpo |
| Giannis Antetokounmpo | MIL | MIA | 2026-03-15 | 2026-10-21 | 27.3 | likely trade | Tyler Herro |
| Bennedict Mathurin | LAC | NOP | 2026-04-15 | 2026-10-21 | 27.2 | moved |  |
| Anfernee Simons | CHI | PHI | 2026-04-12 | 2026-10-20 | 26.9 | moved |  |
| Rayan Rupert | MEM | PHI | 2026-04-12 | 2026-10-20 | 26.9 | moved |  |
| Naz Reid | MIN | CHA | 2026-05-15 | 2026-10-21 | 26.9 | likely trade | LaMelo Ball |
| Deandre Ayton | LAL | WAS | 2026-05-11 | 2026-10-21 | 26.6 | likely trade | Jaden Hardy |
| Leaky Black | WAS | SAC | 2026-04-12 | 2026-10-21 | 26.4 | moved |  |
| Grayson Allen | PHX | CHA | 2026-04-27 | 2026-10-21 | 25.8 | likely trade | Miles Bridges |

## 2026-27: on a new team per roster snapshot, no game played yet (top 25 by prior mpg)

34 players are on rosters_2027 for a team different from their last game-roster team and have not yet been on a 2027 game sheet (e.g. CLE and DEN had not played). 32 further rostered players have no earlier game-roster history (rookies / first NBA data). Snapshot-based, undated: see `data/derived/player_moves_pending_2027.csv`.

| player | from_team | to_team | last_date_old | mpg |
|---|---|---|---|---|
| Brandon Ingram | TOR | LAC | 2026-04-29 | 32.4 |
| Peyton Watson | DEN | CLE | 2026-04-01 | 31.6 |
| Tobias Harris | DET | SAS | 2026-05-17 | 31.1 |
| Julian Reese | WAS | NOP | 2026-04-12 | 30.9 |
| Santi Aldama | MEM | DAL | 2026-03-03 | 30.0 |
| Luke Kennard | LAL | PHX | 2026-05-11 | 29.2 |
| DeMar DeRozan | SAC | DEN | 2026-04-05 | 28.6 |
| Collin Sexton | CHI | LAL | 2026-04-12 | 27.0 |
| Max Strus | CLE | LAC | 2026-05-25 | 26.2 |
| Devin Carter | SAC | BOS | 2026-04-12 | 24.6 |
| Taurean Prince | MIL | DET | 2026-04-12 | 24.4 |
| Nic Claxton | BKN | CHI | 2026-04-12 | 24.4 |
| Elijah Harkless | UTA | DET | 2026-04-12 | 24.1 |
| Malachi Smith | BKN | TOR | 2026-04-12 | 23.9 |
| Dean Wade | CLE | PHI | 2026-05-25 | 22.2 |
| Kam Jones | IND | MIL | 2026-04-12 | 21.7 |
| Marvin Bagley III | DAL | DEN | 2026-04-12 | 21.1 |
| Ryan Nembhard | DAL | DEN | 2026-04-12 | 20.6 |
| Oscar Tshiebwe | UTA | HOU | 2026-04-12 | 18.3 |
| Jalen Wilson | BKN | ATL | 2026-04-12 | 18.1 |
| Cam Whitmore | WAS | DEN | 2026-04-12 | 16.8 |
| Mario Hezonja | POR | CLE | 2020-08-29 | 15.7 |
| Jett Howard | ORL | DAL | 2026-05-03 | 14.7 |
| Isaiah Stewart | DET | MEM | 2026-05-17 | 13.6 |
| Branden Carlson | OKC | POR | 2026-04-12 | 13.3 |
