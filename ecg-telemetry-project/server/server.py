#!/usr/bin/env python3
"""
server.py  --  Parts 4 and 5 of the assignment.

Receives ECG batches from the phone over Wi-Fi / mobile data, runs the QRS
detector and the beat classifier, and returns the results to the phone.

    pip install flask
    python3 server.py --host 0.0.0.0 --port 8000

Then open http://<your-ip>:8000/ in a browser for a live strip chart.

Endpoints
    POST /api/ecg      {"device":"pixel-7","fs":250,"seq":12,"samples":[mV,...]}
                    -> {"beats":[{"t":..,"label":"Ventricular","confidence":..,
                                  "bpm":..}], "hr":74, "summary":{...}}
    GET  /api/state?device=...   last 10 s of signal + recent beats (for the UI)
    GET  /healthz

Design note: detector state is kept PER DEVICE and persists across requests.
The phone sends 2 s batches, and a QRS sitting on a batch boundary would be
missed if each request were analysed independently -- the adaptive thresholds
and the RR history have to carry over.
"""

import argparse
import os
import threading
import time
from collections import deque

from flask import Flask, jsonify, request, Response

from ecg_algorithms import (FS, PanTompkins, RuleClassifier, features,
                            load_classifier)

app = Flask(__name__)

_lock = threading.Lock()
_sessions = {}
MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.json")

if os.path.exists(MODEL_PATH):
    CLASSIFIER = load_classifier(MODEL_PATH)
    print("loaded trained model from model.json")
else:
    CLASSIFIER = RuleClassifier()
    print("model.json not found -- using the rule-based fallback classifier.\n"
          "Run  python3 train_classifier.py  to train on MIT-BIH.")


class Session:
    def __init__(self, fs):
        self.fs = fs
        self.det = PanTompkins(fs)
        self.n = 0
        self.signal = deque(maxlen=int(10 * fs))    # for the web UI
        self.beats = deque(maxlen=200)
        self.counts = {}
        self.started = time.time()
        self.last_seen = time.time()

    def push(self, samples):
        out = []
        for s in samples:
            self.signal.append(s)
            beat = self.det.process(s)
            self.n += 1
            if beat:
                out.append(self._classify(beat))
        self.last_seen = time.time()
        return out

    def _classify(self, beat):
        label, conf = CLASSIFIER.predict(features(beat))
        self.counts[label] = self.counts.get(label, 0) + 1
        rec = {
            "t": round(beat["index"] / self.fs, 3),
            "label": label,
            "confidence": round(float(conf), 3),
            "rr": round(beat["rr_prev"], 3),
            "bpm": round(60.0 / beat["rr_prev"]) if beat["rr_prev"] > 0 else 0,
        }
        self.beats.append(rec)
        return rec

    def heart_rate(self):
        rr = [b["rr"] for b in list(self.beats)[-8:] if b["rr"] > 0]
        return round(60.0 / (sum(rr) / len(rr))) if rr else 0


def get_session(device, fs):
    with _lock:
        s = _sessions.get(device)
        if s is None or s.fs != fs:
            s = _sessions[device] = Session(fs)
        return s


@app.post("/api/ecg")
def ingest():
    d = request.get_json(force=True, silent=True) or {}
    samples = d.get("samples")
    if not isinstance(samples, list) or not samples:
        return jsonify(error="samples must be a non-empty list"), 400
    if len(samples) > 20000:
        return jsonify(error="batch too large"), 413
    try:
        samples = [float(v) for v in samples]
    except (TypeError, ValueError):
        return jsonify(error="samples must be numeric"), 400

    device = str(d.get("device", "unknown"))[:64]
    fs = float(d.get("fs", FS))
    sess = get_session(device, fs)

    t0 = time.time()
    beats = sess.push(samples)
    ms = (time.time() - t0) * 1000

    return jsonify(
        beats=beats,
        hr=sess.heart_rate(),
        seq=d.get("seq"),
        summary=sess.counts,
        analysed_samples=sess.n,
        processing_ms=round(ms, 1),
    )


@app.get("/api/state")
def state():
    device = request.args.get("device", "unknown")
    s = _sessions.get(device)
    if not s:
        return jsonify(signal=[], beats=[], hr=0, summary={})
    return jsonify(signal=[round(v, 4) for v in s.signal],
                   beats=list(s.beats)[-40:],
                   hr=s.heart_rate(), summary=s.counts, fs=s.fs)


@app.get("/api/devices")
def devices():
    return jsonify([{"device": k, "samples": v.n, "hr": v.heart_rate(),
                     "age_s": round(time.time() - v.last_seen, 1)}
                    for k, v in _sessions.items()])


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, classifier=type(CLASSIFIER).__name__)


