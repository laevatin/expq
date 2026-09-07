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

case ":$PATH:" in *":$BIN:"*) ;; *) echo "NOTE: add $BIN to your PATH" ;; esac

if command -v systemctl >/dev/null 2>&1 && systemctl --user is-system-running >/dev/null 2>&1; then
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
  systemctl --user daemon-reload
  systemctl --user enable --now pueued.service
  echo "pueued running as a systemd user service"
  if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    echo "NOTE: run 'sudo loginctl enable-linger $USER' so the queue survives logout"
  fi
else
  pueued -d
fi

sleep 1
PATH="$BIN:$PATH" "$BIN/exp" init "$@"
