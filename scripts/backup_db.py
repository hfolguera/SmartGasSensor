#!/usr/bin/env python3
"""Backup coherent de SQLite, incloent dades al WAL, mentre l'aplicació funciona."""
import argparse
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)
parser.add_argument("--destination", required=True)
args = parser.parse_args()
source = Path(args.source).resolve()
destination = Path(args.destination).resolve()
if not source.is_file() or destination.exists() or source == destination:
    raise SystemExit("L'origen ha d'existir i la destinació ha de ser un fitxer nou")
destination.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(destination) as dst:
    src.backup(dst)
print("Backup creat:", destination)
