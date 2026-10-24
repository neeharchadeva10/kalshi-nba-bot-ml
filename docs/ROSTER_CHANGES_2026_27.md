# Roster changes 2026-27 (bench upgrades, adds and losses)

Generated 2026-10-23 by `scripts/roster_changes.py` from local files only.

**Read this first: every number below is LAST SEASON's (2025-26 regular season) production of the players who moved. It is not a prediction of this season** (age, role, health, new team fit and minutes all change). "Net" = incoming players' last-season per-game numbers minus outgoing players'. Tiers by last-season minutes per game: star 28+, starter 20-28, bench 12-20, deep <12. Bench-upgrade = net change among 12-28 mpg players. Veteran = age 32+. Moves come from the roster-snapshot comparison (player_moves.csv + pending file); a few may be waivers/free-agent signings rather than trades, and players with no 2025-26 NBA team (2026 draft picks, overseas or first-time signings) or who left the NBA are NOT in the move files, so a team's IN side can be undercounted (e.g. a team showing 0 in).

## League table (ranked by net points per game added)

| # | Team | In | Out | Net mpg | Net ppg | Star ppg | Starter ppg | Bench ppg | Bench-upgrade mpg (12-28) | Veterans 32+ added |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | PHI | 9 | 5 | +53.2 | +36.5 | +3.1 | +38.9 | -7.7 | +70.2 | Kentavious Caldwell-Pope, LeBron James |
| 2 | NOP | 4 | 3 | +46.5 | +26.2 | +29.4 | +0.0 | -6.6 | -24.1 | - |
| 3 | HOU | 4 | 2 | +39.6 | +19.6 | +9.3 | +0.0 | +7.4 | +2.2 | Bogdan Bogdanovic, Marcus Smart |
| 4 | MIL | 6 | 3 | +58.8 | +14.5 | +8.3 | -11.8 | +18.0 | +28.2 | Caris LeVert |
| 5 | PHX | 5 | 2 | +31.1 | +12.8 | -9.2 | +8.4 | +7.2 | +40.2 | - |
| 6 | SAS | 1 | 1 | +24.1 | +11.7 | +0.0 | +13.3 | +0.0 | +27.8 | Tobias Harris |
| 7 | DEN | 4 | 3 | +15.5 | +11.3 | +3.8 | -3.1 | +10.6 | +13.8 | DeMar DeRozan |
| 8 | WAS | 5 | 4 | +1.5 | +11.3 | +1.5 | +22.7 | -12.9 | +30.0 | Anthony Davis, Khris Middleton |
| 9 | CHI | 4 | 3 | +22.7 | +9.9 | +21.1 | -18.0 | +6.8 | -6.2 | Buddy Hield, Norman Powell |
| 10 | LAL | 8 | 7 | +10.4 | +9.5 | -13.2 | +15.9 | +10.3 | +47.7 | - |
| 11 | DET | 4 | 4 | +11.1 | +4.8 | +0.0 | +17.4 | -12.6 | +11.1 | Taurean Prince |
| 12 | CHA | 4 | 4 | +20.2 | +3.7 | -10.9 | +24.4 | -9.8 | +22.0 | Dennis Schroder, Royce O'Neale |
| 13 | ORL | 1 | 2 | +3.8 | +2.7 | +15.1 | +0.0 | -5.5 | -12.7 | Nikola Vucevic |
| 14 | TOR | 3 | 4 | -0.6 | +2.0 | +6.3 | -2.9 | +0.2 | +7.9 | Kawhi Leonard, Kyle Anderson |
| 15 | NYK | 2 | 2 | +5.5 | +1.8 | +0.0 | +0.0 | +4.0 | +14.8 | Andre Drummond |
| 16 | IND | 2 | 3 | -6.3 | +0.5 | +14.1 | +0.0 | -13.6 | -37.8 | Larry Nance Jr. |
| 17 | MIA | 5 | 5 | -12.2 | -1.9 | -29.4 | +27.8 | -0.3 | +47.3 | Klay Thompson, Tim Hardaway Jr. |
| 18 | GSW | 2 | 2 | -13.7 | -1.9 | +0.0 | +13.0 | -14.9 | -13.7 | Georges Niang |
| 19 | BKN | 3 | 5 | -25.0 | -1.9 | +21.1 | -23.5 | -6.4 | -70.0 | - |
| 20 | POR | 4 | 6 | -30.2 | -5.6 | +0.8 | -16.2 | +3.9 | -40.9 | - |
| 21 | ATL | 4 | 4 | +8.3 | -6.0 | +0.0 | -4.1 | +2.1 | +19.6 | Dorian Finney-Smith |
| 22 | MIN | 4 | 5 | -31.0 | -7.2 | -1.0 | +7.4 | -10.7 | -17.0 | - |
| 23 | BOS | 4 | 3 | +16.8 | -9.5 | -25.5 | +0.0 | +19.0 | +56.4 | Mike Conley, Paul George |
| 24 | UTA | 6 | 5 | -14.2 | -10.7 | -14.4 | -15.6 | +13.7 | +1.8 | - |
| 25 | LAC | 5 | 5 | +7.5 | -11.0 | -12.4 | -2.4 | +3.8 | +7.3 | - |
| 26 | MEM | 4 | 5 | -21.4 | -12.7 | -0.8 | -14.4 | +2.5 | -22.8 | Jerami Grant |
| 27 | SAC | 3 | 3 | -14.3 | -18.6 | -11.3 | +0.0 | -8.9 | -18.4 | - |
| 28 | CLE | 2 | 6 | -74.4 | -23.6 | +14.6 | -34.5 | -3.7 | -104.0 | - |
| 29 | DAL | 5 | 7 | -51.8 | -33.6 | -20.4 | -13.9 | +4.1 | -10.9 | - |
| 30 | OKC | 0 | 4 | -81.5 | -34.6 | +0.0 | -28.8 | +0.0 | -69.8 | - |

## Teams (alphabetical)

### ATL

ATL loses more than it adds last-season production: 4 in, 4 out, net +8.3 mpg / -6.0 ppg. Biggest add: Luguentz Dort (27 mpg, 8.3 ppg). Biggest loss: Jonathan Kuminga (23 mpg, 12.2 ppg). Rotation (12-28 mpg) gains 19.6 mpg. Veterans (32+) added: Dorian Finney-Smith.

Net by tier (mpg / ppg): star +0.0 / +0.0; starter +3.1 / -4.1; bench +16.5 / +2.1; deep -11.3 / -4.0; rotation 12-28 mpg +19.6 / -2.0.

**In**
- Luguentz Dort (OKC -> ATL, age 27): 69 gp, 26.8 mpg, 8.3 ppg, 3.6 rpg, 1.2 apg, +/- +4.3 [starter]
- Aaron Wiggins (OKC -> ATL, age 27): 65 gp, 21.8 mpg, 9.4 ppg, 3.1 rpg, 1.7 apg, +/- +2.7 [starter]
- Dorian Finney-Smith (HOU -> ATL, age 33): 37 gp, 16.7 mpg, 3.3 ppg, 2.5 rpg, 1.0 apg, +/- +0.4 [bench, VET]
- Jalen Wilson (BKN -> ATL, age 25): 54 gp, 15.9 mpg, 6.4 ppg, 2.1 rpg, 0.9 apg, +/- -3.6 [bench]

**Out**
- Jonathan Kuminga (ATL -> MIN, age 24): 36 gp, 23.1 mpg, 12.2 ppg, 5.6 rpg, 2.3 apg, +/- +2.0 [starter]
- Zaccharie Risacher (ATL -> DAL, age 21): 67 gp, 22.4 mpg, 9.6 ppg, 3.8 rpg, 1.1 apg, +/- -0.2 [starter]
- Buddy Hield (ATL -> CHI, age 33): 51 gp, 16.1 mpg, 7.6 ppg, 2.3 rpg, 1.4 apg, +/- -1.7 [bench, VET]
- Tony Bradley (ATL -> UTA, age 28): 40 gp, 11.3 mpg, 4.0 ppg, 2.9 rpg, 0.6 apg, +/- -3.5 [deep]

### BKN

BKN loses more than it adds last-season production: 3 in, 5 out, net -25.0 mpg / -1.9 ppg. Biggest add: Julius Randle (33 mpg, 21.1 ppg). Biggest loss: Nic Claxton (28 mpg, 11.7 ppg). Rotation (12-28 mpg) loses 70.0 mpg.

Net by tier (mpg / ppg): star +33.1 / +21.1; starter -54.1 / -23.5; bench -15.9 / -6.4; deep +11.9 / +6.9; rotation 12-28 mpg -70.0 / -29.9.

