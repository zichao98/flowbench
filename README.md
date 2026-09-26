# Flowbench

An infinite canvas for your own Python code: drag functions from local folders onto the canvas, wire them together like n8n, run the workflow, see plots / tables / arrays inline, and open many real terminals side by side to check results in parallel.

**App:** https://flowbench.zichaoleng55.workers.dev

## How it works

- `public/` — the web app (static, deployed to Cloudflare Workers). No build step.
- `public/runner.py` — a small local runner you start on your computer. The web app talks to it over a WebSocket on `127.0.0.1`.

```bash
pip install websockets pywinpty      # pywinpty is Windows-only (full terminals)
python runner.py --root "C:\path\to\your\code" --examples
```

The runner prints a link with a token and opens the app, which connects automatically.

## Nodes

| Node | Does |
| --- | --- |
| Function | Any top-level `def` in your `.py` files. Parameters become inputs (wire them, or type a Python expression); the return value is the output. |
| Value | A Python expression (`42`, `[1, 2]`, `np.linspace(0, 1, 50)`). |
| Code | A snippet; inputs become variables, assign the output to `result`. |
| Viewer | Big preview of whatever is wired in, including its plots. |
| Terminal | A real shell (PowerShell / cmd / Python / bash). |
| Note | A sticky note. |

Matplotlib figures, pandas tables, numpy arrays (with an inline line chart for 1-D data) and PIL images are rendered inside the nodes. Results are cached: re-running only recomputes nodes whose inputs, code or source file changed.

## Security

The runner binds to `127.0.0.1` only, requires a secret token (stored in `~/.flowbench/token`, new one with `--new-token`), accepts browser connections only from the Flowbench site or localhost, and only reads files inside the `--root` folders.