@app.get("/")
def index():
    return Response(DASHBOARD, mimetype="text/html")


DASHBOARD = """<!doctype html><meta charset=utf-8>
<title>ECG monitor</title>
<style>
 body{background:#0b0f14;color:#d6e2ee;font:14px/1.5 system-ui,sans-serif;margin:0;padding:24px}
 h1{font-size:16px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#7d93a8}
 canvas{width:100%;height:260px;background:#0d1520;border:1px solid #1e2b3a;border-radius:6px}
 .row{display:flex;gap:32px;align-items:baseline;margin:16px 0}
 .big{font-size:44px;font-weight:300;color:#39d98a}
 .k{color:#7d93a8;font-size:12px;text-transform:uppercase;letter-spacing:.08em}
 table{border-collapse:collapse;margin-top:16px;font-variant-numeric:tabular-nums}
 td,th{padding:4px 16px 4px 0;text-align:left;border-bottom:1px solid #1a2431}
 .V{color:#ff6b6b}.S{color:#ffb454}.N{color:#39d98a}.O{color:#7d93a8}
 select{background:#131c28;color:#d6e2ee;border:1px solid #26364a;padding:4px 8px;border-radius:4px}
</style>
<h1>ECG monitor</h1>
<div class=row>
  <div><div class=k>device</div><select id=dev></select></div>
  <div><div class=k>heart rate</div><span class=big id=hr>--</span> <span class=k>bpm</span></div>
  <div><div class=k>beats</div><span id=summary></span></div>
</div>
<canvas id=c width=1400 height=260></canvas>
<table id=t><tr><th>time<th>label<th>conf<th>rr<th>bpm</tr></table>
<script>
const cls={Normal:'N',Supraventricular:'S',Ventricular:'V',Other:'O'};
async function tick(){
  const ds=await (await fetch('/api/devices')).json();
  const sel=document.getElementById('dev');
  if(sel.options.length!==ds.length){sel.innerHTML=ds.map(d=>`<option>${d.device}</option>`).join('');}
  if(!ds.length)return;
  const dev=sel.value||ds[0].device;
  const s=await (await fetch('/api/state?device='+encodeURIComponent(dev))).json();
  document.getElementById('hr').textContent=s.hr||'--';
  document.getElementById('summary').innerHTML=Object.entries(s.summary||{})
    .map(([k,v])=>`<span class=${cls[k]||'O'}>${k} ${v}</span>`).join(' &middot; ');
  draw(s);
  document.getElementById('t').innerHTML='<tr><th>time<th>label<th>conf<th>rr<th>bpm</tr>'+
    s.beats.slice(-12).reverse().map(b=>`<tr><td>${b.t}<td class=${cls[b.label]||'O'}>${b.label}<td>${b.confidence}<td>${b.rr}<td>${b.bpm}</tr>`).join('');
}
function draw(s){
  const c=document.getElementById('c'),g=c.getContext('2d');
  g.clearRect(0,0,c.width,c.height);
  const d=s.signal||[];if(!d.length)return;
  let lo=Math.min(...d),hi=Math.max(...d);const pad=(hi-lo)*.15||1;lo-=pad;hi+=pad;
  g.strokeStyle='#16212e';g.lineWidth=1;g.beginPath();
  for(let x=0;x<c.width;x+=c.width/50){g.moveTo(x,0);g.lineTo(x,c.height);}
  for(let y=0;y<c.height;y+=26){g.moveTo(0,y);g.lineTo(c.width,y);}g.stroke();
  g.strokeStyle='#39d98a';g.lineWidth=1.6;g.beginPath();
  d.forEach((v,i)=>{const x=i/d.length*c.width,y=c.height-(v-lo)/(hi-lo)*c.height;
    i?g.lineTo(x,y):g.moveTo(x,y);});
  g.stroke();
  const fs=s.fs||250,t1=(s.beats.length?d.length/fs:0);
  (s.beats||[]).forEach(b=>{
    const last=s.beats[s.beats.length-1].t;
    const x=(1-(last-b.t)/(d.length/fs))*c.width;
    if(x<0||x>c.width)return;
    g.fillStyle=b.label=='Ventricular'?'#ff6b6b':b.label=='Supraventricular'?'#ffb454':'#39d98a';
    g.fillRect(x-1,0,2,10);
  });
}
setInterval(tick,500);tick();
</script>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    print("POST ECG batches to http://<this-machine>:%d/api/ecg" % a.port)
    app.run(host=a.host, port=a.port, threaded=True)