**In**
- Julius Randle (MIN -> BKN, age 31): 79 gp, 33.1 mpg, 21.1 ppg, 6.7 rpg, 5.0 apg, +/- +2.9 [star]
- Keon Ellis (CLE -> BKN, age 26): 72 gp, 20.5 mpg, 6.7 ppg, 1.9 rpg, 1.0 apg, +/- -2.0 [starter]
- Moritz Wagner (ORL -> BKN, age 29): 36 gp, 11.9 mpg, 6.9 ppg, 3.2 rpg, 0.8 apg, +/- -2.4 [deep]

**Out**
- Nic Claxton (BKN -> CHI, age 27): 69 gp, 27.8 mpg, 11.7 ppg, 6.9 rpg, 3.7 apg, +/- -7.5 [starter]
- Malachi Smith (BKN -> TOR, age 26): 15 gp, 23.9 mpg, 8.3 ppg, 3.4 rpg, 3.3 apg, +/- -6.0 [starter]
- Ziaire Williams (BKN -> LAL, age 25): 56 gp, 22.9 mpg, 10.2 ppg, 2.4 rpg, 1.1 apg, +/- -5.0 [starter]
- Jalen Wilson (BKN -> ATL, age 25): 54 gp, 15.9 mpg, 6.4 ppg, 2.1 rpg, 0.9 apg, +/- -3.6 [bench]
- Reece Beekman (BKN -> WAS, age 25): 0 gp, n/a mpg, n/a ppg, n/a rpg, n/a apg [none, no 2025-26 minutes]

### BOS

BOS loses more than it adds last-season production: 4 in, 3 out, net +16.8 mpg / -9.5 ppg. Biggest add: Paul George (31 mpg, 17.3 ppg). Biggest loss: Jaylen Brown (33 mpg, 27.7 ppg). Rotation (12-28 mpg) gains 56.4 mpg. Veterans (32+) added: Mike Conley, Paul George.

Net by tier (mpg / ppg): star -31.2 / -25.5; starter +0.0 / +0.0; bench +56.4 / +19.0; deep -8.4 / -3.0; rotation 12-28 mpg +56.4 / +19.0.

**In**
- Paul George (PHI -> BOS, age 36): 37 gp, 30.6 mpg, 17.3 ppg, 5.3 rpg, 3.6 apg, +/- +2.4 [star, VET]
- Mitchell Robinson (NYK -> BOS, age 28): 61 gp, 19.6 mpg, 5.6 ppg, 8.9 rpg, 0.9 apg, +/- +2.7 [bench]
- Mike Conley (MIN -> BOS, age 39): 54 gp, 18.4 mpg, 4.5 ppg, 1.7 rpg, 2.9 apg, +/- +0.3 [bench, VET]
- Devin Carter (SAC -> BOS, age 24): 38 gp, 18.4 mpg, 8.9 ppg, 3.3 rpg, 2.7 apg, +/- -3.0 [bench]

**Out**
- Jaylen Brown (BOS -> PHI, age 29): 74 gp, 33.4 mpg, 27.7 ppg, 6.7 rpg, 4.9 apg, +/- +4.0 [star]
- Nikola Vucevic (BOS -> ORL, age 35): 64 gp, 28.4 mpg, 15.1 ppg, 8.4 rpg, 3.3 apg, +/- -0.7 [star, VET]
- John Tonje (BOS -> POR, age 25): 5 gp, 8.4 mpg, 3.0 ppg, 1.2 rpg, 0.4 apg, +/- -1.2 [deep, 5 gp only]

### CHA

CHA adds more than it loses last-season production: 4 in, 4 out, net +20.2 mpg / +3.7 ppg. Biggest add: Grayson Allen (29 mpg, 16.5 ppg). Biggest loss: Miles Bridges (31 mpg, 17.1 ppg). Rotation (12-28 mpg) gains 22.0 mpg. Veterans (32+) added: Dennis Schroder, Royce O'Neale.

Net by tier (mpg / ppg): star -1.8 / -10.9; starter +50.3 / +24.4; bench -28.3 / -9.8; deep +0.0 / +0.0; rotation 12-28 mpg +22.0 / +14.6.

**In**
- Grayson Allen (PHX -> CHA, age 31): 51 gp, 28.8 mpg, 16.5 ppg, 3.0 rpg, 3.8 apg, +/- +0.4 [star]
- Royce O'Neale (PHX -> CHA, age 33): 78 gp, 28.4 mpg, 9.8 ppg, 4.8 rpg, 2.7 apg, +/- -1.4 [star, VET]
- Naz Reid (MIN -> CHA, age 27): 77 gp, 26.1 mpg, 13.6 ppg, 6.2 rpg, 2.2 apg, +/- +2.2 [starter]
- Dennis Schroder (CLE -> CHA, age 33): 70 gp, 24.2 mpg, 10.8 ppg, 2.7 rpg, 4.9 apg, +/- -3.4 [starter, VET]

**Out**
- Miles Bridges (CHA -> PHX, age 28): 77 gp, 31.0 mpg, 17.1 ppg, 5.8 rpg, 3.2 apg, +/- +1.9 [star]
- LaMelo Ball (CHA -> MIN, age 25): 72 gp, 28.0 mpg, 20.1 ppg, 4.8 rpg, 7.1 apg, +/- +5.8 [star]
- Josh Green (CHA -> UTA, age 25): 58 gp, 15.7 mpg, 4.3 ppg, 1.8 rpg, 0.8 apg, +/- +3.6 [bench]
- Tre Mann (CHA -> WAS, age 25): 53 gp, 12.6 mpg, 5.5 ppg, 1.7 rpg, 1.6 apg, +/- -2.9 [bench]

### CHI

CHI adds more than it loses last-season production: 4 in, 3 out, net +22.7 mpg / +9.9 ppg. Biggest add: Norman Powell (29 mpg, 21.1 ppg). Biggest loss: Anfernee Simons (25 mpg, 14.3 ppg). Rotation (12-28 mpg) loses 6.2 mpg. Veterans (32+) added: Buddy Hield, Norman Powell.

Net by tier (mpg / ppg): star +28.9 / +21.1; starter -20.8 / -18.0; bench +14.6 / +6.8; deep +0.0 / +0.0; rotation 12-28 mpg -6.2 / -11.2.

**In**
- Norman Powell (MIA -> CHI, age 33): 60 gp, 28.9 mpg, 21.1 ppg, 3.4 rpg, 2.4 apg, +/- +0.5 [star, VET]
- Nic Claxton (BKN -> CHI, age 27): 69 gp, 27.8 mpg, 11.7 ppg, 6.9 rpg, 3.7 apg, +/- -7.5 [starter]
- Buddy Hield (ATL -> CHI, age 33): 51 gp, 16.1 mpg, 7.6 ppg, 2.3 rpg, 1.4 apg, +/- -1.7 [bench, VET]
- Jordan Hawkins (NOP -> CHI, age 24): 51 gp, 13.5 mpg, 5.1 ppg, 1.7 rpg, 0.8 apg, +/- -2.6 [bench]

**Out**
- Anfernee Simons (CHI -> PHI, age 27): 55 gp, 24.9 mpg, 14.3 ppg, 2.5 rpg, 2.4 apg, +/- +1.8 [starter]
- Collin Sexton (CHI -> LAL, age 27): 68 gp, 23.7 mpg, 15.4 ppg, 2.3 rpg, 3.3 apg, +/- -1.7 [starter]
- Nick Richards (CHI -> MIA, age 28): 47 gp, 15.0 mpg, 5.9 ppg, 5.2 rpg, 0.3 apg, +/- -2.9 [bench]

### CLE

CLE loses more than it adds last-season production: 2 in, 6 out, net -74.4 mpg / -23.6 ppg. Biggest add: Peyton Watson (30 mpg, 14.6 ppg). Biggest loss: Dennis Schroder (24 mpg, 10.8 ppg). Rotation (12-28 mpg) loses 104.0 mpg.

Net by tier (mpg / ppg): star +29.6 / +14.6; starter -91.1 / -34.5; bench -12.9 / -3.7; deep +0.0 / +0.0; rotation 12-28 mpg -104.0 / -38.2.

**In**
- Peyton Watson (DEN -> CLE, age 24): 54 gp, 29.6 mpg, 14.6 ppg, 4.9 rpg, 2.1 apg, +/- +3.6 [star]
- Mario Hezonja (POR -> CLE, age 31): 0 gp, n/a mpg, n/a ppg, n/a rpg, n/a apg [none, no 2025-26 minutes]

