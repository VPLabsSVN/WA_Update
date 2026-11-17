#!/usr/bin/env python3
from __future__ import annotations
import re, shutil
from datetime import datetime
from pathlib import Path

APP=Path('/home/wa_stopshop_mb/WA/Main_program/Fis')
PANEL=APP/'Panel.py'; BACKEND=APP/'Panel_backend_merged.py'
MARKER='# === CAKOVEC_REOPEN_RETRY_V1 ==='

def die(msg): raise SystemExit('SAFETY STOP: '+msg)
def backup(p,stamp):
    q=p.with_name(p.name+f'.backup_before_CAKOVEC_REOPEN_RETRY_{stamp}')
    shutil.copy2(p,q); return q

for p in (PANEL,BACKEND):
    if not p.is_file(): die(f'missing {p}')

stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
panel=PANEL.read_text(encoding='utf-8')
backend=BACKEND.read_text(encoding='utf-8')
if MARKER in backend: die('patch already installed')

# Structural checks BEFORE writing anything.
m=re.search(r'(?m)^THANKYOU_SECONDS\s*=\s*([0-9.]+)\s*$',panel)
if not m: die('THANKYOU_SECONDS not found')
old_thanks=m.group(1)

for key in ('revolver_7','revolver_10'):
    if not re.search(rf'I18N\.setdefault\("{key}",\s*\{{.*?\n\}}\)',panel,re.S):
        die(f'{key} translation block not found')

old_reopen='''    def on_reopen_door(self):
        items = getattr(self, "_last_purchased_items", []) or []
        if items:
            grouped = {}
            for it in items:
                pos = int(it.get("panel_position", PANEL_POSITION))
                grouped.setdefault(pos, []).append(int(it.get("id")))
            for pos, ids in grouped.items():
                if pos == int(PANEL_POSITION):
                    self.open_doors_sequential(ids, pulse_ms=DOOR_PULSE_MS, gap_ms=DOOR_GAP_MS)
                else:
                    self._send_remote_message_with_ack(pos, {"type": "remote_reopen", "items": ids})
            return

        if self._last_purchased_hidden_id is not None:
            self.open_doors_sequential([self._last_purchased_hidden_id], pulse_ms=DOOR_PULSE_MS, gap_ms=DOOR_GAP_MS)
'''
if old_reopen not in panel: die('expected on_reopen_door block not found')
if 'class PanelBackendReservationApp' not in backend: die('PanelBackendReservationApp not found')

pb=backup(PANEL,stamp); bb=backup(BACKEND,stamp)

# 1) 120-second Thank You window.
panel=panel[:m.start()]+'THANKYOU_SECONDS = 120'+panel[m.end():]

# 2) Customer-facing revolver labels.
def set_label(text,key):
    pat=re.compile(rf'I18N\.setdefault\("{key}",\s*\{{.*?\n\}}\)',re.S)
    rep=f'''I18N.setdefault("{key}", {{
    "en": "Single rose",
    "sl": "Vrtnica - kos",
    "hr": "Ruža komad",
    "de": "Einzelrose",
    "it": "Rosa singola",
}})'''
    return pat.sub(rep,text,count=1)
panel=set_label(panel,'revolver_7')
panel=set_label(panel,'revolver_10')

# 3) Do not block the Kivy thread on a customer Reopen press.
new_reopen='''    def on_reopen_door(self):
        items = getattr(self, "_last_purchased_items", []) or []
        if items:
            grouped = {}
            for it in items:
                pos = int(it.get("panel_position", PANEL_POSITION))
                grouped.setdefault(pos, []).append(int(it.get("id")))
            for pos, ids in grouped.items():
                if pos == int(PANEL_POSITION):
                    self.open_doors_sequential(ids, pulse_ms=DOOR_PULSE_MS, gap_ms=DOOR_GAP_MS)
                else:
                    def remote_reopen_worker(p=int(pos), item_ids=list(ids)):
                        try:
                            ack = self._send_remote_message_with_ack(
                                p, {"type": "remote_reopen", "items": item_ids}
                            )
                            print(f"[REOPEN RETRY] panel={p} items={item_ids} final_ack={ack}")
                        except Exception as exc:
                            print(f"[REOPEN RETRY] panel={p} items={item_ids} exception={exc}")
                    threading.Thread(
                        target=remote_reopen_worker,
                        daemon=True,
                        name=f"reopen-panel-{int(pos)}",
                    ).start()
            return

        if self._last_purchased_hidden_id is not None:
            self.open_doors_sequential([self._last_purchased_hidden_id], pulse_ms=DOOR_PULSE_MS, gap_ms=DOOR_GAP_MS)
'''
panel=panel.replace(old_reopen,new_reopen,1)

