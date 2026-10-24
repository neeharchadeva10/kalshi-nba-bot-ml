# Roster multi-source check (season 2027)

Generated 2026-10-23 13:00 UTC by `scripts/nba_rosters.py`.

Sources compared: **A** rosters_2027 (current team list), **B** game_rosters_2027 (who was on each game sheet, active or inactive), **C** player_box_2027 (who actually played).

- A: GitHub last commit 2026-10-22T11:34:13Z; downloaded 2026-10-23T03:58:34Z; latest game date in data n/a
- B: GitHub last commit 2026-10-22T11:34:13Z; downloaded 2026-10-23T03:58:29Z; latest game date in data 2026-10-21
- C: GitHub last commit 2026-10-22T11:34:13Z; downloaded 2026-10-23T03:58:30Z; latest game date in data 2026-10-21

Reading guide: B lists only the ~13-15 players on a game sheet, A lists 16-18 (two-way / reserve players do not appear on game sheets), so 'on roster but not on the last game sheet' is mostly normal. The informative lines are the ones marked REAL SIGNAL.

## Summary

- A-only vs team's last game sheet (on roster, not on last sheet): **100**
- REAL SIGNAL: on team's last game sheet but NOT on current roster (waived/traded/moved since): **0**
- on roster, never on any game sheet yet this season: **100**
- REAL SIGNAL: last game-sheet team differs from roster team: **0**
- REAL SIGNAL: played for team X in box score, roster says team Y: **0**
- REAL SIGNAL: played in box score, absent from rosters file entirely: **0**
- rosters_2026 -> rosters_2027 team changed (offseason moves per roster snapshots): **108**
- Teams with no games yet (no sheet to compare): CLE, DEN

## REAL SIGNAL: on last game sheet, not on current roster

_(none)_

## REAL SIGNAL: last game-sheet team differs from roster team

_(none)_

## REAL SIGNAL: box-score team differs from roster team

_(none)_

## REAL SIGNAL: played but absent from rosters file

_(none)_

## On roster, not on the team's last game sheet