**Out**
- Dennis Schroder (CLE -> CHA, age 33): 70 gp, 24.2 mpg, 10.8 ppg, 2.7 rpg, 4.9 apg, +/- -3.4 [starter, VET]
- Max Strus (CLE -> LAC, age 30): 12 gp, 24.0 mpg, 11.2 ppg, 5.4 rpg, 2.0 apg, +/- -0.2 [starter]
- Dean Wade (CLE -> PHI, age 29): 59 gp, 22.4 mpg, 5.8 ppg, 4.2 rpg, 1.5 apg, +/- +2.6 [starter]
- Keon Ellis (CLE -> BKN, age 26): 72 gp, 20.5 mpg, 6.7 ppg, 1.9 rpg, 1.0 apg, +/- -2.0 [starter]
- Larry Nance Jr. (CLE -> IND, age 33): 35 gp, 12.9 mpg, 3.7 ppg, 2.7 rpg, 1.0 apg, +/- -0.5 [bench, VET]
- Tacko Fall (CLE -> PHI, age 30): 0 gp, n/a mpg, n/a ppg, n/a rpg, n/a apg [none, no 2025-26 minutes]

### DAL

DAL loses more than it adds last-season production: 5 in, 7 out, net -51.8 mpg / -33.6 ppg. Biggest add: Santi Aldama (28 mpg, 14.0 ppg). Biggest loss: Anthony Davis (31 mpg, 20.4 ppg). Rotation (12-28 mpg) loses 10.9 mpg.

Net by tier (mpg / ppg): star -31.2 / -20.4; starter -16.2 / -13.9; bench +5.3 / +4.1; deep -9.7 / -3.4; rotation 12-28 mpg -10.9 / -9.8.

**In**
- Santi Aldama (MEM -> DAL, age 25): 43 gp, 27.8 mpg, 14.0 ppg, 6.7 rpg, 2.9 apg, +/- -1.1 [starter]
- Zaccharie Risacher (ATL -> DAL, age 21): 67 gp, 22.4 mpg, 9.6 ppg, 3.8 rpg, 1.1 apg, +/- -0.2 [starter]
- Walter Clayton Jr. (MEM -> DAL, age 23): 69 gp, 20.4 mpg, 7.8 ppg, 2.0 rpg, 4.0 apg, +/- -4.1 [starter]
- Jett Howard (ORL -> DAL, age 23): 55 gp, 12.7 mpg, 5.5 ppg, 1.6 rpg, 0.8 apg, +/- -1.3 [bench]
- Marcus Sasser (DET -> DAL, age 26): 38 gp, 12.1 mpg, 5.2 ppg, 1.0 rpg, 2.0 apg, +/- +3.1 [bench]

**Out**
- Anthony Davis (DAL -> WAS, age 33): 20 gp, 31.2 mpg, 20.4 ppg, 11.1 rpg, 2.8 apg, +/- -2.5 [star, VET]
- Khris Middleton (DAL -> WAS, age 35): 63 gp, 22.8 mpg, 10.2 ppg, 3.7 rpg, 2.8 apg, +/- -5.3 [starter, VET]
- Brandon Williams (DAL -> GSW, age 26): 66 gp, 22.2 mpg, 13.0 ppg, 2.9 rpg, 3.9 apg, +/- -2.2 [starter]
- Klay Thompson (DAL -> MIA, age 36): 69 gp, 21.7 mpg, 11.7 ppg, 2.1 rpg, 1.4 apg, +/- -1.9 [starter, VET]
- Marvin Bagley III (DAL -> DEN, age 27): 60 gp, 20.1 mpg, 10.4 ppg, 6.1 rpg, 1.4 apg, +/- -3.7 [starter]
- Ryan Nembhard (DAL -> DEN, age 23): 60 gp, 19.5 mpg, 6.6 ppg, 2.2 rpg, 5.3 apg, +/- -0.4 [bench]
- AJ Johnson (DAL -> NOP, age 21): 47 gp, 9.7 mpg, 3.4 ppg, 1.2 rpg, 1.0 apg, +/- -1.1 [deep]

### DEN

DEN adds more than it loses last-season production: 4 in, 3 out, net +15.5 mpg / +11.3 ppg. Biggest add: DeMar DeRozan (31 mpg, 18.4 ppg). Biggest loss: Peyton Watson (30 mpg, 14.6 ppg). Rotation (12-28 mpg) gains 13.8 mpg. Veterans (32+) added: DeMar DeRozan.

Net by tier (mpg / ppg): star +1.7 / +3.8; starter -6.5 / -3.1; bench +20.3 / +10.6; deep +0.0 / +0.0; rotation 12-28 mpg +13.8 / +7.5.

**In**
- DeMar DeRozan (SAC -> DEN, age 37): 77 gp, 31.3 mpg, 18.4 ppg, 2.9 rpg, 4.1 apg, +/- -7.6 [star, VET]
- Marvin Bagley III (DAL -> DEN, age 27): 60 gp, 20.1 mpg, 10.4 ppg, 6.1 rpg, 1.4 apg, +/- -3.7 [starter]
- Ryan Nembhard (DAL -> DEN, age 23): 60 gp, 19.5 mpg, 6.6 ppg, 2.2 rpg, 5.3 apg, +/- -0.4 [bench]
- Cam Whitmore (WAS -> DEN, age 22): 21 gp, 16.9 mpg, 9.2 ppg, 2.8 rpg, 0.7 apg, +/- -7.2 [bench]

**Out**
- Peyton Watson (DEN -> CLE, age 24): 54 gp, 29.6 mpg, 14.6 ppg, 4.9 rpg, 2.1 apg, +/- +3.6 [star]
- Tim Hardaway Jr. (DEN -> MIA, age 34): 80 gp, 26.6 mpg, 13.5 ppg, 2.6 rpg, 1.4 apg, +/- +2.5 [starter, VET]
- Jalen Pickett (DEN -> LAC, age 26): 50 gp, 16.1 mpg, 5.2 ppg, 2.3 rpg, 2.3 apg, +/- +0.1 [bench]

### DET

DET adds more than it loses last-season production: 4 in, 4 out, net +11.1 mpg / +4.8 ppg. Biggest add: John Collins (27 mpg, 13.6 ppg). Biggest loss: Tobias Harris (28 mpg, 13.3 ppg). Rotation (12-28 mpg) gains 11.1 mpg. Veterans (32+) added: Taurean Prince.

Net by tier (mpg / ppg): star +0.0 / +0.0; starter +42.4 / +17.4; bench -31.3 / -12.6; deep +0.0 / +0.0; rotation 12-28 mpg +11.1 / +4.8.

**In**
- John Collins (LAC -> DET, age 29): 69 gp, 27.1 mpg, 13.6 ppg, 5.3 rpg, 1.0 apg, +/- -0.6 [starter]
- Taurean Prince (MIL -> DET, age 32): 26 gp, 23.6 mpg, 9.2 ppg, 3.1 rpg, 1.8 apg, +/- -2.0 [starter, VET]
- Isaiah Joe (OKC -> DET, age 27): 71 gp, 21.2 mpg, 11.1 ppg, 2.5 rpg, 1.3 apg, +/- +5.0 [starter]
- Elijah Harkless (UTA -> DET, age 26): 26 gp, 21.0 mpg, 6.8 ppg, 2.0 rpg, 2.9 apg, +/- -1.3 [starter]

**Out**
- Tobias Harris (DET -> SAS, age 34): 63 gp, 27.8 mpg, 13.3 ppg, 5.1 rpg, 2.5 apg, +/- +5.8 [starter, VET]
- Isaiah Stewart (DET -> MEM, age 25): 58 gp, 22.7 mpg, 10.0 ppg, 5.0 rpg, 1.1 apg, +/- +0.7 [starter]
- Caris LeVert (DET -> MIL, age 32): 60 gp, 19.2 mpg, 7.4 ppg, 1.9 rpg, 2.6 apg, +/- -0.1 [bench, VET]
- Marcus Sasser (DET -> DAL, age 26): 38 gp, 12.1 mpg, 5.2 ppg, 1.0 rpg, 2.0 apg, +/- +3.1 [bench]

### GSW

GSW loses more than it adds last-season production: 2 in, 2 out, net -13.7 mpg / -1.9 ppg. Biggest add: Brandon Williams (22 mpg, 13.0 ppg). Biggest loss: Pat Spencer (19 mpg, 7.2 ppg). Rotation (12-28 mpg) loses 13.7 mpg. Veterans (32+) added: Georges Niang.

