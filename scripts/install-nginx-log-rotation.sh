#!/usr/bin/env bash
# Install nginx log rotation (OPS-35): copies scripts/ops/newsyslog-nginx.conf
# to /etc/newsyslog.d/ and shows what newsyslog would do with it.
# Needs sudo: /etc/newsyslog.d is root's, and so is nginx's master process.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/ops/newsyslog-nginx.conf"
dest="/etc/newsyslog.d/smart-answer-nginx.conf"

[[ -f /opt/homebrew/var/run/nginx.pid ]] || { echo "nginx pid file not found; is nginx running?" >&2; exit 1; }
sudo install -m 644 -o root -g wheel "$src" "$dest"
echo "installed $dest"
echo "--- dry run (newsyslog -nvv), nothing is rotated:"
sudo newsyslog -nvv -f "$dest"
