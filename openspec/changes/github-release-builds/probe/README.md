# vista-runner-probe

A throwaway probe for VISTA's planned release builds (OpenSpec change
`github-release-builds`, design D10). It reports what each GitHub-hosted runner the release
uses actually offers, and builds nothing:

- virtualisation;
- glibc;
- disk;
- network;
- `msb doctor` from microsandbox 0.7.2.

Run it with `gh workflow run runner-probe.yml`, then read each job's summary.