Net by tier (mpg / ppg): star +0.0 / +0.0; starter +22.2 / +13.0; bench -35.9 / -14.9; deep +0.0 / +0.0; rotation 12-28 mpg -13.7 / -1.9.

**In**
- Brandon Williams (DAL -> GSW, age 26): 66 gp, 22.2 mpg, 13.0 ppg, 2.9 rpg, 3.9 apg, +/- -2.2 [starter]
- Georges Niang (UTA -> GSW, age 33): 0 gp, n/a mpg, n/a ppg, n/a rpg, n/a apg [none, VET, no 2025-26 minutes]

**Out**
- Pat Spencer (GSW -> PHX, age 30): 66 gp, 18.6 mpg, 7.2 ppg, 2.4 rpg, 3.5 apg, +/- -1.4 [bench]
- Quinten Post (GSW -> MEM, age 26): 67 gp, 17.3 mpg, 7.7 ppg, 4.0 rpg, 1.4 apg, +/- +0.9 [bench]

### HOU

HOU adds more than it loses last-season production: 4 in, 2 out, net +39.6 mpg / +19.6 ppg. Biggest add: Marcus Smart (28 mpg, 9.3 ppg). Biggest loss: Josh Okogie (17 mpg, 4.5 ppg). Veterans (32+) added: Bogdan Bogdanovic, Marcus Smart.

Net by tier (mpg / ppg): star +28.5 / +9.3; starter +0.0 / +0.0; bench +2.2 / +7.4; deep +8.9 / +2.9; rotation 12-28 mpg +2.2 / +7.4.

**In**
- Marcus Smart (LAL -> HOU, age 32): 62 gp, 28.5 mpg, 9.3 ppg, 2.8 rpg, 3.0 apg, +/- +4.1 [star, VET]
- Bogdan Bogdanovic (LAC -> HOU, age 34): 23 gp, 19.7 mpg, 7.4 ppg, 2.6 rpg, 2.2 apg, +/- -1.3 [bench, VET]
- Oscar Tshiebwe (UTA -> HOU, age 26): 27 gp, 16.6 mpg, 7.8 ppg, 6.6 rpg, 1.2 apg, +/- -2.9 [bench]
- Julian Phillips (MIN -> HOU, age 22): 48 gp, 8.9 mpg, 2.9 ppg, 1.0 rpg, 0.2 apg, +/- -0.5 [deep]

**Out**
- Josh Okogie (HOU -> UTA, age 28): 78 gp, 17.4 mpg, 4.5 ppg, 2.6 rpg, 0.9 apg, +/- +0.9 [bench]
- Dorian Finney-Smith (HOU -> ATL, age 33): 37 gp, 16.7 mpg, 3.3 ppg, 2.5 rpg, 1.0 apg, +/- +0.4 [bench, VET]

### IND

IND roughly breaks even last-season production: 2 in, 3 out, net -6.3 mpg / +0.5 ppg. Biggest add: Kelly Oubre Jr. (32 mpg, 14.1 ppg). Biggest loss: Micah Potter (19 mpg, 9.7 ppg). Rotation (12-28 mpg) loses 37.8 mpg. Veterans (32+) added: Larry Nance Jr..

Net by tier (mpg / ppg): star +31.5 / +14.1; starter +0.0 / +0.0; bench -37.8 / -13.6; deep +0.0 / +0.0; rotation 12-28 mpg -37.8 / -13.6.

**In**
- Kelly Oubre Jr. (PHI -> IND, age 30): 50 gp, 31.5 mpg, 14.1 ppg, 5.0 rpg, 1.6 apg, +/- -1.3 [star]
- Larry Nance Jr. (CLE -> IND, age 33): 35 gp, 12.9 mpg, 3.7 ppg, 2.7 rpg, 1.0 apg, +/- -0.5 [bench, VET]

**Out**
- Micah Potter (IND -> POR, age 28): 47 gp, 19.3 mpg, 9.7 ppg, 5.0 rpg, 1.5 apg, +/- -2.6 [bench]
- Kam Jones (IND -> MIL, age 24): 37 gp, 16.6 mpg, 4.4 ppg, 1.6 rpg, 3.2 apg, +/- -3.9 [bench]
- James Wiseman (IND -> NYK, age 25): 4 gp, 14.8 mpg, 3.2 ppg, 2.0 rpg, 0.8 apg, +/- -2.2 [bench, 4 gp only]

### LAC

LAC loses more than it adds last-season production: 5 in, 5 out, net +7.5 mpg / -11.0 ppg. Biggest add: Brandon Ingram (33 mpg, 20.9 ppg). Biggest loss: Kawhi Leonard (31 mpg, 27.2 ppg). Rotation (12-28 mpg) gains 7.3 mpg.

Net by tier (mpg / ppg): star +0.2 / -12.4; starter -3.1 / -2.4; bench +10.4 / +3.8; deep +0.0 / +0.0; rotation 12-28 mpg +7.3 / +1.4.

**In**
- Brandon Ingram (TOR -> LAC, age 29): 79 gp, 33.1 mpg, 20.9 ppg, 5.5 rpg, 3.6 apg, +/- +0.6 [star]
- Rui Hachimura (LAL -> LAC, age 28): 68 gp, 28.3 mpg, 11.5 ppg, 3.3 rpg, 0.8 apg, +/- +1.2 [star]
- Max Strus (CLE -> LAC, age 30): 12 gp, 24.0 mpg, 11.2 ppg, 5.4 rpg, 2.0 apg, +/- -0.2 [starter]
- Jalen Pickett (DEN -> LAC, age 26): 50 gp, 16.1 mpg, 5.2 ppg, 2.3 rpg, 2.3 apg, +/- +0.1 [bench]
- Gradey Dick (TOR -> LAC, age 22): 76 gp, 14.0 mpg, 6.0 ppg, 1.9 rpg, 0.7 apg, +/- +1.6 [bench]

**Out**
- Kawhi Leonard (LAC -> TOR, age 35): 68 gp, 31.2 mpg, 27.2 ppg, 6.2 rpg, 3.5 apg, +/- +4.7 [star, VET]
- Bennedict Mathurin (LAC -> NOP, age 24): 54 gp, 30.0 mpg, 17.6 ppg, 5.4 rpg, 2.4 apg, +/- -2.7 [star]
- John Collins (LAC -> DET, age 29): 69 gp, 27.1 mpg, 13.6 ppg, 5.3 rpg, 1.0 apg, +/- -0.6 [starter]
- Bogdan Bogdanovic (LAC -> HOU, age 34): 23 gp, 19.7 mpg, 7.4 ppg, 2.6 rpg, 2.2 apg, +/- -1.3 [bench, VET]
- Ben Simmons (LAC -> SAC, age 30): 0 gp, n/a mpg, n/a ppg, n/a rpg, n/a apg [none, no 2025-26 minutes]

### LAL

LAL adds more than it loses last-season production: 8 in, 7 out, net +10.4 mpg / +9.5 ppg. Biggest add: Walker Kessler (31 mpg, 14.4 ppg). Biggest loss: LeBron James (32 mpg, 20.2 ppg). Rotation (12-28 mpg) gains 47.7 mpg.

Net by tier (mpg / ppg): star -28.2 / -13.2; starter +19.6 / +15.9; bench +28.1 / +10.3; deep -9.1 / -3.5; rotation 12-28 mpg +47.7 / +26.2.

**In**
- Walker Kessler (UTA -> LAL, age 25): 5 gp, 31.0 mpg, 14.4 ppg, 10.8 rpg, 3.0 apg, +/- +2.8 [star, 5 gp only]
- Quentin Grimes (PHI -> LAL, age 26): 75 gp, 29.5 mpg, 13.4 ppg, 3.6 rpg, 3.3 apg, +/- +0.1 [star]
- Collin Sexton (CHI -> LAL, age 27): 68 gp, 23.7 mpg, 15.4 ppg, 2.3 rpg, 3.3 apg, +/- -1.7 [starter]
- Ziaire Williams (BKN -> LAL, age 25): 56 gp, 22.9 mpg, 10.2 ppg, 2.4 rpg, 1.1 apg, +/- -5.0 [starter]
- Sandro Mamukelashvili (TOR -> LAL, age 27): 80 gp, 21.9 mpg, 11.2 ppg, 4.9 rpg, 1.9 apg, +/- +2.3 [starter]
- Matisse Thybulle (POR -> LAL, age 29): 30 gp, 16.0 mpg, 5.8 ppg, 2.0 rpg, 0.9 apg, +/- +2.9 [bench]
- Jaden Hardy (WAS -> LAL, age 24): 57 gp, 15.8 mpg, 9.2 ppg, 1.5 rpg, 1.0 apg, +/- -1.5 [bench]
- Kevon Looney (NOP -> LAL, age 30): 21 gp, 14.6 mpg, 2.8 ppg, 5.6 rpg, 1.6 apg, +/- -3.0 [bench]

