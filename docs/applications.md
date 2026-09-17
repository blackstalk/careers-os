# Application tracking

Scheduled runs never email a job you've already applied to or ruled out.

## Why a committed file

Each job's `status` column (`jobs status ... applied`) lives in whichever
SQLite file you edit. The scheduled GitHub Actions run uses its own
database copy in the Actions cache, so a status set on your Mac never
reaches it. `career/data/applications.yaml` is committed to the repo, so
every run reads it.

```yaml
applications:
- company: WorkOS
  title: Applied AI Engineer
  status: applied          # applied | interviewing | offer | rejected | withdrawn | not_interested
  url: https://jobs.ashbyhq.com/workos/5e650527-d8dd-413a-9cfb-d7d68143274b
  date: '2026-09-16'
```

## Recording an application

```bash
command jobs applied https://jobs.ashbyhq.com/workos/5e650527-...
command jobs applied https://example.com/job/1 --company Acme --title "Platform Engineer" --status not_interested
git add src/careers_os/career/data/applications.yaml && git commit -m "chore: record application" && git push
```

`jobs applied` looks the job up in the local database by URL to fill in the
company and title, writes the file, and also sets the local job status.

## How matching works

A discovered job is skipped (`already_applied` in `jobs run`) when any of
these hold, for the job itself or any high-confidence cross-source
duplicate of it:

- its posting URL matches an entry. The comparison ignores scheme, `www.`,
  trailing slashes, and a trailing `/application` or `/apply`;
- its normalized company **and** title match an entry. This also catches
  aggregator copies and regional clones of the same role at the same
  company;
- its status in the running database is applied, interviewing, offer,
  rejected, or closed.

The skip happens after evaluation, so the job's evaluation is still stored
and visible in `jobs evaluate`.