| team | name | athlete_id | last_game | on_any_2027_game_sheet |
|---|---|---|---|---|
| ATL | Jalen Wilson | 4431714 | 2026-10-21 | False |
| ATL | RayJ Dennis | 4431941 | 2026-10-21 | False |
| ATL | Keshon Gilbert | 4585618 | 2026-10-21 | False |
| ATL | Mouhamed Gueye | 4712863 | 2026-10-21 | False |
| ATL | Henri Veesaar | 5105571 | 2026-10-21 | False |
| BKN | Grant Nelson | 4702670 | 2026-10-21 | False |
| BKN | Joshua Jefferson | 4870564 | 2026-10-21 | False |
| BKN | Tyler Bilodeau | 5105626 | 2026-10-21 | False |
| BOS | Devin Carter | 4433188 | 2026-10-20 | False |
| BOS | Amari Williams | 4702745 | 2026-10-20 | False |
| BOS | Dillon Mitchell | 5106283 | 2026-10-20 | False |
| CHA | Grant Williams | 4066218 | 2026-10-21 | False |
| CHA | Coby White | 4395651 | 2026-10-21 | False |
| CHA | Kylan Boswell | 4684269 | 2026-10-21 | False |
| CHA | Kon Knueppel | 5061575 | 2026-10-21 | False |
| CHI | Nic Claxton | 4278067 | 2026-10-21 | False |
| CHI | Jordan Hawkins | 4683750 | 2026-10-21 | False |
| CHI | Dailyn Swain | 4848625 | 2026-10-21 | False |
| DAL | Caleb Martin | 3138160 | 2026-10-21 | False |
| DAL | Santi Aldama | 4593125 | 2026-10-21 | False |
| DAL | Dereck Lively II | 4683688 | 2026-10-21 | False |
| DAL | Jett Howard | 5105806 | 2026-10-21 | False |
| DAL | Tarik Biberovic | 5148538 | 2026-10-21 | False |
| DET | Taurean Prince | 2990962 | 2026-10-20 | False |
| DET | Elijah Harkless | 4397449 | 2026-10-20 | False |
| DET | Ugonna Onyenso | 5037878 | 2026-10-20 | False |
| DET | Isaac Jones | 5107818 | 2026-10-20 | False |
| GSW | Jimmy Butler III | 6430 | 2026-10-21 | False |
| GSW | Kristaps Porzingis | 3102531 | 2026-10-21 | False |
| GSW | Charles Bassey | 4397886 | 2026-10-21 | False |
| GSW | Moses Moody | 4432171 | 2026-10-21 | False |
| GSW | Brandin Podziemski | 4709138 | 2026-10-21 | False |
| HOU | Oscar Tshiebwe | 4432827 | 2026-10-21 | False |
| HOU | Quadir Copeland | 4683860 | 2026-10-21 | False |
| HOU | Julian Phillips | 5105553 | 2026-10-21 | False |
| IND | T.J. McConnell | 2530530 | 2026-10-21 | False |
| IND | Aaron Nesmith | 4396909 | 2026-10-21 | False |
| IND | Braden Smith | 5105854 | 2026-10-21 | False |
| IND | Johnny Furphy | 5157066 | 2026-10-21 | False |
| LAC | Bradley Beal | 6580 | 2026-10-21 | False |
| LAC | Brandon Ingram | 3913176 | 2026-10-21 | False |
| LAC | Max Strus | 4065778 | 2026-10-21 | False |
| LAC | Jordan Miller | 4396818 | 2026-10-21 | False |
| LAC | Yanic Konan Niederhauser | 5108024 | 2026-10-21 | False |
| LAL | Collin Sexton | 4277811 | 2026-10-21 | False |
| LAL | Arthur Kaluma | 4433599 | 2026-10-21 | False |
| LAL | Adou Thiero | 5060631 | 2026-10-21 | False |
| LAL | Cameron Carr | 5113969 | 2026-10-21 | False |
| LAL | AK Okereke | 5114350 | 2026-10-21 | False |
| MEM | Isaiah Stewart | 4432810 | 2026-10-21 | False |
| MEM | Javon Small | 4781746 | 2026-10-21 | False |
| MEM | Carson Cooper | 5105817 | 2026-10-21 | False |
| MIA | Keshad Johnson | 4431786 | 2026-10-21 | False |
| MIA | Vladislav Goldin | 4700818 | 2026-10-21 | False |
| MIA | Tre Donaldson | 4837356 | 2026-10-21 | False |
| MIL | Cormac Ryan | 4397114 | 2026-10-21 | False |
| MIL | Pete Nance | 4397233 | 2026-10-21 | False |
| MIL | Kam Jones | 4697268 | 2026-10-21 | False |
| MIN | Donte DiVincenzo | 3934673 | 2026-10-21 | False |
| MIN | Zyon Pullin | 4592979 | 2026-10-21 | False |
| MIN | Rocco Zikarsky | 5157587 | 2026-10-21 | False |
| NOP | Julian Reese | 4683742 | 2026-10-21 | False |
| NOP | Jaron Pierre Jr. | 4703115 | 2026-10-21 | False |
| NOP | Malik Dia | 4712862 | 2026-10-21 | False |
| NYK | Jaden Akins | 4683730 | 2026-10-20 | False |
| NYK | Tyler Nickel | 4838721 | 2026-10-20 | False |
| OKC | Thomas Sorber | 5061603 | 2026-10-20 | False |
| OKC | Josh Dix | 5105800 | 2026-10-20 | False |
| OKC | Otega Oweh | 5106270 | 2026-10-20 | False |
| OKC | Nikola Topic | 5159925 | 2026-10-20 | False |
| ORL | Jonathan Isaac | 4065654 | 2026-10-21 | False |
| ORL | Anthony Black | 4712849 | 2026-10-21 | False |
| ORL | Izaiyah Nelson | 5107251 | 2026-10-21 | False |
| ORL | Noah Penda | 5214637 | 2026-10-21 | False |
| PHI | Tacko Fall | 3904625 | 2026-10-20 | False |
| PHI | Dean Wade | 3912848 | 2026-10-20 | False |
| PHI | Dominick Barlow | 4870562 | 2026-10-20 | False |
| PHI | Ariel Hukporti | 4871141 | 2026-10-20 | False |
| PHX | Luke Kennard | 3913174 | 2026-10-21 | False |
| PHX | Drew Timme | 4431695 | 2026-10-21 | False |
| PHX | Mark Williams | 4701232 | 2026-10-21 | False |
| POR | Branden Carlson | 4592435 | 2026-10-21 | False |
| POR | John Tonje | 4593043 | 2026-10-21 | False |
| POR | Chris Youngblood | 4706557 | 2026-10-21 | False |
| POR | Shaedon Sharpe | 4914336 | 2026-10-21 | False |
| SAC | Ben Simmons | 3907387 | 2026-10-21 | False |
| SAC | Malik Monk | 4066262 | 2026-10-21 | False |
| SAC | Dylan Cardwell | 4433174 | 2026-10-21 | False |
| SAC | Maxime Raynaud | 4898371 | 2026-10-21 | False |
| SAS | Tobias Harris | 6440 | 2026-10-20 | False |
| SAS | Jayden Quaintance | 5101845 | 2026-10-20 | False |
| TOR | Malachi Smith | 4397784 | 2026-10-21 | False |
| TOR | Nate Bittle | 4432586 | 2026-10-21 | False |
| UTA | Jusuf Nurkic | 3102530 | 2026-10-21 | False |
| UTA | Tony Bradley | 4065673 | 2026-10-21 | False |
| UTA | Blake Hinson | 4396963 | 2026-10-21 | False |
| UTA | Trey Alexander | 4433268 | 2026-10-21 | False |
| WAS | Jamir Watkins | 4606840 | 2026-10-21 | False |
| WAS | Tristan Vukcevic | 4997537 | 2026-10-21 | False |
| WAS | AJ Dybantsa | 5142718 | 2026-10-21 | False |

