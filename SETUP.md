# SignalBot — Setup Progress

## Credentials Checklist

| Field | Status |
|---|---|
| `TG_API_ID` | ✅ Done |
| `TG_API_HASH` | ✅ Done |
| `BOT_TOKEN` | ✅ Done — @Hafiz_Carat_Signal_Bot |
| `YOUR_CHAT_ID` | ✅ Done |
| `MT5_LOGIN` | ⏳ Pending — need Windows PC |
| `MT5_PASSWORD` | ⏳ Pending — need Windows PC |
| `MT5_SERVER` | ⏳ Pending — need Windows PC |

## Requirements

- Python 3.10+ on **Windows** (MetaTrader5 package is Windows-only)
- MetaTrader 5 installed and logged in
- Telegram account that is a member of the signal group

## Steps to Run (Windows)

```bash
git clone https://github.com/jerzagit/botsignal.git
cd botsignal
pip install -r requirements.txt
copy .env.example .env
# Fill in .env with your credentials
python bot.py
```

## Signal Format (mentor's group)

```
xauusd sell @5096-5100
sl 5103
tp 5092
tp 5090
```

## Notes

- `.env` file is gitignored — never commit it
- Session file saved to `data/session` after first login
- Trade log saved to `data/trades.json`
- Night agent (10 PM – 6 AM MYT) not yet wired into bot.py