**Out**
- LeBron James (LAL -> PHI, age 41): 63 gp, 31.9 mpg, 20.2 ppg, 5.9 rpg, 6.9 apg, +/- +1.5 [star, VET]
- Marcus Smart (LAL -> HOU, age 32): 62 gp, 28.5 mpg, 9.3 ppg, 2.8 rpg, 3.0 apg, +/- +4.1 [star, VET]
- Rui Hachimura (LAL -> LAC, age 28): 68 gp, 28.3 mpg, 11.5 ppg, 3.3 rpg, 0.8 apg, +/- +1.2 [star]
- Deandre Ayton (LAL -> WAS, age 28): 72 gp, 27.3 mpg, 12.5 ppg, 8.0 rpg, 0.8 apg, +/- -0.1 [starter]
- Luke Kennard (LAL -> PHX, age 30): 78 gp, 21.6 mpg, 8.4 ppg, 2.3 rpg, 2.2 apg, +/- +0.5 [starter]
- Jaxson Hayes (LAL -> UTA, age 26): 66 gp, 18.3 mpg, 7.5 ppg, 4.1 rpg, 0.9 apg, +/- +1.1 [bench]
- Drew Timme (LAL -> PHX, age 26): 26 gp, 9.1 mpg, 3.5 ppg, 1.3 rpg, 0.9 apg, +/- -0.9 [deep]

### MEM

MEM loses more than it adds last-season production: 4 in, 5 out, net -21.4 mpg / -12.7 ppg. Biggest add: Jerami Grant (30 mpg, 18.6 ppg). Biggest loss: Ja Morant (28 mpg, 19.4 ppg). Rotation (12-28 mpg) loses 22.8 mpg. Veterans (32+) added: Jerami Grant.

Net by tier (mpg / ppg): star +1.4 / -0.8; starter -23.3 / -14.4; bench +0.5 / +2.5; deep +0.0 / +0.0; rotation 12-28 mpg -22.8 / -11.9.

**In**
- Jerami Grant (POR -> MEM, age 32): 57 gp, 29.8 mpg, 18.6 ppg, 3.5 rpg, 2.1 apg, +/- -0.1 [star, VET]
- Kris Murray (POR -> MEM, age 26): 57 gp, 23.5 mpg, 5.8 ppg, 3.6 rpg, 1.4 apg, +/- +0.8 [starter]
- Isaiah Stewart (DET -> MEM, age 25): 58 gp, 22.7 mpg, 10.0 ppg, 5.0 rpg, 1.1 apg, +/- +0.7 [starter]
- Quinten Post (GSW -> MEM, age 26): 67 gp, 17.3 mpg, 7.7 ppg, 4.0 rpg, 1.4 apg, +/- +0.9 [bench]

**Out**
- Ja Morant (MEM -> POR, age 27): 20 gp, 28.4 mpg, 19.4 ppg, 3.2 rpg, 8.1 apg, +/- -2.5 [star]
- Santi Aldama (MEM -> DAL, age 25): 43 gp, 27.8 mpg, 14.0 ppg, 6.7 rpg, 2.9 apg, +/- -1.1 [starter]
- Kentavious Caldwell-Pope (MEM -> PHI, age 33): 51 gp, 21.3 mpg, 8.4 ppg, 2.5 rpg, 2.7 apg, +/- -2.2 [starter, VET]
- Walter Clayton Jr. (MEM -> DAL, age 23): 69 gp, 20.4 mpg, 7.8 ppg, 2.0 rpg, 4.0 apg, +/- -4.1 [starter]
- Rayan Rupert (MEM -> PHI, age 22): 64 gp, 16.8 mpg, 5.2 ppg, 2.9 rpg, 1.1 apg, +/- -5.0 [bench]

### MIA

MIA loses more than it adds last-season production: 5 in, 5 out, net -12.2 mpg / -1.9 ppg. Biggest add: Giannis Antetokounmpo (29 mpg, 27.6 ppg). Biggest loss: Tyler Herro (31 mpg, 20.5 ppg). Rotation (12-28 mpg) gains 47.3 mpg. Veterans (32+) added: Klay Thompson, Tim Hardaway Jr..

Net by tier (mpg / ppg): star -59.5 / -29.4; starter +50.2 / +27.8; bench -2.9 / -0.3; deep +0.0 / +0.0; rotation 12-28 mpg +47.3 / +27.5.

**In**
- Giannis Antetokounmpo (MIL -> MIA, age 31): 36 gp, 28.9 mpg, 27.6 ppg, 9.8 rpg, 5.4 apg, +/- +2.7 [star]
- Tim Hardaway Jr. (DEN -> MIA, age 34): 80 gp, 26.6 mpg, 13.5 ppg, 2.6 rpg, 1.4 apg, +/- +2.5 [starter, VET]
- Bobby Portis (MIL -> MIA, age 31): 67 gp, 24.1 mpg, 13.7 ppg, 6.4 rpg, 1.6 apg, +/- -4.0 [starter]
- Klay Thompson (DAL -> MIA, age 36): 69 gp, 21.7 mpg, 11.7 ppg, 2.1 rpg, 1.4 apg, +/- -1.9 [starter, VET]
- Nick Richards (CHI -> MIA, age 28): 47 gp, 15.0 mpg, 5.9 ppg, 5.2 rpg, 0.3 apg, +/- -2.9 [bench]

**Out**
- Tyler Herro (MIA -> MIL, age 26): 33 gp, 31.2 mpg, 20.5 ppg, 4.8 rpg, 4.1 apg, +/- -1.2 [star]
- Norman Powell (MIA -> CHI, age 33): 60 gp, 28.9 mpg, 21.1 ppg, 3.4 rpg, 2.4 apg, +/- +0.5 [star, VET]
- Jaime Jaquez Jr. (MIA -> MIL, age 25): 75 gp, 28.3 mpg, 15.4 ppg, 5.0 rpg, 4.7 apg, +/- +1.9 [star]
- Kel'el Ware (MIA -> MIL, age 22): 77 gp, 22.2 mpg, 11.1 ppg, 9.0 rpg, 0.7 apg, +/- -0.9 [starter]
- Kasparas Jakucionis (MIA -> MIL, age 20): 53 gp, 17.9 mpg, 6.2 ppg, 2.6 rpg, 2.6 apg, +/- +1.4 [bench]

### MIL

MIL adds more than it loses last-season production: 6 in, 3 out, net +58.8 mpg / +14.5 ppg. Biggest add: Tyler Herro (31 mpg, 20.5 ppg). Biggest loss: Giannis Antetokounmpo (29 mpg, 27.6 ppg). Rotation (12-28 mpg) gains 28.2 mpg. Veterans (32+) added: Caris LeVert.

Net by tier (mpg / ppg): star +30.6 / +8.3; starter -25.5 / -11.8; bench +53.7 / +18.0; deep +0.0 / +0.0; rotation 12-28 mpg +28.2 / +6.2.

**In**
- Tyler Herro (MIA -> MIL, age 26): 33 gp, 31.2 mpg, 20.5 ppg, 4.8 rpg, 4.1 apg, +/- -1.2 [star]
- Jaime Jaquez Jr. (MIA -> MIL, age 25): 75 gp, 28.3 mpg, 15.4 ppg, 5.0 rpg, 4.7 apg, +/- +1.9 [star]
- Kel'el Ware (MIA -> MIL, age 22): 77 gp, 22.2 mpg, 11.1 ppg, 9.0 rpg, 0.7 apg, +/- -0.9 [starter]
- Caris LeVert (DET -> MIL, age 32): 60 gp, 19.2 mpg, 7.4 ppg, 1.9 rpg, 2.6 apg, +/- -0.1 [bench, VET]
- Kasparas Jakucionis (MIA -> MIL, age 20): 53 gp, 17.9 mpg, 6.2 ppg, 2.6 rpg, 2.6 apg, +/- +1.4 [bench]
- Kam Jones (IND -> MIL, age 24): 37 gp, 16.6 mpg, 4.4 ppg, 1.6 rpg, 3.2 apg, +/- -3.9 [bench]

