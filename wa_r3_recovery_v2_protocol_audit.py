#!/usr/bin/env python3
"""Read-only R3 V8.3.x recovery protocol audit for upgraded machines.

- Pulls fresh authenticated R3 motor status (never sends a recovery command).
- Classifies faults 1..7, global faults and customer door-cycle locks.
- Optionally analyzes a SAVED final purchase ACK for retry safety.
- Does NOT clear faults, reset pending access, resend a purchase, change
  inventory, or assert physical inspection. Do not use it as a recovery bridge.

Usage:
    ./venv/bin/python wa_r3_recovery_v2_protocol_audit.py --self-test
    ./venv/bin/python wa_r3_recovery_v2_protocol_audit.py --status
    ./venv/bin/python wa_r3_recovery_v2_protocol_audit.py --ack-file failed_ack.json --item 5
"""
from __future__ import annotations
import argparse
import json
import socket
import time
import uuid
from pathlib import Path

HOME = Path(__file__).resolve().parent
CONFIG = HOME / 'pi_recovery_endpoints.json'
FAULTS = {
    0: 'no fault', 1: 'startup failure', 2: 'motor disable/readback unsafe',
    3: 'encoder/I2C communication', 4: 'missing motion / mechanical stall',
    5: 'TMC UART communication', 6: 'LEDC/STEP output failure',
    7: 'TMC electrical fault',
}
SOFTWARE_RETRY_CANDIDATES = (1, 3, 5)
INSPECTION_REQUIRED = (2, 4, 6, 7)
BLOCKERS = ('global_fault', 'guard_latched', 'unsafe_maintenance_stop',
            'network_job_active', 'network_job_stalled', 'order_stall_latched',
            'service_busy', 'service_stalled', 'maintenance_busy',
            'idle_worker_stalled')


def endpoint():
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    spec = config['controllers']['r3']
    if spec.get('ip') != '192.168.1.201' or spec.get('protocol') != 'r3_motor_recovery':
        raise ValueError('R3 endpoint mismatch; no request sent')
    token = spec.get('token')
    if not isinstance(token, str) or len(token) < 24:
        raise ValueError('R3 token invalid')
    return (config['pi_ip'], spec['ip'], int(spec['port']), token)


def read_only_status(timeout=10.0):
    src, dest, port, token = endpoint()
    rid = 'r3audit-' + uuid.uuid4().hex
    req = {'type': 'remote_motor_status', 'request_id': rid, 'token': token}
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((src, 0))
        sock.sendto(json.dumps(req, separators=(',', ':')).encode('utf-8'), (dest, port))
        while time.monotonic() < deadline:
            sock.settimeout(deadline - time.monotonic())
            data, addr = sock.recvfrom(65535)
            if addr != (dest, port):
                continue
            try:
                reply = json.loads(data.decode('utf-8'))
            except (UnicodeError, ValueError):
                continue
            if (isinstance(reply, dict) and reply.get('type') == 'remote_motor_ack'
                    and reply.get('request_id') == rid):
                if reply.get('ok') is not True or not isinstance(reply.get('motors'), list):
                    raise ValueError('Controller refused/incomplete status')
                return reply
    raise TimeoutError('No authenticated correlated status reply; state unknown')


def flag(d, key):
    return 'OK' if d.get(key) is False else ('ACTIVE' if d.get(key) is True else 'UNKNOWN')


def evaluate(d):
    rows = d.get('motors')
    if not isinstance(rows, list):
        return {'state': 'UNVERIFIED', 'reason': 'missing motor rows'}
    motors = {r.get('motor'): r for r in rows if isinstance(r, dict)}
    if set(motors) != {7, 10}:
        return {'state': 'UNVERIFIED', 'reason': 'R7 and R10 both required'}
    blocking = [key for key in BLOCKERS if d.get(key) is not False]
    pending = (d.get('pending_wait_open_mask'), d.get('pending_wait_close_mask'))
    if any(type(x) is not int or x != 0 for x in pending):
        blocking.append('pending_wait_masks_unverified_or_nonzero')
    info = []
    for motor in (7, 10):
        r = motors[motor]
        reason = r.get('fault_reason')
        latched = r.get('fault_latched')
        row = {'motor': motor,
               'fault_latched': latched,
               'fault_reason': reason,
               'fault_name': FAULTS.get(reason, 'unknown'),
               'door_closed_verified': r.get('door_closed_verified'),
               'door_cycle_pending': r.get('door_cycle_pending'),
               'enn_disabled_verified': r.get('enn_disabled_verified'),
               'step_output_active': r.get('step_output_active'),
               'auto_recovery_budget_exhausted': r.get('auto_recovery_budget_exhausted')}
        if latched is not True:
            row['policy'] = 'NO LATCHED FAULT; do not issue recovery'
        elif blocking:
            row['policy'] = 'BLOCKED; global/owner/pending state must be resolved'
        elif (r.get('door_closed_verified') is not True or
              r.get('door_cycle_pending') is not False or
              r.get('enn_disabled_verified') is not True or
              r.get('step_output_active') is not False):
            row['policy'] = 'BLOCKED; missing verified closed/disabled state'
        elif reason in SOFTWARE_RETRY_CANDIDATES:
            row['policy'] = 'SOFTWARE RECOVERY CANDIDATE; V1 idle/purchase guards still required'
        elif reason in INSPECTION_REQUIRED:
            row['policy'] = 'INSPECTION/NEW-FIRMWARE AUTH CONTRACT REQUIRED; no unattended command'
        else:
            row['policy'] = 'UNKNOWN FAULT; refuse auto recovery'
        info.append(row)
    return {'state': 'BLOCKED' if blocking else 'MONITOR',
            'firmware': d.get('firmware'),
            'boot_id': d.get('boot_id'),
            'global_fault': d.get('global_fault'),
            'blockers': blocking,
            'pending_wait_open_mask': pending[0],
            'pending_wait_close_mask': pending[1],
            'motors': info}


