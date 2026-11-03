#!/usr/bin/env python3
"""Verify the audit trail from the command line:  python3 verify_audit.py [data_dir]"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afpe.audit import AuditLog
from afpe.crypto_store import load_keys
d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_, akey, _ = load_keys(d)
r = AuditLog(d, akey).verify(); print(json.dumps(r)); sys.exit(0 if r["ok"] else 1)
