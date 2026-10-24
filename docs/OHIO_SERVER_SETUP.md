# Ohio server setup (research log #27, read-only trial)

You do the steps that involve your accounts, payment and secrets. Claude does everything after step 6. Never paste
a key file's contents, or the Kalshi key ID, into the chat.

## 1. Create the AWS account

1. Go to https://aws.amazon.com and choose **Create an AWS Account**. It needs an email, a card and a phone check.
2. If it asks you to choose a plan and offers a free plan with credits, take it. The credits should cover this trial.
3. Sign in to the **AWS Management Console**. Turn on MFA for the account (account menu, top right, then
   **Security credentials**).

## 2. Set a spending alarm (do this first)

**Billing and Cost Management → Budgets → Create budget → Monthly cost budget → $20 → your email.** You'll be
emailed if spending heads past $20 in a month.

## 3. Switch to the Ohio region

Use the region menu at the top right of the console. Choose **United States (Ohio) us-east-2**.

## 4. Launch the server

**EC2 → Instances → Launch instances**:

| Setting | Value |
|---|---|
| Name | `kalshi-ohio` |
| Image (AMI) | **Ubuntu Server 24.04 LTS**, architecture **64-bit (Arm)** |
| Instance type | **t4g.small** (about $12/month, billed by the hour) |
| Key pair | **Create new key pair**: name `kalshi-ohio`, type **ED25519**, format **.pem**. It downloads `kalshi-ohio.pem`. |
| Network settings | **Allow SSH traffic from: My IP**. Nothing else open. |
| Storage | 16 GiB gp3 |

Click **Launch instance**. Then, under **View instances**, select the server and copy its **Public IPv4 address**.

Move the downloaded `kalshi-ohio.pem` to `%USERPROFILE%\.ssh\`, creating the folder if it doesn't exist.

## 5. Make a separate Kalshi API key for the server

On kalshi.com, go to **Account settings → API keys → Create key**. Name it `ohio-server`.

- Save the **private key** file as `%USERPROFILE%\.ssh\kalshi-ohio-api.key`.
- Save the **Key ID** in a plain text file `%USERPROFILE%\.ssh\kalshi-ohio-api.id`, containing only the ID on one
  line.
- If the server is ever lost or anything looks wrong, delete this key on kalshi.com. Your main key isn't affected.

## 6. Tell Claude

Send: the server's **public IP address**, and "files are in place". Nothing else.

## What Claude does next (all read-only, no orders)

1. Tightens the key file permissions on Windows and tests `ssh -i %USERPROFILE%\.ssh\kalshi-ohio.pem ubuntu@<IP>`.
2. Installs Python on the server and copies the code: `src/`, `pyproject.toml` and the trial scripts. No data, logs
   or `.env` from this PC.
3. Copies the server key files over without printing them, and writes the server's own `.env`:
   - `KALSHI_ENV=prod`
   - `KALSHI_PROD_KEY_ID` (from the .id file)
   - `KALSHI_PROD_KEY_PATH` (the key's path on the server)

   Install with `pip install -e ".[trading]"`; Ubuntu 24.04 has Python 3.12, and 3.11 or later is needed.
4. Runs the latency measurement from Ohio, then starts the all-games lock watcher for each NBA night (with the same
   watcher at home for comparison).
5. After about 5 game nights, applies the decision rule in `reports/output/ohio_trial/PREREGISTRATION.md`. Then
   either the bot gets built (with your go-ahead) or the server is shut down.

## Costs and how to stop them

- **Running:** about $16/month all-in (server about $12, public IPv4 about $3.65, disk about $1.30), billed by the
  hour.
- **Stopped** (EC2 → Instance state → Stop): no server charge. The disk and IP charges continue, about $5/month.
- **Terminated** (Instance state → Terminate): every charge ends. Do this when the trial ends.
