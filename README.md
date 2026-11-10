# Help Alert Bot (English + Arabic -> Telegram)

## 1. Create the Telegram bot
1. In Telegram open @BotFather, send /newbot, copy the TOKEN.
2. Open @userinfobot, copy your numeric ID (CHAT_ID).
3. Open your new bot and press Start (it can't message you before that).

## 2. Test locally (PC / Termux / server)
    pip install -r requirements.txt
    python help_alert_bot.py --selftest      # offline keyword tests
    export TELEGRAM_BOT_TOKEN="..."          # Windows: set TELEGRAM_BOT_TOKEN=...
    export TELEGRAM_CHAT_ID="..."            # Windows: set TELEGRAM_CHAT_ID=...
    python help_alert_bot.py --test          # sends a test message
    python help_alert_bot.py --dry-run       # prints matches, sends nothing
    python help_alert_bot.py                 # run forever

## 3. Run free 24/7 on GitHub Actions
1. Create a PUBLIC GitHub repo and upload ALL files in this folder,
   keeping .github/workflows/bot.yml in that exact path.
2. Settings > Secrets and variables > Actions > New repository secret:
   TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
3. Actions tab > help-alert-bot > Run workflow. You get "Bot is running".
4. It then runs every ~10 minutes. If alerts stop after 60 days of
   inactivity, re-enable the workflow in the Actions tab.

## Customize
Edit the CONFIG section at the top of help_alert_bot.py:
keywords (English/Arabic), subreddits, Stack Exchange tags, EXTRA_RSS_FEEDS.
Re-run --selftest after changing keywords.