## On roster, never on a game sheet this season

| team | name | athlete_id |
|---|---|---|
| ATL | Jalen Wilson | 4431714 |
| ATL | RayJ Dennis | 4431941 |
| ATL | Keshon Gilbert | 4585618 |
| ATL | Mouhamed Gueye | 4712863 |
| ATL | Henri Veesaar | 5105571 |
| BKN | Grant Nelson | 4702670 |
| BKN | Joshua Jefferson | 4870564 |
| BKN | Tyler Bilodeau | 5105626 |
| BOS | Devin Carter | 4433188 |
| BOS | Amari Williams | 4702745 |
| BOS | Dillon Mitchell | 5106283 |
| CHA | Grant Williams | 4066218 |
| CHA | Coby White | 4395651 |
| CHA | Kylan Boswell | 4684269 |
| CHA | Kon Knueppel | 5061575 |
| CHI | Nic Claxton | 4278067 |
| CHI | Jordan Hawkins | 4683750 |
| CHI | Dailyn Swain | 4848625 |
| DAL | Caleb Martin | 3138160 |
| DAL | Santi Aldama | 4593125 |
| DAL | Dereck Lively II | 4683688 |
| DAL | Jett Howard | 5105806 |
| DAL | Tarik Biberovic | 5148538 |
| DET | Taurean Prince | 2990962 |
| DET | Elijah Harkless | 4397449 |
| DET | Ugonna Onyenso | 5037878 |
| DET | Isaac Jones | 5107818 |
| GSW | Jimmy Butler III | 6430 |
| GSW | Kristaps Porzingis | 3102531 |
| GSW | Charles Bassey | 4397886 |
| GSW | Moses Moody | 4432171 |
| GSW | Brandin Podziemski | 4709138 |
| HOU | Oscar Tshiebwe | 4432827 |
| HOU | Quadir Copeland | 4683860 |
| HOU | Julian Phillips | 5105553 |
| IND | T.J. McConnell | 2530530 |
| IND | Aaron Nesmith | 4396909 |
| IND | Braden Smith | 5105854 |
| IND | Johnny Furphy | 5157066 |
| LAC | Bradley Beal | 6580 |
| LAC | Brandon Ingram | 3913176 |
| LAC | Max Strus | 4065778 |
| LAC | Jordan Miller | 4396818 |
| LAC | Yanic Konan Niederhauser | 5108024 |
| LAL | Collin Sexton | 4277811 |
| LAL | Arthur Kaluma | 4433599 |
| LAL | Adou Thiero | 5060631 |
| LAL | Cameron Carr | 5113969 |
| LAL | AK Okereke | 5114350 |
| MEM | Isaiah Stewart | 4432810 |
| MEM | Javon Small | 4781746 |
| MEM | Carson Cooper | 5105817 |
| MIA | Keshad Johnson | 4431786 |
| MIA | Vladislav Goldin | 4700818 |
| MIA | Tre Donaldson | 4837356 |
| MIL | Cormac Ryan | 4397114 |
| MIL | Pete Nance | 4397233 |
| MIL | Kam Jones | 4697268 |
| MIN | Donte DiVincenzo | 3934673 |
| MIN | Zyon Pullin | 4592979 |
| MIN | Rocco Zikarsky | 5157587 |
| NOP | Julian Reese | 4683742 |
| NOP | Jaron Pierre Jr. | 4703115 |
| NOP | Malik Dia | 4712862 |
| NYK | Jaden Akins | 4683730 |
| NYK | Tyler Nickel | 4838721 |
| OKC | Thomas Sorber | 5061603 |
| OKC | Josh Dix | 5105800 |
| OKC | Otega Oweh | 5106270 |
| OKC | Nikola Topic | 5159925 |
| ORL | Jonathan Isaac | 4065654 |
| ORL | Anthony Black | 4712849 |
| ORL | Izaiyah Nelson | 5107251 |
| ORL | Noah Penda | 5214637 |
| PHI | Tacko Fall | 3904625 |
| PHI | Dean Wade | 3912848 |
| PHI | Dominick Barlow | 4870562 |
| PHI | Ariel Hukporti | 4871141 |
| PHX | Luke Kennard | 3913174 |
| PHX | Drew Timme | 4431695 |
| PHX | Mark Williams | 4701232 |
| POR | Branden Carlson | 4592435 |
| POR | John Tonje | 4593043 |
| POR | Chris Youngblood | 4706557 |
| POR | Shaedon Sharpe | 4914336 |
| SAC | Ben Simmons | 3907387 |
| SAC | Malik Monk | 4066262 |
| SAC | Dylan Cardwell | 4433174 |
| SAC | Maxime Raynaud | 4898371 |
| SAS | Tobias Harris | 6440 |
| SAS | Jayden Quaintance | 5101845 |
| TOR | Malachi Smith | 4397784 |
| TOR | Nate Bittle | 4432586 |
| UTA | Jusuf Nurkic | 3102530 |
| UTA | Tony Bradley | 4065673 |
| UTA | Blake Hinson | 4396963 |
| UTA | Trey Alexander | 4433268 |
| WAS | Jamir Watkins | 4606840 |
| WAS | Tristan Vukcevic | 4997537 |
| WAS | AJ Dybantsa | 5142718 |

