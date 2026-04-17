from typing import List
#!/usr/bin/env python3
import argparse
import bz2
import gzip
import html
import io
import json
import lzma
import os
import re
import sys
import tarfile
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse


SECRET_PASSWORD = "cn@dD7kfLN$tvM4w"
ARCHIVE_EXTENSIONS = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tgz",
    ".tbz2",
    ".txz",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
)

WEB_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>DokuWiki Attic Search</title>
  <style>
    :root {
      --bg: #0f172a;
      --panel: #111827;
      --ink: #e5e7eb;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --line: #1f2937;
      --warn: #f59e0b;
      --err: #ef4444;
      --note: #22d3ee;
      --source: #a78bfa;
      --hit: #22c55e;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: radial-gradient(circle at top, #1e293b, #020617 60%);
      color: var(--ink);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .wrap {
      max-width: 1100px;
      margin: 32px auto;
      padding: 0 16px;
      display: grid;
      grid-template-columns: 320px 1fr;
      gap: 16px;
    }
    .panel {
      background: color-mix(in srgb, var(--panel) 90%, black 10%);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
    }
    h1 {
      margin: 0 0 12px;
      font-size: 16px;
    }
    label {
      display: block;
      margin: 10px 0 6px;
      color: var(--muted);
      font-size: 13px;
    }
    input[type=text], input[type=number], input[type=password] {
      width: 100%;
      background: #0b1020;
      color: var(--ink);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 10px;
    }
    .row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 10px;
      font-size: 13px;
      color: var(--muted);
    }
    button {
      margin-top: 12px;
      width: 100%;
      background: linear-gradient(120deg, #0284c7, #06b6d4);
      color: white;
      border: 0;
      border-radius: 9px;
      padding: 10px 12px;
      font-weight: 600;
      cursor: pointer;
    }
    .output {
      min-height: 70vh;
      overflow: auto;
      white-space: pre;
      line-height: 1.35;
      font-size: 13px;
      background: #030712;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
    }
    .muted { color: var(--muted); }
    .note { color: var(--note); font-weight: 700; }
    .source { color: var(--source); }
    .lineno { color: #cbd5e1; }
    .warn { color: var(--warn); font-weight: 700; }
    .err { color: var(--err); font-weight: 700; }
    .sep { color: #475569; }
    .total { color: #67e8f9; font-weight: 700; }
    mark {
      background: color-mix(in srgb, var(--hit) 35%, transparent);
      color: #d1fae5;
      border-radius: 3px;
      padding: 0 1px;
    }
    @media (max-width: 900px) {
      .wrap { grid-template-columns: 1fr; }
      .output { min-height: 55vh; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <form class="panel" id="search-form">
      <h1>Attic Search</h1>
      <label for="query">Search Word</label>
      <input id="query" name="query" type="text" placeholder="172.16.11.12" required />
      <label for="password">Secret Password</label>
      <input id="password" name="password" type="password" required />
      <label for="root">Root Directory</label>
      <input id="root" name="root" type="text" value="/var/www/dokuwiki/data/attic" />
      <label for="max_results">Max Results (optional)</label>
      <input id="max_results" name="max_results" type="number" min="1" />
      <label class="row"><input id="ignore_case" name="ignore_case" type="checkbox" /> ignore case</label>
      <button type="submit">Run Search</button>
    </form>

    <div class="panel">
      <div class="muted" style="margin-bottom: 8px;">Terminal-like output</div>
      <pre id="output" class="output">Enter search word and click "Run Search".</pre>
    </div>
  </div>

  <script>
    const form = document.getElementById('search-form');
    const output = document.getElementById('output');

    function escapeHtml(text) {
      return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
    }

    function escapeRegExp(text) {
      return text.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
    }

    function highlightQuery(text, query, ignoreCase) {
      if (!query) return text;
      const flags = ignoreCase ? 'gi' : 'g';
      const re = new RegExp(escapeRegExp(query), flags);
      return text.replace(re, (m) => `<mark>${m}</mark>`);
    }

    function classForLine(line) {
      if (line.startsWith('NOTE:')) return 'note';
      if (line.startsWith('SOURCE:')) return 'source';
      if (line.startsWith('WARN:')) return 'warn';
      if (line.startsWith('ERROR:')) return 'err';
      if (line.startsWith('TOTAL MATCHES:')) return 'total';
      if (/^\\s*\\d+:/.test(line)) return 'lineno';
      if (/^-{20,}$/.test(line)) return 'sep';
      return '';
    }

    function renderLines(text, query, ignoreCase) {
      const lines = text.split('\\n');
      const rendered = lines.map((line) => {
        const cls = classForLine(line);
        const safe = highlightQuery(escapeHtml(line), escapeHtml(query), ignoreCase);
        return cls ? `<span class="${cls}">${safe}</span>` : safe;
      });
      output.innerHTML = rendered.join('\\n');
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const fd = new FormData(form);
      const query = String(fd.get('query') || '');
      const password = String(fd.get('password') || '');
      const root = String(fd.get('root') || '');
      const ignoreCase = fd.get('ignore_case') === 'on';
      const maxResults = String(fd.get('max_results') || '').trim();
      const params = new URLSearchParams();
      params.set('query', query);
      params.set('password', password);
      if (root) params.set('root', root);
      if (ignoreCase) params.set('ignore_case', '1');
      if (maxResults) params.set('max_results', maxResults);

      output.textContent = 'Searching...';
      try {
        const res = await fetch(`/api/search?${params.toString()}`);
        const data = await res.json();
        if (!res.ok) {
          renderLines(data.error || 'ERROR: search failed', query, ignoreCase);
          return;
        }
        renderLines(data.output + `\\nTOTAL MATCHES: ${data.total}`, query, ignoreCase);
      } catch (err) {
        renderLines(`ERROR: ${String(err)}`, query, ignoreCase);
      }
    });
  </script>
</body>
</html>
"""


def iter_files(root: Path) -> Iterator[Path]:
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            yield Path(dirpath) / name


def is_archive(path: Path) -> bool:
    low = path.name.lower()
    return any(low.endswith(ext) for ext in ARCHIVE_EXTENSIONS)


def strip_known_suffixes(name: str) -> str:
    low = name.lower()
    for ext in ARCHIVE_EXTENSIONS:
        if low.endswith(ext):
            return name[: -len(ext)]
    return name


def derive_note_address(relative_path: str, member_name: Optional[str] = None) -> str:
    candidate = relative_path
    # For zip/tar members, use member path. For single-file compression (gz/bz2/xz),
    # keep relative_path so namespace folders are preserved.
    if member_name and ("/" in member_name or "\\" in member_name):
        candidate = member_name
    candidate = unquote(candidate).replace("\\", "/")
    candidate = strip_known_suffixes(candidate)

    # Typical attic file: namespace/page.1700000000.txt
    candidate = re.sub(r"\.\d{9,12}(?=\.txt$)", "", candidate)
    candidate = re.sub(r"\.txt$", "", candidate, flags=re.IGNORECASE)
    candidate = candidate.strip("/")
    return candidate.replace("/", ":")


def open_text_file(path: Path) -> io.TextIOBase:
    low = path.name.lower()
    if low.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
    if low.endswith(".bz2"):
        return bz2.open(path, "rt", encoding="utf-8", errors="ignore")
    if low.endswith(".xz"):
        return lzma.open(path, "rt", encoding="utf-8", errors="ignore")
    return open(path, "rt", encoding="utf-8", errors="ignore")


def iter_archive_texts(path: Path) -> Iterator[Tuple[Optional[str], Iterable[str]]]:
    low = path.name.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                with zf.open(info, "r") as fh:
                    text_stream = io.TextIOWrapper(fh, encoding="utf-8", errors="ignore")
                    yield info.filename, text_stream
        return

    if low.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        with tarfile.open(path, "r:*") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                fh = tf.extractfile(member)
                if fh is None:
                    continue
                with fh:
                    text_stream = io.TextIOWrapper(fh, encoding="utf-8", errors="ignore")
                    yield member.name, text_stream
        return

    # Single-file compressed archives (gz/bz2/xz)
    with open_text_file(path) as fh:
        yield None, fh


def find_matches(
    lines: Iterable[str],
    query: str,
    ignore_case: bool,
) -> Iterator[Tuple[int, str, str, str]]:
    prev_line = ""
    buffered = []

    if ignore_case:
        q = query.casefold()
        predicate = lambda s: q in s.casefold()
    else:
        predicate = lambda s: query in s

    for idx, line in enumerate(lines, start=1):
        clean = line.rstrip("\n")
        if predicate(clean):
            next_line = ""
            buffered.append((idx, prev_line, clean))
            # keep line to fill next on following iteration
            buffered[-1] = (buffered[-1][0], buffered[-1][1], buffered[-1][2], next_line)
        # fill "next line" for pending matches
        if buffered:
            for i in range(len(buffered)):
                lno, before, match, after = buffered[i]
                if after == "":
                    buffered[i] = (lno, before, match, clean if lno != idx else "")
            ready = [item for item in buffered if item[3] != ""]
            buffered = [item for item in buffered if item[3] == ""]
            for lno, before, match, after in ready:
                yield lno, before, match, after

        prev_line = clean

    # flush matches at EOF (without next line)
    for lno, before, match, _ in buffered:
        yield lno, before, match, ""


def format_match(
    rel_source: str,
    line_no: int,
    before: str,
    match: str,
    after: str,
    member_name: Optional[str] = None,
) -> str:
    note_addr = derive_note_address(rel_source, member_name)
    if member_name is None:
        location = unquote(rel_source)
    else:
        location = f"{unquote(rel_source)}!{unquote(member_name)}"
    return "\n".join(
        [
            f"NOTE: {note_addr}",
            f"SOURCE: {location}",
            f"{line_no - 1:>7}: {before}",
            f"{line_no:>7}: {match}",
            f"{line_no + 1:>7}: {after}",
            "-" * 80,
        ]
    )


def process_plain_file(
    path: Path,
    root: Path,
    query: str,
    ignore_case: bool,
    max_results: Optional[int],
    count: int,
    output: List[str],
    warnings: List[str],
) -> int:
    rel_source = str(path.relative_to(root))
    try:
        with open_text_file(path) as fh:
            for lno, before, match, after in find_matches(fh, query, ignore_case):
                output.append(format_match(rel_source, lno, before, match, after))
                count += 1
                if max_results is not None and count >= max_results:
                    return count
    except Exception as exc:
        warnings.append(f"WARN: cannot read {path}: {exc}")
    return count


def process_archive_file(
    path: Path,
    root: Path,
    query: str,
    ignore_case: bool,
    max_results: Optional[int],
    count: int,
    output: List[str],
    warnings: List[str],
) -> int:
    rel_source = str(path.relative_to(root))
    try:
        for member_name, text_stream in iter_archive_texts(path):
            for lno, before, match, after in find_matches(text_stream, query, ignore_case):
                output.append(
                    format_match(rel_source, lno, before, match, after, member_name=member_name)
                )
                count += 1
                if max_results is not None and count >= max_results:
                    return count
    except Exception as exc:
        warnings.append(f"WARN: cannot read archive {path}: {exc}")
    return count


def run_search(
    root: Path,
    query: str,
    ignore_case: bool,
    max_results: Optional[int],
) -> Tuple[str, int, List[str]]:
    total = 0
    output: List[str] = []
    warnings: List[str] = []

    for path in iter_files(root):
        if is_archive(path):
            total = process_archive_file(
                path, root, query, ignore_case, max_results, total, output, warnings
            )
        else:
            total = process_plain_file(
                path, root, query, ignore_case, max_results, total, output, warnings
            )
        if max_results is not None and total >= max_results:
            break

    return "\n".join(output), total, warnings


def resolve_root(root_arg: str) -> Tuple[Optional[Path], Optional[str]]:
    root = Path(root_arg).expanduser().resolve()
    if not root.exists():
        return None, f"ERROR: directory not found: {root}"
    if not root.is_dir():
        return None, f"ERROR: not a directory: {root}"
    return root, None


def validate_password(password: str) -> Optional[str]:
    if not password:
        return "ERROR: password is required"
    if password != SECRET_PASSWORD:
        return "ERROR: invalid password"
    return None


class SearchHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = WEB_TEMPLATE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path != "/api/search":
            self._send_json(404, {"error": "ERROR: not found"})
            return

        params = parse_qs(parsed.query)
        query = params.get("query", [""])[0]
        if not query:
            self._send_json(400, {"error": "ERROR: query is required"})
            return
        password = params.get("password", [""])[0]
        password_error = validate_password(password)
        if password_error is not None:
            self._send_json(403, {"error": password_error})
            return

        root_arg = params.get("root", ["/var/www/dokuwiki/data/attic"])[0]
        root, err = resolve_root(root_arg)
        if err is not None or root is None:
            self._send_json(400, {"error": err})
            return

        ignore_case = params.get("ignore_case", ["0"])[0] in {"1", "true", "yes", "on"}
        max_results_raw = params.get("max_results", [""])[0].strip()
        max_results: Optional[int] = None
        if max_results_raw:
            try:
                max_results = int(max_results_raw)
                if max_results < 1:
                    raise ValueError("must be positive")
            except ValueError:
                self._send_json(400, {"error": "ERROR: max_results must be positive integer"})
                return

        output, total, warnings = run_search(root, query, ignore_case, max_results)
        if warnings:
            output = "\n".join(warnings + ([output] if output else []))
        self._send_json(200, {"output": output, "total": total})

    def log_message(self, format: str, *args: object) -> None:
        return


def run_web_server(host: str, port: int) -> int:
    server = ThreadingHTTPServer((host, port), SearchHandler)
    print(f"Web UI started: http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search in DokuWiki attic directory, including archives."
    )
    parser.add_argument(
        "query",
        nargs="?",
        help='Substring to search (example: "172.16.11.12")',
    )
    parser.add_argument(
        "root",
        nargs="?",
        default="/var/www/dokuwiki/data/attic",
        help="Root directory to scan (default: /var/www/dokuwiki/data/attic)",
    )
    parser.add_argument(
        "-i",
        "--ignore-case",
        action="store_true",
        help="Case-insensitive search",
    )
    parser.add_argument(
        "-n",
        "--max-results",
        type=int,
        default=None,
        help="Stop after this many matches",
    )
    parser.add_argument(
        "--password",
        default="",
        help="Secret password required to run the search",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Run web server with search UI",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for web server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for web server (default: 8000)",
    )
    args = parser.parse_args()

    if args.web:
        return run_web_server(args.host, args.port)

    if not args.query:
        parser.error("query is required unless --web is used")

    password_error = validate_password(args.password)
    if password_error is not None:
        print(password_error, file=sys.stderr)
        return 2

    root, err = resolve_root(args.root)
    if err is not None or root is None:
        print(err, file=sys.stderr)
        return 2

    output, total, warnings = run_search(root, args.query, args.ignore_case, args.max_results)
    for warning in warnings:
        print(warning, file=sys.stderr)
    if output:
        print(output)
    print(f"TOTAL MATCHES: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