**Out**
- Giannis Antetokounmpo (MIL -> MIA, age 31): 36 gp, 28.9 mpg, 27.6 ppg, 9.8 rpg, 5.4 apg, +/- +2.7 [star]
- Bobby Portis (MIL -> MIA, age 31): 67 gp, 24.1 mpg, 13.7 ppg, 6.4 rpg, 1.6 apg, +/- -4.0 [starter]
- Taurean Prince (MIL -> DET, age 32): 26 gp, 23.6 mpg, 9.2 ppg, 3.1 rpg, 1.8 apg, +/- -2.0 [starter, VET]

### MIN

MIN loses more than it adds last-season production: 4 in, 5 out, net -31.0 mpg / -7.2 ppg. Biggest add: LaMelo Ball (28 mpg, 20.1 ppg). Biggest loss: Julius Randle (33 mpg, 21.1 ppg). Rotation (12-28 mpg) loses 17.0 mpg.

Net by tier (mpg / ppg): star -5.1 / -1.0; starter +21.3 / +7.4; bench -38.3 / -10.7; deep -8.9 / -2.9; rotation 12-28 mpg -17.0 / -3.3.

**In**
- LaMelo Ball (CHA -> MIN, age 25): 72 gp, 28.0 mpg, 20.1 ppg, 4.8 rpg, 7.1 apg, +/- +5.8 [star]
- Cody Williams (UTA -> MIN, age 21): 67 gp, 24.3 mpg, 8.8 ppg, 3.0 rpg, 2.0 apg, +/- -5.9 [starter]
- Jonathan Kuminga (ATL -> MIN, age 24): 36 gp, 23.1 mpg, 12.2 ppg, 5.6 rpg, 2.3 apg, +/- +2.0 [starter]
- Trey Lyles (SAC -> MIN, age 30): 0 gp, n/a mpg, n/a ppg, n/a rpg, n/a apg [none, no 2025-26 minutes]

**Out**
- Julius Randle (MIN -> BKN, age 31): 79 gp, 33.1 mpg, 21.1 ppg, 6.7 rpg, 5.0 apg, +/- +2.9 [star]
- Naz Reid (MIN -> CHA, age 27): 77 gp, 26.1 mpg, 13.6 ppg, 6.2 rpg, 2.2 apg, +/- +2.2 [starter]
- Kyle Anderson (MIN -> TOR, age 33): 43 gp, 19.9 mpg, 6.2 ppg, 3.5 rpg, 2.9 apg, +/- -1.9 [bench, VET]
- Mike Conley (MIN -> BOS, age 39): 54 gp, 18.4 mpg, 4.5 ppg, 1.7 rpg, 2.9 apg, +/- +0.3 [bench, VET]
- Julian Phillips (MIN -> HOU, age 22): 48 gp, 8.9 mpg, 2.9 ppg, 1.0 rpg, 0.2 apg, +/- -0.5 [deep]

### NOP

NOP adds more than it loses last-season production: 4 in, 3 out, net +46.5 mpg / +26.2 ppg. Biggest add: Julian Reese (31 mpg, 11.8 ppg). Biggest loss: Kevon Looney (15 mpg, 2.8 ppg). Rotation (12-28 mpg) loses 24.1 mpg.

Net by tier (mpg / ppg): star +60.9 / +29.4; starter +0.0 / +0.0; bench -24.1 / -6.6; deep +9.7 / +3.4; rotation 12-28 mpg -24.1 / -6.6.

**In**
- Julian Reese (WAS -> NOP, age 23): 13 gp, 30.9 mpg, 11.8 ppg, 10.5 rpg, 1.8 apg, +/- -8.6 [star]
- Bennedict Mathurin (LAC -> NOP, age 24): 54 gp, 30.0 mpg, 17.6 ppg, 5.4 rpg, 2.4 apg, +/- -2.7 [star]
- Trendon Watford (PHI -> NOP, age 25): 53 gp, 16.3 mpg, 6.5 ppg, 3.3 rpg, 2.5 apg, +/- -2.9 [bench]
- AJ Johnson (DAL -> NOP, age 21): 47 gp, 9.7 mpg, 3.4 ppg, 1.2 rpg, 1.0 apg, +/- -1.1 [deep]

**Out**
- Kevon Looney (NOP -> LAL, age 30): 21 gp, 14.6 mpg, 2.8 ppg, 5.6 rpg, 1.6 apg, +/- -3.0 [bench]
- Jordan Hawkins (NOP -> CHI, age 24): 51 gp, 13.5 mpg, 5.1 ppg, 1.7 rpg, 0.8 apg, +/- -2.6 [bench]
- Trey Alexander (NOP -> UTA, age 23): 9 gp, 12.3 mpg, 5.2 ppg, 1.2 rpg, 1.0 apg, +/- -2.0 [bench, 9 gp only]

### NYK

NYK adds more than it loses last-season production: 2 in, 2 out, net +5.5 mpg / +1.8 ppg. Biggest add: Andre Drummond (20 mpg, 6.4 ppg). Biggest loss: Mitchell Robinson (20 mpg, 5.6 ppg). Rotation (12-28 mpg) gains 14.8 mpg. Veterans (32+) added: Andre Drummond.

Net by tier (mpg / ppg): star +0.0 / +0.0; starter +0.0 / +0.0; bench +14.8 / +4.0; deep -9.3 / -2.2; rotation 12-28 mpg +14.8 / +4.0.

**In**
- Andre Drummond (PHI -> NYK, age 33): 63 gp, 19.6 mpg, 6.4 ppg, 8.4 rpg, 1.3 apg, +/- -1.4 [bench, VET]
- James Wiseman (IND -> NYK, age 25): 4 gp, 14.8 mpg, 3.2 ppg, 2.0 rpg, 0.8 apg, +/- -2.2 [bench, 4 gp only]

**Out**
- Mitchell Robinson (NYK -> BOS, age 28): 61 gp, 19.6 mpg, 5.6 ppg, 8.9 rpg, 0.9 apg, +/- +2.7 [bench]
- Ariel Hukporti (NYK -> PHI, age 24): 54 gp, 9.3 mpg, 2.2 ppg, 2.9 rpg, 0.5 apg, +/- +0.7 [deep]

### OKC

OKC loses more than it adds last-season production: 0 in, 4 out, net -81.5 mpg / -34.6 ppg. Biggest loss: Luguentz Dort (27 mpg, 8.3 ppg). Rotation (12-28 mpg) loses 69.8 mpg.

Net by tier (mpg / ppg): star +0.0 / +0.0; starter -69.8 / -28.8; bench +0.0 / +0.0; deep -11.7 / -5.8; rotation 12-28 mpg -69.8 / -28.8.

**In**
- none

**Out**
- Luguentz Dort (OKC -> ATL, age 27): 69 gp, 26.8 mpg, 8.3 ppg, 3.6 rpg, 1.2 apg, +/- +4.3 [starter]
- Aaron Wiggins (OKC -> ATL, age 27): 65 gp, 21.8 mpg, 9.4 ppg, 3.1 rpg, 1.7 apg, +/- +2.7 [starter]
- Isaiah Joe (OKC -> DET, age 27): 71 gp, 21.2 mpg, 11.1 ppg, 2.5 rpg, 1.3 apg, +/- +5.0 [starter]
- Branden Carlson (OKC -> POR, age 27): 42 gp, 11.7 mpg, 5.8 ppg, 3.0 rpg, 0.7 apg, +/- -0.6 [deep]

### ORL

ORL adds more than it loses last-season production: 1 in, 2 out, net +3.8 mpg / +2.7 ppg. Biggest add: Nikola Vucevic (28 mpg, 15.1 ppg). Biggest loss: Jett Howard (13 mpg, 5.5 ppg). Rotation (12-28 mpg) loses 12.7 mpg. Veterans (32+) added: Nikola Vucevic.

Net by tier (mpg / ppg): star +28.4 / +15.1; starter +0.0 / +0.0; bench -12.7 / -5.5; deep -11.9 / -6.9; rotation 12-28 mpg -12.7 / -5.5.

**In**
- Nikola Vucevic (BOS -> ORL, age 35): 64 gp, 28.4 mpg, 15.1 ppg, 8.4 rpg, 3.3 apg, +/- -0.7 [star, VET]

**Out**
- Jett Howard (ORL -> DAL, age 23): 55 gp, 12.7 mpg, 5.5 ppg, 1.6 rpg, 0.8 apg, +/- -1.3 [bench]
- Moritz Wagner (ORL -> BKN, age 29): 36 gp, 11.9 mpg, 6.9 ppg, 3.2 rpg, 0.8 apg, +/- -2.4 [deep]

### PHI

