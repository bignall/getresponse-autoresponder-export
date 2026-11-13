# GetResponse Autoresponder Export

Download all your GetResponse autoresponders (follow-up emails) in one go:

- `autoresponders.json`: everything, including each list's sequence order and the delay between emails
- `autoresponders.csv`: a spreadsheet you can open in Excel or Google Sheets
- `html/`: one `.html` file per email, plus `index.html` to browse them
- `txt/`: one plain-text file per email
- `autoresponders_content.zip`: the HTML and TXT folders in one archive

It is a single Python file that uses only the standard library, so there is nothing to install apart from Python 3.9 or newer.

**Documentation:** https://bignall.github.io/getresponse-autoresponder-export/

## Quick start

1. Get an API key in GetResponse: **Menu → Integrations and API → API → Generate API key**.
2. Download `export_autoresponders.py` (or clone this repository).
3. Run it:

   ```bash
   export GETRESPONSE_API_KEY=your-api-key
   python3 export_autoresponders.py
   ```

   On Windows (PowerShell):

   ```powershell
   $env:GETRESPONSE_API_KEY = "your-api-key"
   py export_autoresponders.py
   ```

   You can also copy `.env.example` to `.env` and put the key there.

The files are written to `exports/<date-time>/`.

## Options

| Option | What it does |
| --- | --- |
| `-o, --output-dir DIR` | Write to `DIR` instead of `exports/<date-time>` |
| `-f, --formats LIST` | Only some outputs, e.g. `-f json,csv` (choices: `json,csv,html,txt,zip`) |
| `--campaign-id ID` | Only one list (GetResponse calls lists "campaigns") |
| `--status enabled\|disabled` | Only enabled or only disabled autoresponders |
| `--max us\|pl --domain DOMAIN` | For GetResponse MAX accounts |
| `--include-raw` | Also keep the untouched API responses in the JSON |
| `--csv-no-body` | Leave the email bodies out of the CSV |
| `-v, --verbose` | Show progress |

Run `python3 export_autoresponders.py --help` for the full list.

## What changed from the original script

- **Uses the correct endpoint.** GetResponse API v3 lists autoresponders at `/autoresponders`, not `/messages?type=autoresponder`. Email content is read from `content.html` / `content.plain`, and the day of the cycle from `triggerSettings.dayOfCycle`.
- **No key in the code.** It is read from an environment variable or a `.env` file, so the script can be shared safely.
- **Handles rate limits and errors.** HTTP 429 and server errors are retried, and 401/403 errors explain what to check.
- **Safer files.** Filenames are cleaned and kept unique, and the ZIP holds tidy `html/` and `txt/` paths.
- **Ready for import elsewhere.** The JSON groups emails by list in send order, with `delayDaysFromPrevious` for each email.
- **No dependencies.** Uses only the Python standard library.

## Moving to FluentCRM

FluentCRM has no import screen for emails. The JSON export is designed to feed an importer that rebuilds each list as a FluentCRM email sequence. See the [FluentCRM page](https://bignall.github.io/getresponse-autoresponder-export/fluentcrm.html) for the plan and a manual workaround.

## Privacy

Your exports contain your email content. `exports/`, `*.zip` and `.env` are in `.gitignore` so they are not committed by accident.

## Development

```bash
pip install pytest
pytest -q
```

The tests run against a small fake GetResponse server, so they need no API key or network.

## License

Public domain ([The Unlicense](LICENSE)).
