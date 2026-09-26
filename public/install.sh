#!/usr/bin/env bash
# Flowbench installer for macOS / Linux.
#   curl -fsSL https://flowbench.zichaoleng55.workers.dev/install.sh | bash
# Installs into ~/.local/share/flowbench and adds the `flowbench` command to ~/.local/bin.
set -euo pipefail
SITE="https://flowbench.zichaoleng55.workers.dev"
FB="$HOME/.local/share/flowbench"
BIN="$HOME/.local/bin"
UA="flowbench-installer/1.0"

echo; echo "  FLOWBENCH installer"; echo "  ----------------------------------------"
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$("$c" -c 'import sys; print(sys.executable)')"; break; fi
done
if [ -z "$PY" ]; then echo "  Python 3 was not found. Install it first (https://www.python.org/downloads/)."; exit 1; fi
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "  Flowbench needs Python 3.9 or newer."; exit 1; }
echo "  Using $PY"

mkdir -p "$FB" "$BIN"
[ -x "$FB/runtime/bin/python" ] || "$PY" -m venv "$FB/runtime"
"$FB/runtime/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade websockets
curl -fsSL -A "$UA" "$SITE/runner.py" -o "$FB/runner.py"

if [ ! -f "$FB/config.json" ]; then
  "$FB/runtime/bin/python" - "$HOME" "$PY" "$FB/config.json" <<'PYCFG'
import json, sys, os
home, py, path = sys.argv[1:4]
root = os.path.join(home, "Desktop") if os.path.isdir(os.path.join(home, "Desktop")) else home
json.dump({"roots": [root], "python": py, "port": 8765}, open(path, "w"), indent=2)
PYCFG
fi

cat > "$BIN/flowbench" <<'LAUNCH'
#!/usr/bin/env bash
# flowbench - start and manage the Flowbench runner
FB="$HOME/.local/share/flowbench"; PYR="$FB/runtime/bin/python"; CFG="$FB/config.json"
SITE="https://flowbench.zichaoleng55.workers.dev"
port() { "$PYR" -c "import json;print(json.load(open('$CFG')).get('port',8765))"; }
running() { "$PYR" -c "import socket,sys;s=socket.socket();s.settimeout(.5);sys.exit(s.connect_ex(('127.0.0.1',$(port))))"; }
openpage() { url="http://127.0.0.1:$(port)/#token=$(cat "$HOME/.flowbench/token" 2>/dev/null)"; (command -v open >/dev/null && open "$url") || (command -v xdg-open >/dev/null && xdg-open "$url") || echo "  Open $url"; }
edit() { "$PYR" - "$CFG" "$@" <<'PYE'
import json, os, sys
path, op, *args = sys.argv[1:]
c = json.load(open(path))
if op == "add":
    for a in args or ["."]:
        p = os.path.abspath(os.path.expanduser(a))
        if os.path.isdir(p) and p not in c["roots"]: c["roots"].append(p); print("  Added", p)
elif op == "remove":
    for a in args:
        p = os.path.abspath(os.path.expanduser(a)); c["roots"] = [r for r in c["roots"] if r not in (p, a)]; print("  Removed", a)
elif op == "python":
    c["python"] = os.path.abspath(os.path.expanduser(args[0])); print("  Your code will run with", c["python"])
json.dump(c, open(path, "w"), indent=2)
PYE
}
start() {
  if running; then echo "  Flowbench is already running. Opening it."; openpage; return; fi
  curl -fsSL -m 6 -A flowbench-cli "$SITE/runner.py" -o "$FB/runner.py.new" 2>/dev/null && mv "$FB/runner.py.new" "$FB/runner.py" || true
  nohup "$PYR" "$FB/runner.py" --config "$CFG" "$@" > "$FB/runner.log" 2>&1 &
  echo "  Flowbench is starting (log: $FB/runner.log). Stop it with: flowbench stop"
}
stop() {
  if [ -f "$HOME/.flowbench/runner.pid" ]; then kill "$("$PYR" -c "import json;print(json.load(open('$HOME/.flowbench/runner.pid'))['pid'])")" 2>/dev/null && echo "  Flowbench stopped." || echo "  Flowbench was not running."; rm -f "$HOME/.flowbench/runner.pid"; else echo "  Flowbench was not running."; fi; sleep .4
}
restart_if() { if running; then stop; start; fi; }
case "${1:-start}" in
  start|open) start ;;
  add) shift; edit add "$@"; restart_if ;;
  remove|rm) shift; edit remove "$@"; restart_if ;;
  folders|list|ls) "$PYR" -c "import json;[print('  '+r) for r in json.load(open('$CFG'))['roots']]" ;;
  python) shift; edit python "$1"; restart_if ;;
  examples) stop >/dev/null; start --examples ;;
  stop) stop ;;
  restart) stop; start ;;
  status) if running; then echo "  Running on http://127.0.0.1:$(port)"; else echo "  Not running."; fi ;;
  update) curl -fsSL "$SITE/install.sh" | bash ;;
  token) stop; rm -f "$HOME/.flowbench/token"; start ;;
  *) cat <<'HELP'

  flowbench                 start Flowbench (or open it if it is already running)
  flowbench add [folder]    let Flowbench read a code folder (default: the current folder)
  flowbench remove <folder> stop showing a folder
  flowbench folders         list your folders
  flowbench python <path>   choose which Python runs your code (e.g. a conda / venv python)
  flowbench examples        add a small demo folder to your first folder
  flowbench status | stop | restart | update | token

HELP
  ;;
esac
LAUNCH
chmod +x "$BIN/flowbench"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  Add this to your shell profile:  export PATH=\"\$HOME/.local/bin:\$PATH\"";; esac
echo; echo "  Done! Run:  flowbench        (add a folder: flowbench add ~/code)"; echo
"$BIN/flowbench" start
