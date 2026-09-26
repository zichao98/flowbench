#!/usr/bin/env python3
"""
Flowbench local runner.

Connects the Flowbench canvas (in your browser) to this computer so it can:
  * list the Python files in the folders you allow and read their functions,
  * run a workflow of those functions in your own Python environment,
  * open real terminals (PowerShell / cmd / Python / bash) on the canvas.

Security: it listens on 127.0.0.1 only, requires a secret token, only accepts
browser connections from the Flowbench site (or localhost), and only reads files
inside the --root folders you pass.

Usage:
    pip install websockets pywinpty        # pywinpty: Windows only, for full terminals
    python runner.py --root "C:\\Users\\you\\Desktop\\my-code"
"""
import argparse
import ast
import asyncio
import codecs
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
import webbrowser
from collections import defaultdict
from pathlib import Path

VERSION = "1.1"
APP_URL = "https://flowbench.zichaoleng55.workers.dev/"
IS_WIN = os.name == "nt"
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache", ".idea", ".vscode", "site-packages", ".ipynb_checkpoints", "dist", "build", ".tox", ".wrangler"}

try:
    import websockets
except ImportError:
    print("Missing dependency. Run:  pip install websockets" + ("  pywinpty" if IS_WIN else ""))
    sys.exit(1)
try:
    from websockets.asyncio.server import serve as ws_serve
    NEW_API = True
except ImportError:  # websockets < 13
    from websockets import serve as ws_serve
    NEW_API = False
try:
    from winpty import PtyProcess  # pywinpty
except Exception:
    PtyProcess = None


# ─────────────────────────── kernel (runs your code in a separate process) ───────────────────────────
KERNEL_SRC = r'''
import sys, os, io, json, time, traceback, importlib.util, hashlib, base64, ast
os.environ.setdefault("MPLBACKEND", "Agg")
_proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
os.dup2(2, 1)  # anything written straight to fd 1 by C code goes to stderr, not the protocol
def send(o):
    _proto.write(json.dumps(o, default=str) + "\n"); _proto.flush()

class Stream(io.TextIOBase):
    def __init__(self, nid, name): self.nid, self.name = nid, name
    def write(self, s):
        if s: send({"ev": "stream", "nid": self.nid, "name": self.name, "text": s})
        return len(s)
    def flush(self): pass
    def isatty(self): return False

RESULTS, MODS, FIGS = {}, {}, {}
NS = {"__name__": "__flow__"}
try:
    import numpy as np; NS["np"] = np
except Exception: np = None

def load(path):
    mt = os.path.getmtime(path); hit = MODS.get(path)
    if hit and hit[0] == mt: return hit[1]
    d = os.path.dirname(path)
    if d not in sys.path: sys.path.insert(0, d)
    name = "fb_" + hashlib.md5(path.encode()).hexdigest()[:10]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
    spec.loader.exec_module(mod)
    MODS[path] = (mt, mod); return mod

def ev(expr, local=None):
    expr = (expr or "").strip()
    if not expr: return None
    try: return ast.literal_eval(expr)
    except Exception: return eval(expr, dict(NS), local or {})

def resolve(spec):
    if "ref" in spec: return RESULTS[spec["ref"]]
    return ev(spec.get("expr", ""))

def figures(result):
    out = []
    if "matplotlib.pyplot" not in sys.modules: return out
    import matplotlib.pyplot as plt
    figs = [plt.figure(n) for n in plt.get_fignums()]
    try:
        from matplotlib.figure import Figure
        if isinstance(result, Figure) and result not in figs: figs.append(result)
    except Exception: pass
    for f in figs:
        buf = io.BytesIO()
        try:
            f.savefig(buf, format="png", dpi=110, bbox_inches="tight"); out.append(base64.b64encode(buf.getvalue()).decode())
        except Exception: pass
    plt.close("all")
    return out

def preview(v):
    p = {"type": type(v).__name__}
    try:
        mod = type(v).__module__
        if np is not None and isinstance(v, np.ndarray):
            p.update(shape=list(v.shape), dtype=str(v.dtype), text=np.array2string(v, threshold=120, edgeitems=4, precision=4))
            if v.ndim == 1 and v.size > 1 and np.issubdtype(v.dtype, np.number):
                step = max(1, v.size // 1500); p["plot"] = [float(x) for x in v[::step]]
            return p
        if mod.startswith("pandas"):
            p["shape"] = list(getattr(v, "shape", []))
            if hasattr(v, "to_frame") and not hasattr(v, "columns"): v = v.to_frame()
            p["html"] = v.head(60).to_html(max_cols=30, border=0)
            return p
        if mod.startswith("PIL"):
            buf = io.BytesIO(); v.save(buf, format="PNG"); p["images"] = [base64.b64encode(buf.getvalue()).decode()]; return p
        if isinstance(v, (list, tuple)) and 1 < len(v) <= 200000 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v[:2000]):
            step = max(1, len(v) // 1500); p["plot"] = [float(x) for x in v[::step]]; p["shape"] = [len(v)]
        if isinstance(v, str): p["text"] = v[:20000]; return p
        try: p["text"] = json.dumps(v, indent=1, default=str)[:20000]
        except Exception: p["text"] = repr(v)[:20000]
    except Exception as e:
        p["text"] = "<preview failed: %s>" % e
    return p

def jsonable(v, d=0):
    """Convert a Python result into plain JSON so other languages can read it."""
    if d > 60: return repr(v)
    if v is None or isinstance(v, (bool, int, str)): return v
    if isinstance(v, float): return v if v == v and v not in (float("inf"), float("-inf")) else None
    if np is not None:
        if isinstance(v, np.ndarray): return jsonable(v.tolist(), d + 1)
        if isinstance(v, np.generic): return jsonable(v.item(), d + 1)
    mod = type(v).__module__
    if mod.startswith("pandas"):
        if hasattr(v, "columns"): return jsonable(v.to_dict(orient="records"), d + 1)
        if hasattr(v, "tolist"): return jsonable(v.tolist(), d + 1)
    if isinstance(v, dict): return {str(k): jsonable(x, d + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)): return [jsonable(x, d + 1) for x in v]
    if isinstance(v, (bytes, bytearray)): return base64.b64encode(bytes(v)).decode()
    if hasattr(v, "isoformat"): return v.isoformat()
    return repr(v)

for line in sys.stdin:
    try: msg = json.loads(line)
    except Exception: continue
    if msg.get("cmd") == "forget":
        for k in msg.get("nids", []): RESULTS.pop(k, None)
        continue
    if msg.get("cmd") == "set":  # a value produced by another language
        RESULTS[msg["nid"]] = msg.get("value"); FIGS[msg["nid"]] = msg.get("images", [])
        continue
    if msg.get("cmd") == "export":  # hand a Python value to another language
        try: send({"ev": "export", "nid": msg["nid"], "ok": True, "value": jsonable(RESULTS.get(msg["nid"]))})
        except Exception as e: send({"ev": "export", "nid": msg["nid"], "ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        continue
    nid, kind = msg["nid"], msg["kind"]
    old = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = Stream(nid, "stdout"), Stream(nid, "stderr")
    t0 = time.perf_counter()
    try:
        args = {k: resolve(s) for k, s in msg.get("args", {}).items()}
        if kind == "func":
            res = getattr(load(msg["file"]), msg["func"])(**args)
            if hasattr(res, "__await__"):
                import asyncio; res = asyncio.run(res)
        elif kind == "code":
            ns = dict(NS); ns.update(args)
            exec(compile(msg.get("code", ""), "<code node>", "exec"), ns)
            res = ns.get("result")
        elif kind == "value":
            res = ev(msg.get("expr", ""))
        else:
            res = args.get("data")
        RESULTS[nid] = res
        pv = preview(res); imgs = figures(res)
        if kind == "viewer":  # also show the plots drawn by the node feeding this viewer
            src = msg.get("args", {}).get("data", {}).get("ref")
            imgs = FIGS.get(src, []) + imgs
        FIGS[nid] = imgs
        if imgs: pv["images"] = pv.get("images", []) + imgs
        sys.stdout, sys.stderr = old
        send({"ev": "done", "nid": nid, "ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1), "preview": pv})
    except BaseException:
        sys.stdout, sys.stderr = old
        try: figures(None)
        except Exception: pass
        send({"ev": "done", "nid": nid, "ok": False, "ms": round((time.perf_counter() - t0) * 1000, 1), "error": traceback.format_exc(limit=12)})
'''


