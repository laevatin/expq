#!/bin/sh
# Install pueue (static binary) + exp into ~/.local/bin, run pueued as a
# systemd user service, and create the per-GPU groups. Linux only; on macOS
# use `brew install pueue` and copy `exp` onto your PATH.
set -eu

BIN="$HOME/.local/bin"
VER="${PUEUE_VERSION:-v4.0.4}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$BIN"

arch="$(uname -m)"
case "$arch" in
  x86_64)  target=x86_64-unknown-linux-musl ;;
  aarch64|arm64) target=aarch64-unknown-linux-musl ;;
  *) echo "unsupported arch $arch" >&2; exit 1 ;;
esac

if ! command -v pueued >/dev/null 2>&1 && [ ! -x "$BIN/pueued" ]; then
  for b in pueue pueued; do
    url="https://github.com/Nukesor/pueue/releases/download/$VER/$b-$target"
    echo "fetching $url"
    curl -fsSL "$url" -o "$BIN/$b.tmp"
    chmod +x "$BIN/$b.tmp"
    mv -f "$BIN/$b.tmp" "$BIN/$b"   # atomic: safe even if pueued is running
  done
fi

install -m 755 "$HERE/exp" "$BIN/exp"

# Explicit paths in pueue's config. The static musl build cannot look up the
# username on hosts with LDAP/NIS accounts, and without a name it cannot even
# derive its default socket path. Fixed paths sidestep that everywhere.
CFG="$HOME/.config/pueue/pueue.yml"
RUN="${XDG_RUNTIME_DIR:-/tmp}"
if [ ! -f "$CFG" ]; then
  mkdir -p "$HOME/.config/pueue"
  cat > "$CFG" <<YML
shared:
  pueue_directory: $HOME/.local/share/pueue
  runtime_directory: $RUN
  use_unix_socket: true
  unix_socket_path: $RUN/pueue-$(id -u).sock
YML
elif grep -q '^  unix_socket_path: null' "$CFG"; then
  sed -i.bak \
    -e "s|^  pueue_directory: null|  pueue_directory: $HOME/.local/share/pueue|" \
    -e "s|^  runtime_directory: null|  runtime_directory: $RUN|" \
    -e "s|^  unix_socket_path: null|  unix_socket_path: $RUN/pueue-$(id -u).sock|" "$CFG"
fi

case ":$PATH:" in *":$BIN:"*) ;; *) echo "NOTE: add $BIN to your PATH" ;; esac

# is-system-running exits non-zero for "degraded" (some unrelated unit failed); that is still a usable instance.
user_systemd="$(systemctl --user is-system-running 2>/dev/null || true)"
case "$user_systemd" in running|degraded) have_user_systemd=1 ;; *) have_user_systemd=0 ;; esac
if [ "$have_user_systemd" = 1 ]; then
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/pueued.service" <<UNIT
[Unit]
Description=pueue daemon (exp queue)
After=network.target

[Service]
ExecStart=$BIN/pueued -v
Restart=on-failure

[Install]
WantedBy=default.target
UNIT
  # Sliding 24h window over finished tasks: hourly, drop anything that finished >24h ago.
  cat > "$HOME/.config/systemd/user/exp-clean.service" <<UNIT
[Unit]
Description=exp: drop tasks finished more than 24h ago
After=pueued.service

[Service]
Type=oneshot
ExecStart=$BIN/exp clean --older-than 24
UNIT
  cat > "$HOME/.config/systemd/user/exp-clean.timer" <<UNIT
[Unit]
Description=exp: hourly sliding-window clean

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  systemctl --user daemon-reload
  systemctl --user reset-failed pueued.service 2>/dev/null || true
  systemctl --user enable --now pueued.service
  systemctl --user restart pueued.service
  systemctl --user enable --now exp-clean.timer
  echo "pueued running as a systemd user service; exp-clean.timer prunes finished tasks hourly"
  if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    echo "NOTE: run 'sudo loginctl enable-linger $USER' so the queue survives logout"
  fi
else
  "$BIN/pueued" -d
  # No user systemd: same hourly sliding-window clean via cron, if available.
  line="0 * * * * $BIN/exp clean --older-than 24 >/dev/null 2>&1"
  if command -v crontab >/dev/null 2>&1; then
    { crontab -l 2>/dev/null | grep -v 'exp clean --older-than' || true; echo "$line"; } | crontab -
    echo "cron entry added: hourly exp clean --older-than 24"
  else
    echo "NOTE: no systemd or cron; run 'exp clean --older-than 24' periodically"
  fi
fi

sleep 1
PATH="$BIN:$PATH" "$BIN/exp" init "$@"