def ack_assessment(reply, item_id, controller):
    """Conservative classification of a SAVED controller ACK, not a command."""
    if not isinstance(reply, dict) or reply.get('type') != 'remote_purchase_ack':
        return {'decision': 'STOP', 'reason': 'missing exact final purchase ACK'}
    if reply.get('warning') or reply.get('error'):
        return {'decision': 'STOP', 'reason': 'top-level warning/error: access or outcome uncertain'}
    results = reply.get('results')
    if not isinstance(results, list) or len(results) != 1:
        return {'decision': 'STOP', 'reason': 'not an exact single-item result'}
    r = results[0]
    if not isinstance(r, dict) or str(r.get('id')) != str(item_id):
        return {'decision': 'STOP', 'reason': 'result ID mismatch'}
    if r.get('ok') is True:
        return {'decision': 'SUCCESS/NOT_RETRYABLE', 'reason': 'ESP reports opening pulse success; delivery still requires separate confirmation'}
    if r.get('access_unconfirmed') is True:
        return {'decision': 'STOP', 'reason': 'door movement unconfirmed after a possible pulse'}
    error = str(r.get('error') or '').strip()
    if error not in {'revolver positioning failed', 'revolver_positioning_failed'}:
        return {'decision': 'STOP', 'reason': 'error is not verified pre-lock positioning failure'}
    analyzed = evaluate(controller)
    if analyzed.get('blockers'):
        return {'decision': 'STOP', 'reason': 'controller global/owner/pending state unsafe or unknown'}
    target_motor = 7 if 5 <= item_id <= 11 else 10 if 12 <= item_id <= 21 else None
    rows = {m['motor']: m for m in analyzed['motors']}
    if target_motor is None:
        return {'decision': 'STOP', 'reason': 'not a revolver item'}
    row = rows[target_motor]
    if row['fault_latched'] is True:
        return {'decision': 'STOP_RECOVER_FIRST', 'reason': 'motor fault present; recovery policy/verification required before any retry'}
    if (row['door_closed_verified'] is not True or
            row['door_cycle_pending'] is not False):
        return {'decision': 'STOP', 'reason': 'door state not verified safe'}
    return {'decision': 'PRE_PULSE_CANDIDATE',
            'reason': 'confirmed positioning failure; Pi still needs transaction-bound single-flight and final readiness ACK'}


def self_test():
    d = {k: False for k in BLOCKERS}
    d.update(boot_id=43, firmware='V8.3.7', pending_wait_open_mask=0,
             pending_wait_close_mask=0,
             motors=[{'motor':n,'fault_latched':False,'fault_reason':0,
                      'door_closed_verified':True,'door_cycle_pending':False,
                      'enn_disabled_verified':True,'step_output_active':False,
                      'auto_recovery_budget_exhausted':False} for n in (7,10)])
    failure = {'type':'remote_purchase_ack','results':[{'id':5, 'ok':False,'error':'revolver positioning failed'}]}
    assert ack_assessment(failure,5,d)['decision'] == 'PRE_PULSE_CANDIDATE'
    p=json.loads(json.dumps(d));p['pending_wait_open_mask']=1
    assert ack_assessment(failure,5,p)['decision'] == 'STOP'
    ack={'type':'remote_purchase_ack','warning':'at_least_one_revolver_door_never_opened; verify_delivery',
         'results':[{'id':5,'ok':True,'access_unconfirmed':True}]}
    assert ack_assessment(ack,5,d)['decision'] == 'STOP'
    for code in range(1,8):
        p=json.loads(json.dumps(d));p['motors'][0]['fault_latched']=True;p['motors'][0]['fault_reason']=code
        assert ack_assessment(failure,5,p)['decision'] == 'STOP_RECOVER_FIRST'
        label=evaluate(p)['motors'][0]['policy']
        assert ('INSPECTION' in label if code in INSPECTION_REQUIRED else 'SOFTWARE' in label)
    p=json.loads(json.dumps(d));p['global_fault']=True
    assert ack_assessment(failure,5,p)['decision'] == 'STOP'
    print('PASS: global lockout, faults 1..7, pre-pulse vs post-pulse, pending masks and ACK analysis')


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    opts=cli.add_mutually_exclusive_group(required=True)
    opts.add_argument('--status', action='store_true', help='Read-only authenticated ESP32 status and classification')
    opts.add_argument('--ack-file', type=Path, help='Inspect one saved final ACK; uses read-only controller status')
    opts.add_argument('--self-test', action='store_true', help='Offline tests; no network requests')
    cli.add_argument('--item',type=int,choices=range(5,22),metavar='5..21')
    args=cli.parse_args()
    if args.self_test:
        self_test();return
    try:
        if args.ack_file and args.item is None:
            cli.error('--ack-file requires --item')
        d=read_only_status()
        print('=== R3 RECOVERY V2 READ-ONLY AUDIT ===')
        print(json.dumps(evaluate(d), indent=2, sort_keys=True))
        if args.ack_file:
            ack=json.loads(args.ack_file.read_text(encoding='utf-8'))
            print('=== PURCHASE ACK CLASSIFICATION (NO ACTION SENT) ===')
            print(json.dumps(ack_assessment(ack,args.item,d),indent=2,sort_keys=True))
        print('NO fault clear, pending clear, motor restart, door open, purchase retry or stock update sent.')
    except (OSError, ValueError, KeyError, TimeoutError, socket.error) as exc:
        raise SystemExit('REFUSED: '+type(exc).__name__+': '+str(exc)) from exc


if __name__ == '__main__':
    main()