class Kernel:
    def __init__(self, python, cwd, on_event):
        self.python, self.cwd, self.on_event = python, cwd, on_event
        self.proc, self.pending, self.path = None, {}, None

    async def ensure(self):
        if self.proc and self.proc.returncode is None:
            return
        self.path = os.path.join(tempfile.gettempdir(), "flowbench_kernel.py")
        Path(self.path).write_text(KERNEL_SRC, encoding="utf-8")
        env = dict(os.environ, MPLBACKEND="Agg", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proc = await asyncio.create_subprocess_exec(
            self.python, "-u", self.path, cwd=self.cwd, env=env, limit=256 * 1024 * 1024,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        asyncio.create_task(self._read_out(self.proc))
        asyncio.create_task(self._read_err(self.proc))

    async def _read_out(self, proc):
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("ev") in ("done", "export"):
                fut = self.pending.pop(("x:" if ev["ev"] == "export" else "") + ev["nid"], None)
                if fut and not fut.done():
                    fut.set_result(ev)
            else:
                self.on_event(ev)
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("The Python kernel stopped"))
        self.pending.clear()

    async def _read_err(self, proc):
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            self.on_event({"ev": "stream", "nid": None, "name": "stderr", "text": line.decode("utf-8", "replace")})

    async def run(self, payload):
        return await self._call(payload["nid"], payload)

    async def export(self, nid):
        """Get a Python result as plain JSON (for other languages)."""
        return await self._call("x:" + nid, {"cmd": "export", "nid": nid})

    async def put(self, nid, value, images=None):
        """Store a value produced by another language so Python nodes can use it."""
        await self.ensure()
        self.proc.stdin.write((json.dumps({"cmd": "set", "nid": nid, "value": value, "images": images or []}) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def _call(self, key, payload):
        await self.ensure()
        fut = asyncio.get_running_loop().create_future()
        self.pending[key] = fut
        self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()
        return await fut

    def kill(self):
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
        self.proc = None


# ─────────────────────────── other languages & containers ───────────────────────────
# tier "vars": inputs become variables and `result` is passed on (a small wrapper is added).
# tier "prog": a normal program; inputs are in the JSON file $FLOW_IN, whatever it prints is the result.
EXE = ".exe" if IS_WIN else ""
LANGS = {
    "javascript": {"ext": ".cjs", "tier": "vars", "tool": "node", "run": ["node", "{src}"], "image": "node:22-alpine", "crun": "node {src}"},
    "typescript": {"ext": ".cts", "tier": "vars", "tool": "node", "run": ["node", "{src}"], "image": "node:24-alpine", "crun": "node {src}"},
    "python": {"ext": ".py", "tier": "vars", "tool": None, "run": None, "image": "python:3.12-slim", "crun": "python {src}"},
    "powershell": {"ext": ".ps1", "tier": "prog", "tool": "powershell", "run": ["{tool}", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "{src}"],
                   "image": "mcr.microsoft.com/powershell", "crun": "pwsh -NoProfile -File {src}"},
    "bash": {"ext": ".sh", "tier": "prog", "tool": "bash", "run": ["{tool}", "{src}"], "image": "bash:5", "crun": "bash {src}"},
    "rust": {"ext": ".rs", "tier": "prog", "tool": "rustc", "build": ["rustc", "-O", "{src}", "-o", "{bin}"], "run": ["{bin}"],
             "image": "rust:1-slim", "crun": "rustc -O {src} -o /tmp/prog && /tmp/prog"},
    "c": {"ext": ".c", "tier": "prog", "tool": "gcc", "build": ["gcc", "-O2", "{src}", "-o", "{bin}", "-lm"], "run": ["{bin}"],
          "image": "gcc:14", "crun": "gcc -O2 {src} -o /tmp/prog -lm && /tmp/prog"},
    "cpp": {"ext": ".cpp", "tier": "prog", "tool": "g++", "build": ["g++", "-O2", "-std=c++17", "{src}", "-o", "{bin}"], "run": ["{bin}"],
            "image": "gcc:14", "crun": "g++ -O2 -std=c++17 {src} -o /tmp/prog && /tmp/prog"},
    "go": {"ext": ".go", "tier": "prog", "tool": "go", "run": ["go", "run", "{src}"], "image": "golang:1.23-alpine", "crun": "go run {src}"},
    "r": {"ext": ".R", "tier": "prog", "tool": "Rscript", "run": ["Rscript", "{src}"], "image": "r-base", "crun": "Rscript {src}"},
    "julia": {"ext": ".jl", "tier": "prog", "tool": "julia", "run": ["julia", "{src}"], "image": "julia:1", "crun": "julia {src}"},
    "java": {"ext": ".java", "tier": "prog", "tool": "java", "run": ["java", "{src}"], "image": "eclipse-temurin:21", "crun": "java {src}", "name": "Main.java"},
    "shell": {"ext": ".sh", "tier": "prog", "tool": None, "run": None, "image": "alpine:3", "crun": "sh {src}"},
}
EXT_LANG = {".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript", ".ps1": "powershell", ".sh": "bash",
            ".rs": "rust", ".c": "c", ".cpp": "cpp", ".cc": "cpp", ".go": "go", ".r": "r", ".jl": "julia", ".java": "java"}


def find_tool(name):
    if not name:
        return None
    if name == "bash" and IS_WIN:  # prefer Git Bash; C:\Windows\System32\bash.exe is WSL
        for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.exists(p):
                return p
        w = shutil.which("bash")
        return w if w and "system32" not in w.lower() else None
    if name == "powershell":
        return shutil.which("pwsh") or shutil.which("powershell")
    return shutil.which(name)


ENGINE = {"name": None, "path": None, "ok": False, "detail": "not checked yet"}


async def detect_engine():
    """Find Podman (preferred) or Docker, and whether it can actually run containers."""
    forced = os.environ.get("FLOWBENCH_ENGINE")
    for name in ([forced] if forced else ["podman", "docker"]):
        path = shutil.which(name) or (name if forced and os.path.exists(name) else None)
        if not path:
            continue
        ENGINE.update(name=os.path.basename(name).split(".")[0], path=path)
        try:
            p = await asyncio.create_subprocess_exec(path, "info", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(p.communicate(), 25)
            ENGINE["ok"] = p.returncode == 0
            ENGINE["detail"] = "ready" if ENGINE["ok"] else (err.decode("utf-8", "replace").strip().splitlines() or ["not running"])[-1][:200]
        except Exception as e:
            ENGINE["ok"], ENGINE["detail"] = False, "%s: %s" % (type(e).__name__, e)
        return ENGINE
    ENGINE.update(name=None, path=None, ok=False, detail="Podman / Docker not installed")
    return ENGINE


def wrap_vars(lang, code, names):
    """Wrap a tier-'vars' snippet so inputs become variables and `result` is written out."""
    ok = [n for n in names if n.isidentifier()]
    if lang in ("javascript", "typescript"):
        head = "const __fs = require('fs');\nconst __in = JSON.parse(__fs.readFileSync(process.env.FLOW_IN, 'utf8'));\n"
        head += "".join("let %s = __in[%s];\n" % (n, json.dumps(n)) for n in ok)
        head += "let result%s = undefined;\n" % (": any" if lang == "typescript" else "")
        tail = "\n;__fs.writeFileSync(process.env.FLOW_RESULT, JSON.stringify(result === undefined ? null : result, (k, v) => typeof v === 'bigint' ? v.toString() : v));\n"
        return head + "// ── your code ──\n" + code + tail
    if lang == "python":
        head = "import json, os\n__in = json.load(open(os.environ['FLOW_IN'], encoding='utf-8'))\n"
        head += "".join("%s = __in[%r]\n" % (n, n) for n in ok) + "result = None\n"
        tail = "\njson.dump(result, open(os.environ['FLOW_RESULT'], 'w', encoding='utf-8'), default=lambda o: o.tolist() if hasattr(o, 'tolist') else repr(o))\n"
        return head + "# ── your code ──\n" + code + tail
    return code


def json_preview(v, text_fallback=None):
    """Preview for values coming back from other languages (plain JSON)."""
    p = {"type": "json " + type(v).__name__ if text_fallback is None else "text"}
    if text_fallback is not None:
        p["text"] = text_fallback[:20000]
        return p
    if isinstance(v, list):
        p["shape"] = [len(v)]
        nums = [x for x in v[:200000] if isinstance(x, (int, float)) and not isinstance(x, bool)]
        if len(v) > 1 and len(nums) == len(v):
            step = max(1, len(v) // 1500)
            p["plot"] = [float(x) for x in v[::step]]
        elif v and all(isinstance(r, dict) for r in v[:200]):
            cols = []
            for r in v[:200]:
                for k in r:
                    if k not in cols:
                        cols.append(k)
            esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            rows = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % esc(r.get(c, "")) for c in cols[:30]) for r in v[:60])
            p["html"] = "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % ("".join("<th>%s</th>" % esc(c) for c in cols[:30]), rows)
    p["text"] = json.dumps(v, indent=1, ensure_ascii=False)[:20000] if not isinstance(v, str) else v[:20000]
    return p


def collect_media(folder):
    out = []
    mimes = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp"}
    import base64
    for f in sorted(Path(folder).glob("*"))[:12]:
        m = mimes.get(f.suffix.lower())
        if m and f.stat().st_size < 8 * 1024 * 1024:
            out.append("data:%s;base64,%s" % (m, base64.b64encode(f.read_bytes()).decode()))
    return out


# ─────────────────────────── terminals ───────────────────────────
def shell_argv(shell, python, image=None, root=None):
    if shell == "container":
        if not ENGINE.get("path"):
            raise RuntimeError("Podman / Docker is not installed (run: flowbench podman)")
        return [ENGINE["path"], "run", "-it", "--rm", "-v", "%s:/code" % root, "-w", "/code", image or "python:3.12-slim",
                "sh", "-c", "command -v bash >/dev/null && exec bash || exec sh"]
    if shell == "python":
        return [python, "-i", "-u"]
    if IS_WIN:
        if shell == "cmd":
            return ["cmd.exe"]
        if shell == "bash":
            b = find_tool("bash")
            if not b:
                raise RuntimeError("bash was not found (install Git for Windows, or use a container terminal)")
            return [b, "--login", "-i"]
        return [shutil.which("pwsh") or "powershell.exe", "-NoLogo"]
    return [os.environ.get("SHELL", "/bin/bash"), "-i"] if shell != "cmd" else ["/bin/sh", "-i"]


class Term:
    def __init__(self, loop, send, tid, argv, cwd, cols, rows):
        self.loop, self.send, self.tid = loop, send, tid
        self.mode, self.alive, self.line = None, True, ""
        if IS_WIN and PtyProcess:
            self.mode = "winpty"
            self.p = PtyProcess.spawn(argv, cwd=cwd, dimensions=(rows, cols))
            threading.Thread(target=self._pump_win, daemon=True).start()
        elif not IS_WIN:
            import fcntl, pty, struct, subprocess, termios
            self.mode = "pty"
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            self.p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, start_new_session=True,
                                      env=dict(os.environ, TERM="xterm-256color"))
            os.close(slave)
            self.fd, self.dec = master, codecs.getincrementaldecoder("utf-8")("replace")
            loop.add_reader(master, self._on_pty)
        else:
            import subprocess
            self.mode = "pipe"  # no pywinpty: basic line-mode terminal
            self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd, bufsize=0)
            self.dec = codecs.getincrementaldecoder("utf-8")("replace")
            threading.Thread(target=self._pump_pipe, daemon=True).start()
            self._emit("\x1b[2m(basic terminal: install pywinpty for full keyboard support)\x1b[0m\r\n")

    def _emit(self, text):
        self.loop.call_soon_threadsafe(self.send, {"type": "term-data", "tid": self.tid, "data": text})

    def _exited(self, code=None):
        if self.alive:
            self.alive = False
            self.loop.call_soon_threadsafe(self.send, {"type": "term-exit", "tid": self.tid, "code": code})

    def _pump_win(self):
        while True:
            try:
                data = self.p.read(8192)
            except EOFError:
                break
            except Exception:
                break
            if data:
                self._emit(data)
            elif not self.p.isalive():
                break
        self._exited(getattr(self.p, "exitstatus", None))

    def _on_pty(self):
        try:
            data = os.read(self.fd, 65536)
        except OSError:
            data = b""
        if not data:
            self.loop.remove_reader(self.fd)
            self._exited(self.p.poll())
            return
        self.send({"type": "term-data", "tid": self.tid, "data": self.dec.decode(data)})

    def _pump_pipe(self):
        while True:
            data = self.p.stdout.read1(65536) if hasattr(self.p.stdout, "read1") else os.read(self.p.stdout.fileno(), 65536)
            if not data:
                break
            self._emit(self.dec.decode(data).replace("\r\n", "\n").replace("\n", "\r\n"))
        self._exited(self.p.wait())

    def write(self, data):
        if not self.alive:
            return
        if self.mode == "winpty":
            self.p.write(data)
        elif self.mode == "pty":
            os.write(self.fd, data.encode("utf-8"))
        else:  # simple line discipline for pipes
            for ch in data:
                if ch in "\r\n":
                    self._emit("\r\n")
                    self.p.stdin.write((self.line + "\n").encode("utf-8")); self.p.stdin.flush(); self.line = ""
                elif ch in "\x7f\b":
                    if self.line:
                        self.line = self.line[:-1]; self._emit("\b \b")
                elif ch == "\x03":
                    self.line = ""; self._emit("^C\r\n")
                elif ch >= " ":
                    self.line += ch; self._emit(ch)

    def resize(self, cols, rows):
        try:
            if self.mode == "winpty":
                self.p.setwinsize(rows, cols)
            elif self.mode == "pty":
                import fcntl, struct, termios
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:
            pass

    def close(self):
        try:
            if self.mode == "winpty":
                self.p.terminate(force=True)
            else:
                self.p.kill()
            if self.mode == "pty":
                self.loop.remove_reader(self.fd); os.close(self.fd)
        except Exception:
            pass
        self.alive = False


# ─────────────────────────── files & functions ───────────────────────────
def seg(src, node):
    return ast.get_source_segment(src, node) if node is not None else None


def scan_file(path):
    src = Path(path).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    out = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
            continue
        a = node.args
        pos = list(a.posonlyargs) + list(a.args)
        defs = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
        params = [{"name": p.arg, "default": seg(src, d), "ann": seg(src, p.annotation)} for p, d in zip(pos, defs)]
        params += [{"name": p.arg, "default": seg(src, d), "ann": seg(src, p.annotation), "kwonly": True} for p, d in zip(a.kwonlyargs, a.kw_defaults)]
        out.append({"name": node.name, "params": params, "doc": ast.get_docstring(node) or "", "ret": seg(src, node.returns), "line": node.lineno,
                    "varargs": bool(a.vararg or a.kwarg)})
    return out


class Session:
    def __init__(self, ws, cfg):
        self.ws, self.cfg, self.authed = ws, cfg, False
        self.loop = asyncio.get_running_loop()
        self.python = cfg.python
        self.kernel = Kernel(self.python, str(cfg.roots[0]), self._kernel_event)
        self.terms, self.sig, self.running, self.run_task = {}, {}, False, None
        self.index_cache = {}
        self.values, self.media, self.where = {}, {}, {}   # results of non-Python nodes; where each result lives
        self.procs, self.containers = set(), set()

    def send(self, obj):
        asyncio.ensure_future(self._send(obj))

    async def _send(self, obj):
        try:
            await self.ws.send(json.dumps(obj, default=str))
        except Exception:
            pass

    def _kernel_event(self, ev):
        if ev.get("ev") == "stream":
            self.send({"type": "stream", "nid": ev.get("nid"), "name": ev.get("name"), "text": ev.get("text", "")})

    def safe(self, p):
        rp = Path(p).expanduser().resolve()
        for r in self.cfg.roots:
            if rp == r or r in rp.parents:
                return rp
        raise PermissionError("Outside the allowed folders: " + str(rp))

    def langs(self):
        return {k: bool(find_tool(v["tool"])) for k, v in LANGS.items()}

    def engine_info(self):
        return {"name": ENGINE.get("name"), "ok": ENGINE.get("ok"), "detail": ENGINE.get("detail")}

    async def handle(self, msg):
        t, rid = msg.get("type"), msg.get("id")
        reply = lambda **kw: self.send(dict(re=rid, **kw))
        if not self.authed:
            if t == "hello" and secrets.compare_digest(str(msg.get("token", "")), self.cfg.token):
                self.authed = True
                reply(ok=True, version=VERSION, python=self.python, pyversion=self.cfg.pyversion, platform=sys.platform,
                      roots=[str(r) for r in self.cfg.roots], fullterm=bool(PtyProcess) or not IS_WIN,
                      langs=self.langs(), engine=self.engine_info())
            else:
                await self._send({"re": rid, "ok": False, "error": "Wrong token"})
                await self.ws.close(1008, "unauthorized")
            return
        try:
            if t == "ls":
                d = self.safe(msg.get("path") or self.cfg.roots[0])
                items = []
                for e in sorted(d.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
                    if e.name.startswith(".") or e.name in SKIP_DIRS:
                        continue
                    lang = EXT_LANG.get(e.suffix.lower())
                    if e.is_dir() or e.suffix == ".py" or lang:
                        items.append({"name": e.name, "path": str(e), "dir": e.is_dir(), "lang": None if e.is_dir() or e.suffix == ".py" else lang})
                reply(ok=True, path=str(d), items=items)
            elif t == "scan":
                p = self.safe(msg["path"])
                reply(ok=True, path=str(p), mtime=p.stat().st_mtime, funcs=scan_file(p))
            elif t == "read":
                p = self.safe(msg["path"])
                reply(ok=True, path=str(p), text=p.read_text(encoding="utf-8", errors="replace")[:400000])
            elif t == "index":
                reply(ok=True, files=await asyncio.to_thread(self.build_index))
            elif t == "engine":
                await detect_engine()
                reply(ok=True, engine=self.engine_info(), langs=self.langs())
            elif t == "flows":
                reply(ok=True, flows=await asyncio.to_thread(self.list_flows))
            elif t == "save-flow":
                if msg.get("path"):
                    target = self.safe(msg["path"])
                else:
                    name = "".join(c for c in str(msg.get("name") or "workflow") if c.isalnum() or c in " -_.").strip(" .") or "workflow"
                    target = self.cfg.roots[0] / "flows" / (name + ".flow.json")
                    self.safe(target)
                if not target.name.endswith(".flow.json"):
                    raise ValueError("Workflow files must end with .flow.json")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(msg.get("data", {}), indent=1, ensure_ascii=False), encoding="utf-8")
                reply(ok=True, path=str(target))
            elif t == "load-flow":
                p = self.safe(msg["path"])
                reply(ok=True, path=str(p), data=json.loads(p.read_text(encoding="utf-8")))
            elif t == "run":
                if self.running:
                    return reply(ok=False, error="A run is already in progress")
                self.run_task = asyncio.create_task(self.run_graph(msg, rid))
            elif t == "stop":
                self.stop_all()
                reply(ok=True)
            elif t == "reset":
                self.stop_all()
                if msg.get("python"):
                    self.python = self.kernel.python = msg["python"]
                reply(ok=True, python=self.python)
            elif t == "term-open":
                tid = msg["tid"]
                cwd = str(self.safe(msg.get("cwd") or self.cfg.roots[0]))
                argv = shell_argv(msg.get("shell", "default"), self.python, msg.get("image"), cwd)
                self.terms[tid] = Term(self.loop, self.send, tid, argv, cwd, int(msg.get("cols", 80)), int(msg.get("rows", 24)))
                reply(ok=True, mode=self.terms[tid].mode, cwd=cwd)
            elif t == "term-input":
                term = self.terms.get(msg["tid"])
                if term:
                    term.write(msg.get("data", ""))
            elif t == "term-resize":
                term = self.terms.get(msg["tid"])
                if term:
                    term.resize(int(msg["cols"]), int(msg["rows"]))
            elif t == "term-close":
                term = self.terms.pop(msg["tid"], None)
                if term:
                    term.close()
            else:
                reply(ok=False, error="Unknown request: %s" % t)
        except Exception as e:
            reply(ok=False, error="%s: %s" % (type(e).__name__, e))

    def stop_all(self):
        self.kernel.kill()
        self.sig.clear(); self.where.clear(); self.values.clear(); self.media.clear()
        for p in list(self.procs):
            try:
                p.kill()
            except Exception:
                pass
        self.procs.clear()
        if ENGINE.get("path"):
            for c in list(self.containers):
                asyncio.ensure_future(asyncio.create_subprocess_exec(ENGINE["path"], "rm", "-f", c,
                                                                     stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL))
        self.containers.clear()

    def list_flows(self):
        found = []
        for root in self.cfg.roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
                if len(Path(dirpath).relative_to(root).parts) >= 4:
                    dirnames[:] = []
                for fn in filenames:
                    if fn.endswith(".flow.json"):
                        p = os.path.join(dirpath, fn)
                        found.append({"path": p, "name": fn[:-10], "mtime": os.path.getmtime(p)})
                if len(found) > 300:
                    break
        return sorted(found, key=lambda f: -f["mtime"])

    def build_index(self):
        files, count = [], 0
        for root in self.cfg.roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
                if len(Path(dirpath).relative_to(root).parts) > 6:
                    dirnames[:] = []
                for fn in filenames:
                    if not fn.endswith(".py"):
                        continue
                    p = os.path.join(dirpath, fn)
                    count += 1
                    if count > 4000:
                        return files
                    try:
                        mt = os.path.getmtime(p)
                        hit = self.index_cache.get(p)
                        if not hit or hit[0] != mt:
                            hit = (mt, [f["name"] for f in scan_file(p)])
                            self.index_cache[p] = hit
                        if hit[1]:
                            files.append({"path": p, "funcs": hit[1]})
                    except Exception:
                        continue
        return files

    @staticmethod
    def is_ext(n):
        d = n.get("data", {})
        return n.get("kind") == "code" and ((d.get("lang") or "python") != "python" or bool(d.get("container")))

    async def run_graph(self, msg, rid):
        self.running = True
        try:
            nodes = {n["id"]: n for n in msg.get("nodes", [])}
            runnable = {i for i, n in nodes.items() if n.get("kind") in ("func", "code", "value", "viewer")}
            ins = defaultdict(dict)
            for e in msg.get("edges", []):
                if e["from"] in runnable and e["to"] in runnable:
                    ins[e["to"]][e["port"]] = e["from"]
            targets = [t for t in (msg.get("targets") or runnable) if t in runnable]
            need, stack = set(), list(targets)
            while stack:
                x = stack.pop()
                if x not in need:
                    need.add(x); stack.extend(ins[x].values())
            indeg = {n: sum(1 for s in ins[n].values() if s in need) for n in need}
            outs = defaultdict(list)
            for n in need:
                for s in ins[n].values():
                    outs[s].append(n)
            order, ready = [], sorted(n for n in need if indeg[n] == 0)
            while ready:
                n = ready.pop(0); order.append(n)
                for m in outs[n]:
                    indeg[m] -= 1
                    if indeg[m] == 0:
                        ready.append(m)
            if len(order) != len(need):
                self.send({"type": "run-done", "re": rid, "ok": False, "error": "The workflow has a loop (cycle). Remove one of the wires."})
                return
            force = bool(msg.get("force"))
            ok, failed, running = set(), set(), {}
            remaining = list(order)
            klock, sem = asyncio.Lock(), asyncio.Semaphore(4)
            # independent branches run in parallel; Python nodes share one kernel, so they take turns
            while remaining or running:
                for nid in list(remaining):
                    deps = list(ins[nid].values())
                    if any(d in failed for d in deps):
                        remaining.remove(nid); failed.add(nid); self.send({"type": "node", "nid": nid, "status": "skipped"})
                    elif all(d in ok for d in deps):
                        remaining.remove(nid)
                        running[nid] = asyncio.create_task(self.run_node(nid, nodes[nid], ins[nid], force, klock, sem))
                if not running:
                    break
                done, _ = await asyncio.wait(list(running.values()), return_when=asyncio.FIRST_COMPLETED)
                for nid, task in list(running.items()):
                    if task in done:
                        running.pop(nid)
                        try:
                            good = task.result()
                        except Exception as e:
                            good = False
                            self.send({"type": "node", "nid": nid, "status": "error", "error": "%s: %s" % (type(e).__name__, e)})
                        (ok if good else failed).add(nid)
            self.send({"type": "run-done", "re": rid, "ok": not failed, "failed": len(failed)})
        except Exception as e:
            self.send({"type": "run-done", "re": rid, "ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        finally:
            self.running = False

    async def run_node(self, nid, n, ins_n, force, klock, sem):
        d = n.get("data", {})
        kind = n["kind"]
        ext = self.is_ext(n)
        payload, extra = {"nid": nid, "kind": kind, "args": {}}, ""
        if ext:
            code = d.get("code", "")
            if d.get("file"):
                fp = self.safe(d["file"])
                code = fp.read_text(encoding="utf-8", errors="replace")
                extra = str(fp.stat().st_mtime)
            payload.update(kind="ext", lang=d.get("lang") or "python", code=code, fromfile=bool(d.get("file")), inputs=d.get("inputs", ""), values=d.get("values", {}),
                           container=bool(d.get("container")), image=d.get("image") or "")
        elif kind == "func":
            fpath = self.safe(d["file"])
            payload.update(file=str(fpath), func=d["func"])
            extra = str(fpath.stat().st_mtime)
            for p in d.get("params", []):
                name = p["name"]
                if name in ins_n:
                    payload["args"][name] = {"ref": ins_n[name]}
                elif str(d.get("values", {}).get(name, "")).strip():
                    payload["args"][name] = {"expr": d["values"][name]}
        elif kind == "code":
            payload["code"] = d.get("code", "")
            for name in [s.strip() for s in d.get("inputs", "").split(",") if s.strip()]:
                payload["args"][name] = {"ref": ins_n[name]} if name in ins_n else {"expr": d.get("values", {}).get(name, "") or "None"}
        elif kind == "value":
            payload["expr"] = d.get("expr", "")
        elif "data" in ins_n:
            payload["args"]["data"] = {"ref": ins_n["data"]}
        sig = hashlib.sha1(json.dumps([payload, extra, [self.sig.get(s) for _, s in sorted(ins_n.items())]], sort_keys=True).encode()).hexdigest()
        if not force and self.sig.get(nid) == sig and nid in self.where:
            self.send({"type": "node", "nid": nid, "status": "cached"})
            return True
        self.send({"type": "node", "nid": nid, "status": "running"})
        t0 = time.perf_counter()
        # a viewer showing a non-Python result needs no kernel
        if kind == "viewer" and self.where.get(ins_n.get("data")) == "ext":
            src = ins_n["data"]
            v, media = self.values.get(src), self.media.get(src, [])
            self.values[nid], self.media[nid], self.where[nid] = v, media, "ext"
            pv = json_preview(None, v) if isinstance(v, str) else json_preview(v)
            if media:
                pv["images"] = media
            self.sig[nid] = sig
            self.send({"type": "node", "nid": nid, "status": "ok", "ms": 0.1, "preview": pv})
            return True
        if not ext:
            async with klock:
                for src in ins_n.values():
                    if self.where.get(src) == "ext":
                        await self.kernel.put(src, self.values.get(src), self.media.get(src))
                try:
                    res = await self.kernel.run(payload)
                except Exception as e:
                    res = {"ok": False, "error": str(e), "ms": 0}
            if res.get("ok"):
                self.sig[nid] = sig
                self.where[nid] = "kernel"
                self.send({"type": "node", "nid": nid, "status": "ok", "ms": res.get("ms"), "preview": res.get("preview")})
                return True
            self.sig.pop(nid, None)
            self.where.pop(nid, None)
            if "kernel stopped" in str(res.get("error", "")):
                self.sig.clear()
                self.where = {k: v for k, v in self.where.items() if v == "ext"}
            self.send({"type": "node", "nid": nid, "status": "error", "ms": res.get("ms"), "error": res.get("error")})
            return False
        # another language (or a container)
        async with sem:
            inputs = {}
            names = [s.strip() for s in d.get("inputs", "").split(",") if s.strip()]
            for name in names:
                if name in ins_n:
                    src = ins_n[name]
                    if self.where.get(src) == "ext":
                        inputs[name] = self.values.get(src)
                    else:
                        async with klock:
                            r = await self.kernel.export(src)
                        if not r.get("ok"):
                            raise RuntimeError("Could not pass '%s' on: %s" % (name, r.get("error")))
                        inputs[name] = r["value"]
                else:
                    raw = str(d.get("values", {}).get(name, "")).strip()
                    try:
                        inputs[name] = json.loads(raw) if raw else None
                    except Exception:
                        inputs[name] = raw
            res = await self.run_ext(nid, payload, inputs)
        res["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        if not res["ok"]:
            self.sig.pop(nid, None)
            self.where.pop(nid, None)
            self.send({"type": "node", "nid": nid, "status": "error", "ms": res["ms"], "error": res["error"]})
            return False
        self.values[nid], self.media[nid], self.where[nid] = res["value"], res["media"], "ext"
        pv = json_preview(None, res["value"]) if res.get("is_text") else json_preview(res["value"])
        if res["media"]:
            pv["images"] = res["media"]
        self.sig[nid] = sig
        self.send({"type": "node", "nid": nid, "status": "ok", "ms": res["ms"], "preview": pv})
        return True

    async def run_ext(self, nid, p, inputs):
        lang = p["lang"] if p["lang"] in LANGS else "python"
        L = LANGS[lang]
        container = p["container"] or lang == "shell"
        work = Path.home() / ".flowbench" / "work" / nid
        shutil.rmtree(work, ignore_errors=True)
        (work / "out").mkdir(parents=True, exist_ok=True)
        src_name = L.get("name") or ("main" + L["ext"])
        tier = "prog" if p.get("fromfile") else L["tier"]  # a script file: whatever it prints is the result
        code = wrap_vars(lang, p["code"], list(inputs)) if tier == "vars" else p["code"]
        (work / src_name).write_text(code, encoding="utf-8")
        (work / "inputs.json").write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
        steps = []
        binp = None
        if container:
            if not ENGINE.get("ok"):
                await detect_engine()
            if not ENGINE.get("ok"):
                return {"ok": False, "error": "Containers aren't ready (%s).\nSet them up once with:  flowbench podman" % ENGINE.get("detail")}
            cname = "flowbench-%s-%s" % (nid, secrets.token_hex(3))
            self.containers.add(cname)
            argv = [ENGINE["path"], "run", "--rm", "-i", "--name", cname, "-v", "%s:/work" % work, "-v", "%s:/code:ro" % self.cfg.roots[0], "-w", "/work",
                    "-e", "FLOW_IN=/work/inputs.json", "-e", "FLOW_OUT=/work/out", "-e", "FLOW_RESULT=/work/result.json",
                    p["image"] or L["image"], "sh", "-c", L["crun"].format(src="/work/" + src_name)]
            steps.append(("run", argv))
        else:
            tool = self.python if lang == "python" else find_tool(L["tool"])
            if not tool:
                return {"ok": False, "error": "%s isn't installed on this computer.\nTick CONTAINER on this node to run it with Podman instead." % lang}
            build_dir = Path.home() / ".flowbench" / "build"
            build_dir.mkdir(parents=True, exist_ok=True)
            binp = build_dir / (hashlib.sha1((lang + "\0" + code).encode()).hexdigest()[:16] + EXE)
            fmt = lambda a: [x.format(src=str(work / src_name), bin=str(binp), tool=tool) for x in a]
            if L.get("build") and not binp.exists():
                b = fmt(L["build"])
                b[0] = find_tool(b[0]) or b[0]
                steps.append(("build", b))
            run = fmt(L["run"] or ["{tool}", "{src}"])
            if run[0] == L["tool"]:
                run[0] = tool
            steps.append(("run", run))
        env = dict(os.environ, FLOW_IN=str(work / "inputs.json"), FLOW_OUT=str(work / "out"), FLOW_RESULT=str(work / "result.json"),
                   PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        stdout_text = ""
        for step, argv in steps:
            try:
                rc, out, err = await self.run_proc(nid, argv, work, env, stream_stdout=(step == "build" or tier == "vars"))
            except FileNotFoundError as e:
                return {"ok": False, "error": "Could not start %s: %s" % (argv[0], e)}
            except OSError as e:
                if getattr(e, "winerror", None) in (4551, 1260, 225):
                    return {"ok": False, "error": ("Windows blocked the compiled program (Smart App Control / application control policy "
                                                   "doesn't allow new unsigned .exe files).\nTick CONTAINER on this node to build and run it "
                                                   "in Podman instead, or allow the file in Windows Security.")}
                return {"ok": False, "error": "Could not start %s: %s" % (argv[0], e)}
            if rc != 0:
                if step == "build" and binp is not None:
                    try:
                        binp.unlink()
                    except Exception:
                        pass
                tail = (err or out)[-4000:]
                return {"ok": False, "error": "%s failed (exit code %s)\n%s" % ("Compiling" if step == "build" else "The program", rc, tail)}
            if step == "run":
                stdout_text = out
        media = collect_media(work / "out")
        if tier == "vars":
            rf = work / "result.json"
            value = json.loads(rf.read_text(encoding="utf-8")) if rf.exists() else None
            return {"ok": True, "value": value, "media": media}
        s = stdout_text.strip()
        if not s:
            return {"ok": True, "value": None, "media": media}
        try:
            return {"ok": True, "value": json.loads(s), "media": media}
        except Exception:
            return {"ok": True, "value": stdout_text, "media": media, "is_text": True}

    async def run_proc(self, nid, argv, cwd, env, stream_stdout):
        proc = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd), env=env, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.procs.add(proc)
        buf = {"stdout": [], "stderr": []}

        async def pump(stream, name):
            dec = codecs.getincrementaldecoder("utf-8")("replace")
            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    break
                text = dec.decode(chunk)
                buf[name].append(text)
                if name == "stderr" or stream_stdout:
                    self.send({"type": "stream", "nid": nid, "name": name, "text": text})
        await asyncio.gather(pump(proc.stdout, "stdout"), pump(proc.stderr, "stderr"))
        rc = await proc.wait()
        self.procs.discard(proc)
        return rc, "".join(buf["stdout"]), "".join(buf["stderr"])

    def cleanup(self):
        for term in self.terms.values():
            term.close()
        self.terms.clear()
        self.stop_all()


# ─────────────────────────── serve the web app locally ───────────────────────────
# Browsers increasingly block public https sites from talking to programs on your own
# computer, so the runner also serves the Flowbench page at http://127.0.0.1:<port>/.
# The files are fetched from the Flowbench site and cached for offline use.
ASSET_TYPES = {"/index.html": "text/html; charset=utf-8", "/app.js": "text/javascript; charset=utf-8",
               "/style.css": "text/css; charset=utf-8", "/runner.py": "text/x-python; charset=utf-8"}
ASSET_CACHE = {}


def get_asset(path):
    if path in ASSET_CACHE:
        return ASSET_CACHE[path]
    import urllib.request
    cache_file = Path.home() / ".flowbench" / "app" / path.lstrip("/")
    try:
        req = urllib.request.Request(APP_URL.rstrip("/") + path, headers={"User-Agent": "flowbench-runner/" + VERSION})
        with urllib.request.urlopen(req, timeout=8) as r:
            body = r.read()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(body)
    except Exception:
        if not cache_file.exists():
            return None
        body = cache_file.read_bytes()
    ASSET_CACHE[path] = body
    return body


def http_response(path):
    path = path.split("?", 1)[0].split("#", 1)[0]
    if path == "/":
        path = "/index.html"
    if path not in ASSET_TYPES:
        return 404, "text/plain; charset=utf-8", b"Not found"
    body = get_asset(path)
    if body is None:
        return 502, "text/plain; charset=utf-8", ("Could not download the Flowbench app (no internet?). Open %s instead." % APP_URL).encode()
    return 200, ASSET_TYPES[path], body


def origin_ok(origin, cfg):
    if not origin:
        return True  # non-browser clients still need the token
    if origin in cfg.origins:
        return True
    for local in ("http://localhost", "http://127.0.0.1", "https://localhost"):
        if origin == local or origin.startswith(local + ":"):
            return True
    return False


def load_token(renew):
    d = Path.home() / ".flowbench"
    d.mkdir(exist_ok=True)
    f = d / "token"
    if renew or not f.exists():
        f.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        try:
            os.chmod(f, 0o600)
        except Exception:
            pass
    return f.read_text(encoding="utf-8").strip()


EXAMPLES = {
    "signals.py": '''"""Example modules for Flowbench: signals."""
import numpy as np


def make_signal(freq: float = 5.0, noise: float = 0.3, n: int = 500):
    """A noisy sine wave sampled over one second."""
    t = np.linspace(0, 1, n)
    return np.sin(2 * np.pi * freq * t) + noise * np.random.randn(n)


def moving_average(x, window: int = 15):
    """Smooth a signal with a moving average."""
    return np.convolve(x, np.ones(window) / window, mode="same")


def spectrum(x):
    """Magnitude of the FFT (first half)."""
    return np.abs(np.fft.rfft(x))


def plot_compare(raw, smooth, title: str = "raw vs smoothed"):
    """Plot two signals on top of each other."""
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.plot(raw, lw=0.8, alpha=0.6, label="raw")
    ax.plot(smooth, lw=2, label="smoothed")
    ax.set_title(title)
    ax.legend()
    return fig
''',
    "stats.py": '''"""Example modules for Flowbench: statistics."""


def describe(x):
    """Basic statistics of a sequence."""
    import numpy as np
    x = np.asarray(x, dtype=float)
    return {"n": int(x.size), "mean": float(x.mean()), "std": float(x.std()), "min": float(x.min()), "max": float(x.max())}


def to_table(x, name: str = "value"):
    """Turn a sequence into a pandas DataFrame (needs pandas)."""
    import pandas as pd
    return pd.DataFrame({name: list(x)})
''',
}


async def main():
    ap = argparse.ArgumentParser(description="Flowbench local runner")
    ap.add_argument("--root", action="append", help="Folder(s) Flowbench may read. Default: current folder")
    ap.add_argument("--config", help="JSON file with roots / python / port (written by the installer)")
    ap.add_argument("--port", type=int, default=None, help="Default 8765")
    ap.add_argument("--python", default=None, help="Python interpreter used to run your code (default: this one)")
    ap.add_argument("--allow-origin", action="append", default=[], help="Extra allowed web origin")
    ap.add_argument("--new-token", action="store_true", help="Generate a new secret token")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--examples", action="store_true", help="Write example modules into the first root")
    cfg = ap.parse_args()
    conf = {}
    if cfg.config and Path(cfg.config).exists():
        conf = json.loads(Path(cfg.config).read_text(encoding="utf-8-sig"))
    cfg.port = cfg.port or int(conf.get("port") or 8765)
    cfg.python = cfg.python or conf.get("python") or sys.executable
    cfg.allow_origin += conf.get("allow_origin", [])
    roots = [Path(r).expanduser().resolve() for r in (cfg.root or conf.get("roots") or [os.getcwd()])]
    cfg.roots = [r for r in roots if r.is_dir()]
    for r in roots:
        if not r.is_dir():
            print("  Skipping missing folder:", r)
    if not cfg.roots:
        print("No valid folders. Pass --root or run:  flowbench add <folder>"); sys.exit(1)
    cfg.token = load_token(cfg.new_token)
    link = "http://127.0.0.1:%d/#token=%s" % (cfg.port, cfg.token)

    # single instance: if a runner already listens on this port, just open the page
    import socket
    try:
        socket.create_connection(("127.0.0.1", cfg.port), timeout=0.5).close()
        print("Flowbench is already running on port %d. Opening it." % cfg.port)
        if not cfg.no_browser:
            webbrowser.open(link)
        return
    except OSError:
        pass

    import subprocess
    try:
        cfg.pyversion = subprocess.run([cfg.python, "-c", "import sys; print(sys.version.split()[0])"], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        cfg.pyversion = ""
    if not cfg.pyversion:
        print("Can't start the Python interpreter:", cfg.python); sys.exit(1)

    pid_file = Path.home() / ".flowbench" / "runner.pid"
    pid_file.write_text(json.dumps({"pid": os.getpid(), "port": cfg.port}), encoding="utf-8")
    import atexit
    atexit.register(lambda: pid_file.unlink(missing_ok=True))
    if cfg.examples:
        ex = cfg.roots[0] / "flowbench_examples"
        ex.mkdir(exist_ok=True)
        for name, src in EXAMPLES.items():
            if not (ex / name).exists():
                (ex / name).write_text(src, encoding="utf-8")
        print("Example modules written to", ex)
    cfg.origins = {APP_URL.rstrip("/")} | {o.rstrip("/") for o in cfg.allow_origin}

    async def handler(ws, *_):
        headers = ws.request.headers if NEW_API else ws.request_headers
        if not origin_ok(headers.get("Origin"), cfg):
            await ws.close(1008, "origin not allowed")
            return
        sess = Session(ws, cfg)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                await sess.handle(msg)
        except Exception:
            pass
        finally:
            sess.cleanup()

    url = "ws://127.0.0.1:%d" % cfg.port

    if NEW_API:
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        def process_request(connection, request):
            if request.headers.get("Upgrade", "").lower() == "websocket":
                return None
            status, ctype, body = http_response(request.path)
            return Response(status, "OK" if status == 200 else "Error",
                            Headers([("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-cache")]), body)
    else:
        def process_request(path, headers):
            if headers.get("Upgrade", "").lower() == "websocket":
                return None
            status, ctype, body = http_response(path)
            return status, [("Content-Type", ctype), ("Content-Length", str(len(body)))], body

    asyncio.create_task(detect_engine())
    async with ws_serve(handler, "127.0.0.1", cfg.port, max_size=64 * 1024 * 1024, ping_interval=20, process_request=process_request):
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:
            pass
        print("\n  FLOWBENCH runner v%s" % VERSION)
        print("  Python   : %s (%s)" % (cfg.python, cfg.pyversion))
        print("  Folders  : %s" % "\n             ".join(str(r) for r in cfg.roots))
        print("  Listening: %s  (this computer only)" % url)
        print("  Terminal : %s" % ("full" if (PtyProcess or not IS_WIN) else "basic  (pip install pywinpty for a full terminal)"))
        print("  Token    : %s" % cfg.token)
        print("\n  Open Flowbench (connects automatically):\n  %s\n" % link)
        print("  (Or open %s and paste the token.)\n" % APP_URL)
        print("  Keep this window open. Press Ctrl+C to stop.\n")
        if not cfg.no_browser:
            try:
                webbrowser.open(link)
            except Exception:
                pass
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nRunner stopped.")