PHI adds more than it loses last-season production: 9 in, 5 out, net +53.2 mpg / +36.5 ppg. Biggest add: Jaylen Brown (33 mpg, 27.7 ppg). Biggest loss: Kelly Oubre Jr. (32 mpg, 14.1 ppg). Rotation (12-28 mpg) gains 70.2 mpg. Veterans (32+) added: Kentavious Caldwell-Pope, LeBron James.

Net by tier (mpg / ppg): star -26.3 / +3.1; starter +89.3 / +38.9; bench -19.1 / -7.7; deep +9.3 / +2.2; rotation 12-28 mpg +70.2 / +31.2.

**In**
- Jaylen Brown (BOS -> PHI, age 29): 74 gp, 33.4 mpg, 27.7 ppg, 6.7 rpg, 4.9 apg, +/- +4.0 [star]
- LeBron James (LAL -> PHI, age 41): 63 gp, 31.9 mpg, 20.2 ppg, 5.9 rpg, 6.9 apg, +/- +1.5 [star, VET]
- Anfernee Simons (CHI -> PHI, age 27): 55 gp, 24.9 mpg, 14.3 ppg, 2.5 rpg, 2.4 apg, +/- +1.8 [starter]
- Dean Wade (CLE -> PHI, age 29): 59 gp, 22.4 mpg, 5.8 ppg, 4.2 rpg, 1.5 apg, +/- +2.6 [starter]
- Kentavious Caldwell-Pope (MEM -> PHI, age 33): 51 gp, 21.3 mpg, 8.4 ppg, 2.5 rpg, 2.7 apg, +/- -2.2 [starter, VET]
- Caleb Love (POR -> PHI, age 25): 49 gp, 20.7 mpg, 10.4 ppg, 2.3 rpg, 2.5 apg, +/- -2.6 [starter]
- Rayan Rupert (MEM -> PHI, age 22): 64 gp, 16.8 mpg, 5.2 ppg, 2.9 rpg, 1.1 apg, +/- -5.0 [bench]
- Ariel Hukporti (NYK -> PHI, age 24): 54 gp, 9.3 mpg, 2.2 ppg, 2.9 rpg, 0.5 apg, +/- +0.7 [deep]
- Tacko Fall (CLE -> PHI, age 30): 0 gp, n/a mpg, n/a ppg, n/a rpg, n/a apg [none, no 2025-26 minutes]

**Out**
- Kelly Oubre Jr. (PHI -> IND, age 30): 50 gp, 31.5 mpg, 14.1 ppg, 5.0 rpg, 1.6 apg, +/- -1.3 [star]
- Paul George (PHI -> BOS, age 36): 37 gp, 30.6 mpg, 17.3 ppg, 5.3 rpg, 3.6 apg, +/- +2.4 [star, VET]
- Quentin Grimes (PHI -> LAL, age 26): 75 gp, 29.5 mpg, 13.4 ppg, 3.6 rpg, 3.3 apg, +/- +0.1 [star]
- Andre Drummond (PHI -> NYK, age 33): 63 gp, 19.6 mpg, 6.4 ppg, 8.4 rpg, 1.3 apg, +/- -1.4 [bench, VET]
- Trendon Watford (PHI -> NOP, age 25): 53 gp, 16.3 mpg, 6.5 ppg, 3.3 rpg, 2.5 apg, +/- -2.9 [bench]

### PHX

PHX adds more than it loses last-season production: 5 in, 2 out, net +31.1 mpg / +12.8 ppg. Biggest add: Miles Bridges (31 mpg, 17.1 ppg). Biggest loss: Grayson Allen (29 mpg, 16.5 ppg). Rotation (12-28 mpg) gains 40.2 mpg.

Net by tier (mpg / ppg): star -26.2 / -9.2; starter +21.6 / +8.4; bench +18.6 / +7.2; deep +17.1 / +6.4; rotation 12-28 mpg +40.2 / +15.6.

**In**
- Miles Bridges (CHA -> PHX, age 28): 77 gp, 31.0 mpg, 17.1 ppg, 5.8 rpg, 3.2 apg, +/- +1.9 [star]
- Luke Kennard (LAL -> PHX, age 30): 78 gp, 21.6 mpg, 8.4 ppg, 2.3 rpg, 2.2 apg, +/- +0.5 [starter]
- Pat Spencer (GSW -> PHX, age 30): 66 gp, 18.6 mpg, 7.2 ppg, 2.4 rpg, 3.5 apg, +/- -1.4 [bench]
- Drew Timme (LAL -> PHX, age 26): 26 gp, 9.1 mpg, 3.5 ppg, 1.3 rpg, 0.9 apg, +/- -0.9 [deep]
- Duop Reath (POR -> PHX, age 30): 32 gp, 8.0 mpg, 2.9 ppg, 1.2 rpg, 0.3 apg, +/- -1.2 [deep]

**Out**
- Grayson Allen (PHX -> CHA, age 31): 51 gp, 28.8 mpg, 16.5 ppg, 3.0 rpg, 3.8 apg, +/- +0.4 [star]
- Royce O'Neale (PHX -> CHA, age 33): 78 gp, 28.4 mpg, 9.8 ppg, 4.8 rpg, 2.7 apg, +/- -1.4 [star, VET]

### POR

POR loses more than it adds last-season production: 4 in, 6 out, net -30.2 mpg / -5.6 ppg. Biggest add: Ja Morant (28 mpg, 19.4 ppg). Biggest loss: Jerami Grant (30 mpg, 18.6 ppg). Rotation (12-28 mpg) loses 40.9 mpg.

Net by tier (mpg / ppg): star -1.4 / +0.8; starter -44.2 / -16.2; bench +3.3 / +3.9; deep +12.1 / +5.9; rotation 12-28 mpg -40.9 / -12.3.

**In**
- Ja Morant (MEM -> POR, age 27): 20 gp, 28.4 mpg, 19.4 ppg, 3.2 rpg, 8.1 apg, +/- -2.5 [star]
- Micah Potter (IND -> POR, age 28): 47 gp, 19.3 mpg, 9.7 ppg, 5.0 rpg, 1.5 apg, +/- -2.6 [bench]
- Branden Carlson (OKC -> POR, age 27): 42 gp, 11.7 mpg, 5.8 ppg, 3.0 rpg, 0.7 apg, +/- -0.6 [deep]
- John Tonje (BOS -> POR, age 25): 5 gp, 8.4 mpg, 3.0 ppg, 1.2 rpg, 0.4 apg, +/- -1.2 [deep, 5 gp only]

**Out**
- Jerami Grant (POR -> MEM, age 32): 57 gp, 29.8 mpg, 18.6 ppg, 3.5 rpg, 2.1 apg, +/- -0.1 [star, VET]
- Kris Murray (POR -> MEM, age 26): 57 gp, 23.5 mpg, 5.8 ppg, 3.6 rpg, 1.4 apg, +/- +0.8 [starter]
- Caleb Love (POR -> PHI, age 25): 49 gp, 20.7 mpg, 10.4 ppg, 2.3 rpg, 2.5 apg, +/- -2.6 [starter]
- Matisse Thybulle (POR -> LAL, age 29): 30 gp, 16.0 mpg, 5.8 ppg, 2.0 rpg, 0.9 apg, +/- +2.9 [bench]
- Duop Reath (POR -> PHX, age 30): 32 gp, 8.0 mpg, 2.9 ppg, 1.2 rpg, 0.3 apg, +/- -1.2 [deep]
- Mario Hezonja (POR -> CLE, age 31): 0 gp, n/a mpg, n/a ppg, n/a rpg, n/a apg [none, no 2025-26 minutes]

### SAC

SAC loses more than it adds last-season production: 3 in, 3 out, net -14.3 mpg / -18.6 ppg. Biggest add: Leaky Black (29 mpg, 7.1 ppg). Biggest loss: DeMar DeRozan (31 mpg, 18.4 ppg). Rotation (12-28 mpg) loses 18.4 mpg.

Net by tier (mpg / ppg): star -2.5 / -11.3; starter +0.0 / +0.0; bench -18.4 / -8.9; deep +6.6 / +1.6; rotation 12-28 mpg -18.4 / -8.9.

**In**
- Leaky Black (WAS -> SAC, age 27): 15 gp, 28.8 mpg, 7.1 ppg, 5.0 rpg, 1.5 apg, +/- -6.8 [star]
- Jonathan Mogbo (TOR -> SAC, age 24): 38 gp, 6.6 mpg, 1.6 ppg, 1.8 rpg, 0.7 apg, +/- -1.3 [deep]
- Ben Simmons (LAC -> SAC, age 30): 0 gp, n/a mpg, n/a ppg, n/a rpg, n/a apg [none, no 2025-26 minutes]