# 4) Final-ACK-aware retries for initial purchase AND Reopen.
patch=r'''
# === CAKOVEC_REOPEN_RETRY_V1 ===
_CAKOVEC_REMOTE_ACK_ORIGINAL = PanelBackendReservationApp._send_remote_message_with_ack
_CAKOVEC_DOOR_MAX_PHYSICAL_ATTEMPTS = 5
_CAKOVEC_BUSY_WAIT_S = 1.0
_CAKOVEC_BUSY_WAIT_LIMIT = 45
_CAKOVEC_HARD_ERROR_TOKENS = (
    "invalid", "no ids", "not found", "out_of_memory", "safety",
    "interlock", "polarity", "encoder", "tmc", "electrical",
    "hardware_not_ready", "motor_preflight", "global_fault", "quarantine",
)

def _cakovec_err(ack):
    if not isinstance(ack, dict): return ""
    parts=[str(ack.get("error") or "")]
    for r in ack.get("results") or []:
        if isinstance(r,dict): parts.append(str(r.get("error") or ""))
    return " ".join(parts).lower()

def _cakovec_id(x):
    try: return int(x.get("id") if isinstance(x,dict) else x)
    except Exception: return None

def _cakovec_retry_ack(self, position, payload, timeout_s=REMOTE_COMMAND_TIMEOUT_S):
    typ=str((payload or {}).get("type") or "").lower()
    if typ not in ("remote_purchase","remote_reopen"):
        return _CAKOVEC_REMOTE_ACK_ORIGINAL(self,position,payload,timeout_s=timeout_s)
    raw=list((payload or {}).get("items") or [])
    ids=[i for i in (_cakovec_id(x) for x in raw) if i is not None]
    if not ids:
        return _CAKOVEC_REMOTE_ACK_ORIGINAL(self,position,payload,timeout_s=timeout_s)
    pending=set(ids); final={}; attempts=0; busy=0; last={}
    while pending and attempts < _CAKOVEC_DOOR_MAX_PHYSICAL_ATTEMPTS:
        msg=dict(payload)
        msg["items"]=[x for x in raw if _cakovec_id(x) in pending]
        last=_CAKOVEC_REMOTE_ACK_ORIGINAL(self,position,msg,timeout_s=timeout_s)
        if not isinstance(last,dict): last={"ok":False,"error":"invalid_ack"}
        err=_cakovec_err(last)
        if "previous_order_in_progress_wait_for_final_ack" in err:
            busy += 1
            print(f"[DOOR RETRY] panel={position} type={typ} busy_wait={busy}")
            if busy >= _CAKOVEC_BUSY_WAIT_LIMIT: break
            time.sleep(_CAKOVEC_BUSY_WAIT_S); continue
        busy=0; attempts += 1
        rows={}
        for r in last.get("results") or []:
            if isinstance(r,dict):
                iid=_cakovec_id(r)
                if iid is not None: rows[iid]=dict(r)
        for iid,r in rows.items():
            if iid in pending:
                final[iid]=r
                if bool(r.get("ok")): pending.discard(iid)
        if bool(last.get("ok")) and not rows:
            for iid in list(pending):
                final[iid]={"id":iid,"ok":True,"error":""}; pending.discard(iid)
        print(f"[DOOR RETRY] panel={position} type={typ} attempt={attempts}/5 remaining={sorted(pending)} ack={last}")
        if not pending: break
        if any(tok in err for tok in _CAKOVEC_HARD_ERROR_TOKENS):
            print(f"[DOOR RETRY] panel={position} hard failure; retries stopped")
            break
        time.sleep(1.0)
    results=[]
    for iid in ids:
        results.append(final.get(iid,{"id":iid,"ok":False,"error":str(last.get("error") or "retry_exhausted")}))
    out=dict(last or {})
    out["ok"]=bool(results) and all(bool(r.get("ok")) for r in results)
    out["results"]=results
    out["completed"]=sum(1 for r in results if bool(r.get("ok")))
    out["requested"]=len(results)
    out["retry_physical_attempts"]=attempts
    out["retry_max_physical_attempts"]=_CAKOVEC_DOOR_MAX_PHYSICAL_ATTEMPTS
    if not out["ok"] and not out.get("error"): out["error"]="door_open_retry_exhausted"
    return out

PanelBackendReservationApp._send_remote_message_with_ack = _cakovec_retry_ack
'''

mm=re.search(r'(?m)^if __name__\s*==\s*["\']__main__["\']\s*:',backend)
if mm:
    backend=backend[:mm.start()]+patch+'\n\n'+backend[mm.start():]
else:
    backend=backend.rstrip()+'\n\n'+patch+'\n'

PANEL.write_text(panel,encoding='utf-8')
BACKEND.write_text(backend,encoding='utf-8')
print('PATCH INSTALLED')
print('Panel backup:',pb)
print('Backend backup:',bb)
print('THANKYOU_SECONDS:',old_thanks,'-> 120')
print('R7/R10 labels changed in en/sl/hr/de/it')
print('Initial purchase + Reopen: up to 5 final-ACK-aware physical attempts')
