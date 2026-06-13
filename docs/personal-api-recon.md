# Personal API Recon

Work Hunter can inspect a HAR file exported from your own logged-in browser
session and build a local endpoint inventory.

This is meant for a private, personal workflow:

- your account
- your browser session
- your local machine
- no shared tokens
- no secret values in generated reports

It does not try to bypass login, CAPTCHA, rate limits, or access controls. Use
the normal website login flow, export the network session from DevTools, and let
the tool map what your own browser already called.

## Usage

Start with public JS discovery:

```powershell
work-hunter api-discover-url https://hirehi.ru/ --host hirehi.ru --max-scripts 12
work-hunter api-discover-url https://app.rvc.global/ --host app.rvc.global --host api.rvc.global --max-scripts 12
```

Then probe the best candidates without cookies or auth headers:

```powershell
work-hunter api-probe-url https://hirehi.ru/ --host hirehi.ru --max-scripts 12 --limit 20
work-hunter api-probe-url https://app.rvc.global/ --host app.rvc.global --host api.rvc.global --limit 20
```

Probe results mark endpoints as:

- `public`
- `auth_required`
- `method_or_payload_required`
- `missing`
- `redirect`
- `network_error`

For logged-in/private account flows, use HAR analysis.

Export a HAR from browser DevTools, then run:

```powershell
work-hunter api-recon-har .\getmatch-session.har --host getmatch.ru
```

Multiple hosts are allowed:

```powershell
work-hunter api-recon-har .\session.har --host getmatch.ru --host api.getmatch.ru
```

Build an adapter promotion plan from the same HAR:

```powershell
work-hunter external-adapter-plan .\hirehi-session.har --source hirehi --host hirehi.ru
work-hunter external-adapter-plan .\rvc-session.har --source rvc --host app.rvc.global --host api.rvc.global
```

Store the personal session locally for explicit dry-run/real requests:

```powershell
work-hunter external-session import-har hirehi .\hirehi-session.har --host hirehi.ru
work-hunter external-session list
work-hunter external-session show hirehi
work-hunter external-session call hirehi GET https://hirehi.ru/api/search/jobs
```

`external-session call` is a dry run by default. It prints the request shape with
masked session headers. To make a real request with the locally stored session,
add `--real`:

```powershell
work-hunter external-session call hirehi GET https://hirehi.ru/api/search/jobs --real
```

Real mutating requests (`POST`, `PUT`, `PATCH`, `DELETE`) are blocked unless you
explicitly mark the command as an unsafe lab action:

```powershell
work-hunter external-session call hirehi POST https://hirehi.ru/api/applications --data-file .\payload.json --real --unsafe-lab
```

The report includes:

- unique method + URL endpoints
- redacted request headers
- redacted JSON request bodies
- response JSON previews
- tags such as `jobs`, `apply`, `profile`, `api`

Sensitive query params and headers are masked before printing:

- `Authorization`
- `Cookie`
- `csrf`
- `access_token`
- `refresh_token`
- `password`
- `secret`
- `api_key`
- `session`

## How To Use The Output

1. Find endpoints tagged `jobs` for search/listing/detail.
2. Find endpoints tagged `apply` for application flows.
3. Find endpoints tagged `profile` for resume/profile data.
4. Convert stable endpoints into a site adapter under `work_hunter/sources/`.
5. Keep new sources out of default scheduled sync unless you explicitly add them.
6. Use `external-adapter-plan` to choose the first source/apply implementation target.
7. Use `external-session call` dry-runs before any real session-backed request.

For the sites in this batch, the built-in generic public-board adapter is already
available for:

- `hirehi`
- `relocate_me`
- `rvc`
- `getmatch`
- `careerspace`
- `another_it`
- `jabka`

Existing specialized sources remain:

- `habr`
- `geekjob`
