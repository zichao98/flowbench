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

VERSION = "1.0"
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

for line in sys.stdin:
    try: msg = json.loads(line)
    except Exception: continue
    if msg.get("cmd") == "forget":
        for k in msg.get("nids", []): RESULTS.pop(k, None)
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
            if ev.get("ev") == "done":
                fut = self.pending.pop(ev["nid"], None)
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
        await self.ensure()
        fut = asyncio.get_running_loop().create_future()
        self.pending[payload["nid"]] = fut
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


# ─────────────────────────── terminals ───────────────────────────
def shell_argv(shell, python):
    if shell == "python":
        return [python, "-i", "-u"]
    if IS_WIN:
        if shell == "cmd":
            return ["cmd.exe"]
        if shell == "bash":
            b = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
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

    async def handle(self, msg):
        t, rid = msg.get("type"), msg.get("id")
        reply = lambda **kw: self.send(dict(re=rid, **kw))
        if not self.authed:
            if t == "hello" and secrets.compare_digest(str(msg.get("token", "")), self.cfg.token):
                self.authed = True
                reply(ok=True, version=VERSION, python=self.python, pyversion=sys.version.split()[0], platform=sys.platform,
                      roots=[str(r) for r in self.cfg.roots], fullterm=bool(PtyProcess) or not IS_WIN)
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
                    if e.is_dir() or e.suffix == ".py":
                        items.append({"name": e.name, "path": str(e), "dir": e.is_dir()})
                reply(ok=True, path=str(d), items=items)
            elif t == "scan":
                p = self.safe(msg["path"])
                reply(ok=True, path=str(p), mtime=p.stat().st_mtime, funcs=scan_file(p))
            elif t == "read":
                p = self.safe(msg["path"])
                reply(ok=True, path=str(p), text=p.read_text(encoding="utf-8", errors="replace")[:400000])
            elif t == "index":
                reply(ok=True, files=await asyncio.to_thread(self.build_index))
            elif t == "run":
                if self.running:
                    return reply(ok=False, error="A run is already in progress")
                self.run_task = asyncio.create_task(self.run_graph(msg, rid))
            elif t == "stop":
                self.kernel.kill(); self.sig.clear()
                reply(ok=True)
            elif t == "reset":
                self.kernel.kill(); self.sig.clear()
                if msg.get("python"):
                    self.python = self.kernel.python = msg["python"]
                reply(ok=True, python=self.python)
            elif t == "term-open":
                tid = msg["tid"]
                cwd = str(self.safe(msg.get("cwd") or self.cfg.roots[0]))
                argv = shell_argv(msg.get("shell", "default"), self.python)
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

    def build_index(self):
        files, count = [], 0
        for root in self.cfg.roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
                if Path(dirpath).relative_to(root).parts.__len__() > 6:
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
            failed, force = set(), bool(msg.get("force"))
            for nid in order:
                n, d = nodes[nid], nodes[nid].get("data", {})
                if any(s in failed for s in ins[nid].values()):
                    failed.add(nid); self.send({"type": "node", "nid": nid, "status": "skipped"}); continue
                payload = {"nid": nid, "kind": n["kind"], "args": {}}
                extra = ""
                if n["kind"] == "func":
                    fpath = self.safe(d["file"])
                    payload.update(file=str(fpath), func=d["func"]); extra = str(fpath.stat().st_mtime)
                    for p in d.get("params", []):
                        name = p["name"]
                        if name in ins[nid]:
                            payload["args"][name] = {"ref": ins[nid][name]}
                        elif str(d.get("values", {}).get(name, "")).strip():
                            payload["args"][name] = {"expr": d["values"][name]}
                elif n["kind"] == "code":
                    payload["code"] = d.get("code", "")
                    for name in [s.strip() for s in d.get("inputs", "").split(",") if s.strip()]:
                        payload["args"][name] = {"ref": ins[nid][name]} if name in ins[nid] else {"expr": d.get("values", {}).get(name, "") or "None"}
                elif n["kind"] == "value":
                    payload["expr"] = d.get("expr", "")
                elif "data" in ins[nid]:
                    payload["args"]["data"] = {"ref": ins[nid]["data"]}
                sig = hashlib.sha1(json.dumps([payload, extra, [self.sig.get(s) for _, s in sorted(ins[nid].items())]], sort_keys=True).encode()).hexdigest()
                if not force and self.sig.get(nid) == sig:
                    self.send({"type": "node", "nid": nid, "status": "cached"}); continue
                self.send({"type": "node", "nid": nid, "status": "running"})
                try:
                    res = await self.kernel.run(payload)
                except Exception as e:
                    res = {"ok": False, "error": str(e), "ms": 0}
                if res.get("ok"):
                    self.sig[nid] = sig
                    self.send({"type": "node", "nid": nid, "status": "ok", "ms": res.get("ms"), "preview": res.get("preview")})
                else:
                    self.sig.pop(nid, None); failed.add(nid)
                    self.send({"type": "node", "nid": nid, "status": "error", "ms": res.get("ms"), "error": res.get("error")})
                    if "kernel stopped" in str(res.get("error", "")):
                        self.sig.clear(); break
            self.send({"type": "run-done", "re": rid, "ok": not failed, "failed": len(failed)})
        except Exception as e:
            self.send({"type": "run-done", "re": rid, "ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        finally:
            self.running = False

    def cleanup(self):
        for term in self.terms.values():
            term.close()
        self.terms.clear()
        self.kernel.kill()


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
        with urllib.request.urlopen(APP_URL.rstrip("/") + path, timeout=8) as r:
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
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--python", default=sys.executable, help="Python interpreter used to run your code")
    ap.add_argument("--allow-origin", action="append", default=[], help="Extra allowed web origin")
    ap.add_argument("--new-token", action="store_true", help="Generate a new secret token")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--examples", action="store_true", help="Write example modules into the first root")
    cfg = ap.parse_args()
    cfg.roots = [Path(r).expanduser().resolve() for r in (cfg.root or [os.getcwd()])]
    for r in cfg.roots:
        if not r.is_dir():
            print("Not a folder:", r); sys.exit(1)
    if cfg.examples:
        ex = cfg.roots[0] / "flowbench_examples"
        ex.mkdir(exist_ok=True)
        for name, src in EXAMPLES.items():
            if not (ex / name).exists():
                (ex / name).write_text(src, encoding="utf-8")
        print("Example modules written to", ex)
    cfg.origins = {APP_URL.rstrip("/")} | {o.rstrip("/") for o in cfg.allow_origin}
    cfg.token = load_token(cfg.new_token)

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

    async with ws_serve(handler, "127.0.0.1", cfg.port, max_size=64 * 1024 * 1024, ping_interval=20, process_request=process_request):
        link = "http://127.0.0.1:%d/#token=%s" % (cfg.port, cfg.token)
        sys.stdout.reconfigure(line_buffering=True)
        print("\n  FLOWBENCH runner v%s" % VERSION)
        print("  Python   : %s (%s)" % (cfg.python, sys.version.split()[0]))
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
