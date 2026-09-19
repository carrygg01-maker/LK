#!/usr/bin/env python3
"""
webapp/app.py — หน้าเว็บสำหรับรัน edge_lab เอง ไม่ต้องเปิด terminal

    pip install -r ../requirements.txt
    python webapp/app.py
    เปิด http://localhost:5000

รันงานจริง (synthetic หรือ real) ใน background thread แล้ว poll ผลผ่าน /api/status/<job_id>
ห้ามรันสองงานพร้อมกัน — run.main() ใช้ sys.argv/สถานะร่วมของโมดูล run.py
"""
from __future__ import annotations

import contextlib
import io
import sys
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

LAB_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB_DIR))
import run as R          # noqa: E402
import hypotheses as H    # noqa: E402

app = Flask(__name__)

JOBS: dict[str, dict] = {}
JOB_LOCK = threading.Lock()


class _LogCapture(io.TextIOBase):
    """ดัก stdout ของ run.main() แยกเป็นบรรทัด เก็บไว้ให้หน้าเว็บ poll อ่าน"""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            JOBS[self.job_id]["log"].append(line)
        return len(s)


def _build_argv(payload: dict) -> list[str]:
    argv = ["run.py"]
    mode = payload.get("mode", "synthetic")
    if mode == "synthetic":
        argv.append("--synthetic")
        edge_bps = payload.get("edge_bps")
        if edge_bps:
            argv += ["--edge-bps", str(float(edge_bps))]
    else:
        coins = int(payload.get("coins", 40))
        start = str(payload.get("start", "2021-01-01"))
        argv += ["--coins", str(coins), "--start", start]
    only = payload.get("only") or []
    only = [o for o in only if o in H.REGISTRY]
    if only:
        argv += ["--only", *only]
    return argv


def _run_job(job_id: str, argv: list[str]) -> None:
    job = JOBS[job_id]
    with JOB_LOCK:
        job["status"] = "running"
        old_argv = sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(_LogCapture(job_id)):
                R.main()
            results_csv = LAB_DIR / "results.csv"
            report_md = LAB_DIR / "report.md"
            if results_csv.exists():
                import pandas as pd
                df = pd.read_csv(results_csv)
                job["table"] = df.to_dict(orient="records")
            if report_md.exists():
                job["report_md"] = report_md.read_text(encoding="utf-8")
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001
            job["status"] = "error"
            job["error"] = str(e)
        finally:
            sys.argv = old_argv


@app.route("/")
def index():
    return render_template("index.html", hypotheses=list(H.REGISTRY.items()))


@app.route("/api/run", methods=["POST"])
def api_run():
    if JOB_LOCK.locked():
        return jsonify({"error": "มีงานกำลังรันอยู่ รอให้เสร็จก่อนแล้วค่อยรันใหม่"}), 409

    payload = request.get_json(force=True) or {}
    argv = _build_argv(payload)

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "log": [], "table": None,
                    "report_md": None, "argv": argv}
    threading.Thread(target=_run_job, args=(job_id, argv), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def api_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "ไม่พบงานนี้"}), 404
    return jsonify(job)


@app.route("/download/<name>")
def download(name: str):
    if name not in ("report.md", "results.csv"):
        return jsonify({"error": "ไม่รู้จักไฟล์นี้"}), 404
    return send_from_directory(LAB_DIR, name, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
