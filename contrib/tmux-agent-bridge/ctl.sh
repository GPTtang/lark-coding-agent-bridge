#!/bin/zsh
# Manage the bridge's launchd jobs (macOS).
#   ctl.sh install     write + load both jobs: bridge (KeepAlive) and tmux supervisor (every 30s)
#   ctl.sh uninstall   unload + remove both jobs
#   ctl.sh start|stop|restart|status|logs   the bridge job
DIR=${0:A:h}
PREFIX=${BRIDGE_LAUNCHD_PREFIX:-com.lark-agent-bridge}
BRIDGE=$PREFIX.bridge
SUPERVISOR=$PREFIX.tmux
AGENTS_DIR=$HOME/Library/LaunchAgents
PY=$DIR/.venv/bin/python
JOB_PATH=$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

plist() {  # plist <label> <extra keys xml> <program args...>
  local label=$1 extra=$2; shift 2
  local args=""
  for a in "$@"; do args+="<string>$a</string>"; done
  cat > "$AGENTS_DIR/$label.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>$args</array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$JOB_PATH</string>
    <key>HOME</key><string>$HOME</string>
    <key>LANG</key><string>en_US.UTF-8</string>
    <key>SHELL</key><string>/bin/zsh</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  $extra
  <key>StandardOutPath</key><string>$DIR/$label.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/$label.err.log</string>
</dict>
</plist>
EOF
}

case "$1" in
  install)
    mkdir -p "$AGENTS_DIR"
    plist $BRIDGE '<key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>60</integer>' \
      "$PY" "$DIR/bridge.py"
    # AbandonProcessGroup: tmux daemonizes; keep its server and the agents alive after each pass.
    plist $SUPERVISOR '<key>StartInterval</key><integer>30</integer><key>AbandonProcessGroup</key><true/>' \
      "$PY" "$DIR/tmux_supervisor.py" ensure
    for l in $BRIDGE $SUPERVISOR; do
      launchctl bootout gui/$UID/$l 2>/dev/null
      launchctl bootstrap gui/$UID "$AGENTS_DIR/$l.plist" && echo "loaded $l"
    done ;;
  uninstall)
    for l in $BRIDGE $SUPERVISOR; do
      launchctl bootout gui/$UID/$l 2>/dev/null
      rm -f "$AGENTS_DIR/$l.plist" && echo "removed $l"
    done ;;
  start)   launchctl bootstrap gui/$UID "$AGENTS_DIR/$BRIDGE.plist" ;;
  stop)    launchctl bootout gui/$UID/$BRIDGE ;;
  restart) launchctl kickstart -k gui/$UID/$BRIDGE ;;
  status)  for l in $BRIDGE $SUPERVISOR; do
             echo "$l:"; launchctl print gui/$UID/$l 2>/dev/null | grep -E '^\s+(state|pid|last exit code) =' || echo "  (not loaded)"
           done ;;
  logs)    tail -n 50 -f "$DIR/bridge.log" "$DIR/$BRIDGE.err.log" ;;
  *)       echo "usage: $0 install|uninstall|start|stop|restart|status|logs"; exit 1 ;;
esac