**Out**
- DeMar DeRozan (SAC -> DEN, age 37): 77 gp, 31.3 mpg, 18.4 ppg, 2.9 rpg, 4.1 apg, +/- -7.6 [star, VET]
- Devin Carter (SAC -> BOS, age 24): 38 gp, 18.4 mpg, 8.9 ppg, 3.3 rpg, 2.7 apg, +/- -3.0 [bench]
- Trey Lyles (SAC -> MIN, age 30): 0 gp, n/a mpg, n/a ppg, n/a rpg, n/a apg [none, no 2025-26 minutes]

### SAS

SAS adds more than it loses last-season production: 1 in, 1 out, net +24.1 mpg / +11.7 ppg. Biggest add: Tobias Harris (28 mpg, 13.3 ppg). Biggest loss: Harrison Ingram (4 mpg, 1.6 ppg). Rotation (12-28 mpg) gains 27.8 mpg. Veterans (32+) added: Tobias Harris.

Net by tier (mpg / ppg): star +0.0 / +0.0; starter +27.8 / +13.3; bench +0.0 / +0.0; deep -3.7 / -1.6; rotation 12-28 mpg +27.8 / +13.3.

**In**
- Tobias Harris (DET -> SAS, age 34): 63 gp, 27.8 mpg, 13.3 ppg, 5.1 rpg, 2.5 apg, +/- +5.8 [starter, VET]

**Out**
- Harrison Ingram (SAS -> UTA, age 23): 7 gp, 3.7 mpg, 1.6 ppg, 0.4 rpg, 0.1 apg, +/- -0.9 [deep, 7 gp only]

### TOR

TOR adds more than it loses last-season production: 3 in, 4 out, net -0.6 mpg / +2.0 ppg. Biggest add: Kawhi Leonard (31 mpg, 27.2 ppg). Biggest loss: Brandon Ingram (33 mpg, 20.9 ppg). Rotation (12-28 mpg) gains 7.9 mpg. Veterans (32+) added: Kawhi Leonard, Kyle Anderson.

Net by tier (mpg / ppg): star -1.9 / +6.3; starter +2.0 / -2.9; bench +5.9 / +0.2; deep -6.6 / -1.6; rotation 12-28 mpg +7.9 / -2.7.

**In**
- Kawhi Leonard (LAC -> TOR, age 35): 68 gp, 31.2 mpg, 27.2 ppg, 6.2 rpg, 3.5 apg, +/- +4.7 [star, VET]
- Malachi Smith (BKN -> TOR, age 26): 15 gp, 23.9 mpg, 8.3 ppg, 3.4 rpg, 3.3 apg, +/- -6.0 [starter]
- Kyle Anderson (MIN -> TOR, age 33): 43 gp, 19.9 mpg, 6.2 ppg, 3.5 rpg, 2.9 apg, +/- -1.9 [bench, VET]

**Out**
- Brandon Ingram (TOR -> LAC, age 29): 79 gp, 33.1 mpg, 20.9 ppg, 5.5 rpg, 3.6 apg, +/- +0.6 [star]
- Sandro Mamukelashvili (TOR -> LAL, age 27): 80 gp, 21.9 mpg, 11.2 ppg, 4.9 rpg, 1.9 apg, +/- +2.3 [starter]
- Gradey Dick (TOR -> LAC, age 22): 76 gp, 14.0 mpg, 6.0 ppg, 1.9 rpg, 0.7 apg, +/- +1.6 [bench]
- Jonathan Mogbo (TOR -> SAC, age 24): 38 gp, 6.6 mpg, 1.6 ppg, 1.8 rpg, 0.7 apg, +/- -1.3 [deep]

### UTA

UTA loses more than it adds last-season production: 6 in, 5 out, net -14.2 mpg / -10.7 ppg. Biggest add: Jaxson Hayes (18 mpg, 7.5 ppg). Biggest loss: Walker Kessler (31 mpg, 14.4 ppg).

Net by tier (mpg / ppg): star -31.0 / -14.4; starter -45.3 / -15.6; bench +47.1 / +13.7; deep +15.0 / +5.6; rotation 12-28 mpg +1.8 / -1.9.

**In**
- Jaxson Hayes (LAL -> UTA, age 26): 66 gp, 18.3 mpg, 7.5 ppg, 4.1 rpg, 0.9 apg, +/- +1.1 [bench]
- Josh Okogie (HOU -> UTA, age 28): 78 gp, 17.4 mpg, 4.5 ppg, 2.6 rpg, 0.9 apg, +/- +0.9 [bench]
- Josh Green (CHA -> UTA, age 25): 58 gp, 15.7 mpg, 4.3 ppg, 1.8 rpg, 0.8 apg, +/- +3.6 [bench]
- Trey Alexander (NOP -> UTA, age 23): 9 gp, 12.3 mpg, 5.2 ppg, 1.2 rpg, 1.0 apg, +/- -2.0 [bench, 9 gp only]
- Tony Bradley (ATL -> UTA, age 28): 40 gp, 11.3 mpg, 4.0 ppg, 2.9 rpg, 0.6 apg, +/- -3.5 [deep]
- Harrison Ingram (SAS -> UTA, age 23): 7 gp, 3.7 mpg, 1.6 ppg, 0.4 rpg, 0.1 apg, +/- -0.9 [deep, 7 gp only]

**Out**
- Walker Kessler (UTA -> LAL, age 25): 5 gp, 31.0 mpg, 14.4 ppg, 10.8 rpg, 3.0 apg, +/- +2.8 [star, 5 gp only]
- Cody Williams (UTA -> MIN, age 21): 67 gp, 24.3 mpg, 8.8 ppg, 3.0 rpg, 2.0 apg, +/- -5.9 [starter]
- Elijah Harkless (UTA -> DET, age 26): 26 gp, 21.0 mpg, 6.8 ppg, 2.0 rpg, 2.9 apg, +/- -1.3 [starter]
- Oscar Tshiebwe (UTA -> HOU, age 26): 27 gp, 16.6 mpg, 7.8 ppg, 6.6 rpg, 1.2 apg, +/- -2.9 [bench]
- Georges Niang (UTA -> GSW, age 33): 0 gp, n/a mpg, n/a ppg, n/a rpg, n/a apg [none, VET, no 2025-26 minutes]

### WAS

WAS adds more than it loses last-season production: 5 in, 4 out, net +1.5 mpg / +11.3 ppg. Biggest add: Anthony Davis (31 mpg, 20.4 ppg). Biggest loss: Julian Reese (31 mpg, 11.8 ppg). Rotation (12-28 mpg) gains 30.0 mpg. Veterans (32+) added: Anthony Davis, Khris Middleton.

Net by tier (mpg / ppg): star -28.5 / +1.5; starter +50.1 / +22.7; bench -20.1 / -12.9; deep +0.0 / +0.0; rotation 12-28 mpg +30.0 / +9.8.

**In**
- Anthony Davis (DAL -> WAS, age 33): 20 gp, 31.2 mpg, 20.4 ppg, 11.1 rpg, 2.8 apg, +/- -2.5 [star, VET]
- Deandre Ayton (LAL -> WAS, age 28): 72 gp, 27.3 mpg, 12.5 ppg, 8.0 rpg, 0.8 apg, +/- -0.1 [starter]
- Khris Middleton (DAL -> WAS, age 35): 63 gp, 22.8 mpg, 10.2 ppg, 3.7 rpg, 2.8 apg, +/- -5.3 [starter, VET]
- Tre Mann (CHA -> WAS, age 25): 53 gp, 12.6 mpg, 5.5 ppg, 1.7 rpg, 1.6 apg, +/- -2.9 [bench]
- Reece Beekman (BKN -> WAS, age 25): 0 gp, n/a mpg, n/a ppg, n/a rpg, n/a apg [none, no 2025-26 minutes]

**Out**
- Julian Reese (WAS -> NOP, age 23): 13 gp, 30.9 mpg, 11.8 ppg, 10.5 rpg, 1.8 apg, +/- -8.6 [star]
- Leaky Black (WAS -> SAC, age 27): 15 gp, 28.8 mpg, 7.1 ppg, 5.0 rpg, 1.5 apg, +/- -6.8 [star]
- Cam Whitmore (WAS -> DEN, age 22): 21 gp, 16.9 mpg, 9.2 ppg, 2.8 rpg, 0.7 apg, +/- -7.2 [bench]
- Jaden Hardy (WAS -> LAL, age 24): 57 gp, 15.8 mpg, 9.2 ppg, 1.5 rpg, 1.0 apg, +/- -1.5 [bench]

