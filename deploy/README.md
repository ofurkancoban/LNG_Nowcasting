# VPS provisioning: AIS ingestion host

The long-lived AISStream.io WebSocket connection runs on a dedicated VPS as
a systemd-supervised process, not on GitHub Actions (which cannot hold a
persistent connection for a scheduled job). See ADR 0001 and
docs/milestones/M7.md for why.

## Security note

Never provision this host with password-based root SSH login. Use a
non-root deploy user with key-based SSH auth only, and disable password
authentication in `/etc/ssh/sshd_config` (`PasswordAuthentication no`).
Store the private key used by `.github/workflows/vps-ingester-health-check.yml`
as a GitHub Actions secret (`VPS_SSH_PRIVATE_KEY`), never in the repository.

## Provisioning steps

1. Create a non-root deploy user, e.g. `lng`:
   ```
   adduser --disabled-password lng
   ```

2. Install Python 3.11+ and `uv`:
   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. Clone the repository to `/opt/lng-nowcasting` and install dependencies:
   ```
   sudo mkdir -p /opt/lng-nowcasting
   sudo chown lng:lng /opt/lng-nowcasting
   git clone <repo-url> /opt/lng-nowcasting
   cd /opt/lng-nowcasting
   uv sync
   ```

4. Create `/opt/lng-nowcasting/.env` (never committed; matches the
   project's gitignored `.env` convention) containing:
   ```
   AISSTREAM_API_KEY=<the real key>
   ```
   Restrict permissions: `chmod 600 /opt/lng-nowcasting/.env`.

5. Install the systemd unit:
   ```
   sudo cp deploy/systemd/lng-ingest-aisstream.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now lng-ingest-aisstream.service
   ```

6. Check status and logs:
   ```
   systemctl status lng-ingest-aisstream.service
   journalctl -u lng-ingest-aisstream.service -f
   ```

7. Log rotation: systemd's journal handles rotation automatically via
   `journald`'s own retention policy (`/etc/systemd/journald.conf`,
   `SystemMaxUse=`). No separate logrotate configuration is required since
   `StandardOutput=journal` / `StandardError=journal` route through
   `journald`.

## Verifying the unit file

`systemd-analyze verify deploy/systemd/lng-ingest-aisstream.service` requires
a Linux host with systemd's tooling installed (this does not require systemd
to be PID 1). It also checks that `ExecStart`'s binary and `User=` actually
exist wherever the check runs, so a bare debian image without the target
paths reports a (misleading, environment-only) failure. On a non-Linux
development machine, this project verified the unit file using a disposable
container with those paths stubbed out:

```
docker run -d --name lng-systemd-verify debian:bookworm-slim sleep infinity
docker exec lng-systemd-verify bash -c "apt-get update -qq && apt-get install -y -qq systemd"

docker exec lng-systemd-verify bash -c "
  useradd -m lng
  mkdir -p /opt/lng-nowcasting/.venv/bin
  printf '#!/bin/sh\nexit 0\n' > /opt/lng-nowcasting/.venv/bin/python
  chmod +x /opt/lng-nowcasting/.venv/bin/python
  touch /opt/lng-nowcasting/.env
  chown -R lng:lng /opt/lng-nowcasting
"
docker cp deploy/systemd/lng-ingest-aisstream.service \
  lng-systemd-verify:/etc/systemd/system/lng-ingest-aisstream.service
docker exec lng-systemd-verify systemd-analyze verify /etc/systemd/system/lng-ingest-aisstream.service
# exit 0 confirms valid syntax and a Restart= policy is set

docker rm -f lng-systemd-verify
```

On the real VPS, once provisioning steps 1-5 above are complete, the same
`systemd-analyze verify` command can be run directly against the installed
unit file without any stubbing, since the real paths and user will exist.

## What is NOT yet automated

- There is no automated deployment/CD step that pulls new commits onto the
  VPS and restarts the service. Deploying an updated version currently
  means SSHing in, `git pull`, `uv sync`, and
  `systemctl restart lng-ingest-aisstream.service` by hand. Automating this
  safely (without risking an unattended bad deploy killing the only
  ingestion path) is left as explicit follow-up work, not silently assumed.
