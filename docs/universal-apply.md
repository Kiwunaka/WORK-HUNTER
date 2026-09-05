# Universal applications

Work Hunter uses one local pipeline for HH, LinkedIn, Indeed, Habr Career,
HireHi, Relocate.me, GetMatch, CareerSpace, GeekJob, Another-IT, and Jabka:

1. collect and score a vacancy;
2. prepare the cover letter and form answers;
3. build a dry-run application plan;
4. send after explicit confirmation;
5. store the result in the common application history.

## First setup

Open the local UI and fill `name`, `email`, `phone`, and `resume_path` in the
active profile. Install the browser runtime once if required:

```powershell
python -m pip install -e ".[browser,ui]"
python -m playwright install chromium
```

Account passwords are not stored in Work Hunter. Prepare a persistent browser
session interactively:

```powershell
work-hunter browser-login linkedin
work-hunter browser-login indeed
work-hunter browser-login getmatch
```

Each source has its own profile under `.work-hunter/browser/<source>`.

## Search and apply

```powershell
work-hunter sync --source linkedin --source indeed --limit 50
work-hunter score
work-hunter list --min-score 70 --json
work-hunter apply-plan JOB_ID
work-hunter apply JOB_ID --confirm
```

LinkedIn search uses its public guest job-card endpoint. Indeed rejects normal
HTTP collection from some regions, so its collector automatically falls back
to the persistent browser. External applications use browser form filling by
default. A source can be promoted to a direct authenticated session adapter
after importing and reviewing a personal HAR.

## APK and HAR recon

Raw reports and recovered constants stay in ignored local storage:

```powershell
work-hunter apk-recon .\app.apk --source hh --jadx
work-hunter api-recon-har .\session.har --host example.com
```

The CLI output masks credential candidates. The unmasked APK report is stored
only at `.work-hunter/research/apk/<source>/report.private.json`.