## Offseason team changes between rosters_2026 and rosters_2027

(snapshot-vs-snapshot; the rosters_2026 file's own date is unknown, see as-of note)

| name | athlete_id | from | to |
|---|---|---|---|
| Luguentz Dort | 4397020 | OKC | ATL |
| Dorian Finney-Smith | 2578185 | HOU | ATL |
| Aaron Wiggins | 4397183 | OKC | ATL |
| Jalen Wilson | 4431714 | BKN | ATL |
| Bogdan Bogdanovic | 3037789 | LAC | HOU |
| Julian Phillips | 5105553 | MIN | HOU |
| Marcus Smart | 2990992 | LAL | HOU |
| Oscar Tshiebwe | 4432827 | UTA | HOU |
| Larry Nance Jr. | 2580365 | CLE | IND |
| Kelly Oubre Jr. | 3133603 | PHI | IND |
| Gradey Dick | 5106258 | TOR | LAC |
| Rui Hachimura | 4066648 | LAL | LAC |
| Brandon Ingram | 3913176 | TOR | LAC |
| Jalen Pickett | 4398390 | DEN | LAC |
| Max Strus | 4065778 | CLE | LAC |
| Quentin Grimes | 4397014 | PHI | LAL |
| Jaden Hardy | 4868423 | WAS | LAL |
| Walker Kessler | 4433136 | UTA | LAL |
| Kevon Looney | 3155535 | NOP | LAL |
| Sandro Mamukelashvili | 4278580 | TOR | LAL |
| Collin Sexton | 4277811 | CHI | LAL |
| Matisse Thybulle | 3907498 | POR | LAL |
| Ziaire Williams | 4433137 | BKN | LAL |
| Giannis Antetokounmpo | 3032977 | MIL | MIA |
| Tim Hardaway Jr. | 2528210 | DEN | MIA |
| Bobby Portis | 3064482 | MIL | MIA |
| Nick Richards | 4278076 | CHI | MIA |
| Klay Thompson | 6475 | DAL | MIA |
| Tyler Herro | 4395725 | MIA | MIL |
| Kasparas Jakucionis | 5214640 | MIA | MIL |
| Jaime Jaquez Jr. | 4432848 | MIA | MIL |
| Kam Jones | 4697268 | IND | MIL |
| Caris LeVert | 2991043 | DET | MIL |
| Kel'el Ware | 5105623 | MIA | MIL |
| LaMelo Ball | 4432816 | CHA | MIN |
| Jonathan Kuminga | 4433247 | ATL | MIN |
| Cody Williams | 4895758 | UTA | MIN |
| Keon Ellis | 4702177 | CLE | BKN |
| Julius Randle | 3064514 | MIN | BKN |
| Moritz Wagner | 3150844 | ORL | BKN |
| Andre Drummond | 6585 | PHI | NYK |
| Nikola Vucevic | 6478 | BOS | ORL |
| Devin Carter | 4433188 | SAC | BOS |
| Mike Conley | 3195 | MIN | BOS |
| Paul George | 4251 | PHI | BOS |
| Mitchell Robinson | 4351852 | NYK | BOS |
| Jaylen Brown | 3917376 | BOS | PHI |
| Kentavious Caldwell-Pope | 2581018 | MEM | PHI |
| Ariel Hukporti | 4871141 | NYK | PHI |
| LeBron James | 1966 | LAL | PHI |
| Caleb Love | 4433144 | POR | PHI |
| Rayan Rupert | 5099752 | MEM | PHI |
| Anfernee Simons | 4351851 | CHI | PHI |
| Dean Wade | 3912848 | CLE | PHI |
| Miles Bridges | 4066383 | CHA | PHX |
| Luke Kennard | 3913174 | LAL | PHX |
| Pat Spencer | 4592714 | GSW | PHX |
| Drew Timme | 4431695 | LAL | PHX |
| Branden Carlson | 4592435 | OKC | POR |
| Ja Morant | 4279888 | MEM | POR |
| Micah Potter | 4066399 | IND | POR |
| John Tonje | 4593043 | BOS | POR |
| Leaky Black | 4395650 | WAS | SAC |
| Jonathan Mogbo | 5107897 | TOR | SAC |
| Tobias Harris | 6440 | DET | SAS |
| Trey Alexander | 4433268 | NOP | UTA |
| Tony Bradley | 4065673 | ATL | UTA |
| Josh Green | 4432811 | CHA | UTA |
| Jaxson Hayes | 4397077 | LAL | UTA |
| Harrison Ingram | 4433618 | SAS | UTA |
| Josh Okogie | 4065663 | HOU | UTA |
| Deandre Ayton | 4278129 | LAL | WAS |
| Tre Mann | 4432819 | CHA | WAS |
| Khris Middleton | 6609 | DAL | WAS |
| Kyle Anderson | 2993874 | MIN | TOR |
| Kawhi Leonard | 6450 | LAC | TOR |
| Malachi Smith | 4397784 | BKN | TOR |
| Jerami Grant | 2991070 | POR | MEM |
| Kris Murray | 4594326 | POR | MEM |
| Quinten Post | 4593016 | GSW | MEM |
| Isaiah Stewart | 4432810 | DET | MEM |
| AJ Johnson | 5170947 | DAL | NOP |
| Bennedict Mathurin | 4683634 | LAC | NOP |
| Julian Reese | 4683742 | WAS | NOP |
| Trendon Watford | 4431675 | PHI | NOP |
| Grayson Allen | 3135045 | PHX | CHA |
| Royce O'Neale | 2583632 | PHX | CHA |
| Naz Reid | 4396971 | MIN | CHA |
| Dennis Schroder | 3032979 | CLE | CHA |
| Nic Claxton | 4278067 | BKN | CHI |
| Jordan Hawkins | 4683750 | NOP | CHI |
| Buddy Hield | 2990984 | ATL | CHI |
| Norman Powell | 2595516 | MIA | CHI |
| Peyton Watson | 4576087 | DEN | CLE |
| Santi Aldama | 4593125 | MEM | DAL |
| Walter Clayton Jr. | 4896372 | MEM | DAL |
| Jett Howard | 5105806 | ORL | DAL |
| Zaccharie Risacher | 5211175 | ATL | DAL |
| Marcus Sasser | 4432107 | DET | DAL |
| Marvin Bagley III | 4277848 | DAL | DEN |
| DeMar DeRozan | 3978 | SAC | DEN |
| Ryan Nembhard | 4433629 | DAL | DEN |
| Cam Whitmore | 5105592 | WAS | DEN |
| John Collins | 3908845 | LAC | DET |
| Elijah Harkless | 4397449 | UTA | DET |
| Isaiah Joe | 4395702 | OKC | DET |
| Taurean Prince | 2990962 | MIL | DET |
| Brandon Williams | 4397040 | DAL | GSW |
