#!/usr/bin/env python3
"""
Who's Singing? - offline bird ID for your walks.

Two open models, both running on your own machine:
  1. BirdNET (open-weight audio classifier) identifies the birds.
  2. A local LLM via Ollama (swappable with --model) writes a short note,
     grounded only in what BirdNET found.

Setup (one time, needs internet):
    pip install birdnet-analyzer
    ollama pull qwen2.5:3b      # optional, for the field note

Usage (fully offline afterwards):
    python whos_singing.py recording.mp3 --lat 59.91 --lon 10.75
    python whos_singing.py recording.mp3 --lat 59.91 --lon 10.75 --min-conf 0.1
    python whos_singing.py recording.mp3 --no-note --no-log
"""

import argparse
import csv
import datetime
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

LOG_FILE = Path("bird_log.csv")


def run_birdnet(audio: Path, out_dir: Path, lat, lon, min_conf):
    """Run BirdNET-Analyzer locally and write CSV results to out_dir."""
    cmd = [
        sys.executable, "-m", "birdnet_analyzer.analyze",
        str(audio), "-o", str(out_dir),
        "--min_conf", str(min_conf),
        "--rtype", "csv",
    ]
    if lat is not None and lon is not None:
        week = datetime.date.today().isocalendar()[1]
        cmd += ["--lat", str(lat), "--lon", str(lon), "--week", str(week)]
    subprocess.run(cmd, check=True)


def parse_results(out_dir: Path):
    """Collapse per-3-second detections into one row per species."""
    best = defaultdict(lambda: {"conf": 0.0, "count": 0, "sci": ""})
    for f in out_dir.rglob("*.csv"):
        with open(f, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                name = row.get("Common name")
                if not name:
                    continue
                conf = float(row.get("Confidence", 0))
                b = best[name]
                b["count"] += 1
                b["sci"] = row.get("Scientific name", "")
                b["conf"] = max(b["conf"], conf)
    return sorted(best.items(), key=lambda kv: (kv[1]["count"], kv[1]["conf"]), reverse=True)


def tier(d):
    """Label how much to trust a species. Repeated detections matter more
    than one high-scoring window."""
    if d["count"] >= 3 and d["conf"] >= 0.6:
        return "confident"
    if (d["count"] >= 2 and d["conf"] >= 0.5) or d["conf"] >= 0.8:
        return "likely"
    return "possible"


EXAMPLE_BIRDS = ("Robin", "Wren")  # birds used in the prompt example


def article(name):
    """'a' or 'an' for a bird name (Eurasian/European start with a 'y' sound)."""
    if name.lower().startswith(("eu", "uni")):
        return "a"
    return "an" if name[0].lower() in "aeiou" else "a"


def fix_articles(note, names):
    """Small models fumble a/an; set them correctly from the real names."""
    for n in names:
        def repl(m):
            a = article(n)
            return (a.capitalize() if m.group(1)[0].isupper() else a) + " " + n
        note = re.sub(rf"\b(an?) {re.escape(n)}", repl, note, flags=re.IGNORECASE)
    return note


def template_note(species):
    """Plain, always-correct note built directly from the detections."""
    parts = []
    for n, d in species:
        times = f"{d['count']} time{'s' if d['count'] != 1 else ''}"
        verb = "clearly heard" if tier(d) == "confident" else "probably heard"
        parts.append(f"{verb} {article(n)} {n} ({times})")
    return "On this walk I " + ", and I ".join(parts) + "."


def note_is_valid(note, species):
    """Reject notes that repeat a bird, miss a bird, mention an example bird,
    or use certainty wording that doesn't match the detections."""
    low = note.lower()
    names = [n for n, _ in species]
    for n in names:
        if low.count(n.lower()) != 1:  # each detected bird exactly once
            return False
    for b in EXAMPLE_BIRDS:
        if b.lower() in low and not any(b.lower() in n.lower() for n in names):
            return False
    has_confident = any(tier(d) == "confident" for _, d in species)
    has_likely = any(tier(d) == "likely" for _, d in species)
    if "clearly" in low and not has_confident:
        return False
    if "probably" in low and not has_likely:
        return False
    return True


def field_note(species, model):
    """Ask a local Ollama model for a short note grounded in the detections.
    Certainty wording is decided in code, the prompt has an example to imitate,
    and the result is checked; if it fails, a plain template is used instead.
    Returns (note, source)."""
    lines = []
    for n, d in species:
        times = f"{d['count']} time{'s' if d['count'] != 1 else ''}"
        verb = "I clearly heard" if tier(d) == "confident" else "I probably heard"
        lines.append(f"- {verb} {article(n)} {n} ({times})")
    facts = "\n".join(lines)
    prompt = (
        f"Turn these walk notes into a friendly summary of exactly {len(species)} sentence(s), one per bird.\n\n"
        f"{facts}\n\n"
        "Rules: mention each bird exactly once. Keep the wording 'clearly heard' or 'probably heard' exactly "
        "as given. Use only the birds and counts above. Do not describe "
        "songs, colours, behaviour or habitat. Do not add any other bird.\n\n"
        "Example input:\n- I clearly heard a Robin (4 times)\n"
        "- I probably heard a Wren (2 times)\n"
        "Example output: On this walk I clearly heard a Robin, four times in "
        "all. I also probably heard a Wren, twice.\n"
        "(The example birds are NOT part of this walk.)\n\n"
        "Summary:"
    )
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=json.dumps({
            "model": model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.2},
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        note = json.load(r)["response"].strip()
    if note_is_valid(note, species):
        note = fix_articles(note, [n for n, _ in species])
        return note, f"written by {model}"
    return template_note(species), f"{model} output failed the check, used template"


def log_session(audio, lat, lon, species):
    """Append this walk to a local CSV. Stays on your machine."""
    new = not LOG_FILE.exists()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["time", "file", "lat", "lon", "species", "scientific",
                        "best_confidence", "detections", "tier"])
        for name, d in species:
            w.writerow([now, audio.name, lat, lon, name, d["sci"],
                        round(d["conf"], 3), d["count"], tier(d)])


def life_list_size():
    """Count distinct species ever logged (confident/likely only)."""
    if not LOG_FILE.exists():
        return 0
    with open(LOG_FILE, newline="", encoding="utf-8") as fh:
        return len({r["species"] for r in csv.DictReader(fh)
                    if r["tier"] in ("confident", "likely")})


def main():
    p = argparse.ArgumentParser(description="Offline bird call identifier")
    p.add_argument("audio", type=Path, help="wav/mp3/flac recording")
    p.add_argument("--lat", type=float, help="latitude (filters to local species)")
    p.add_argument("--lon", type=float, help="longitude")
    p.add_argument("--min-conf", type=float, default=0.25)
    p.add_argument("--min-detections", type=int, default=1,
                   help="hide species heard fewer times than this")
    p.add_argument("--model", default="qwen2.5:3b", help="Ollama model for the note")
    p.add_argument("--no-note", action="store_true", help="skip the LLM note")
    p.add_argument("--no-log", action="store_true", help="don't save to bird_log.csv")
    a = p.parse_args()

    if not a.audio.exists():
        sys.exit(f"File not found: {a.audio}")

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        run_birdnet(a.audio, out, a.lat, a.lon, a.min_conf)
        species = parse_results(out)

    species = [(n, d) for n, d in species if d["count"] >= a.min_detections]

    if not species:
        print("No birds detected above the thresholds. Try --min-conf 0.1.")
        return

    print("\nWho's singing:\n")
    for name, d in species:
        s = "s" if d["count"] != 1 else ""
        print(f"  {name:<26} {tier(d):<10} best {d['conf']*100:5.1f}%  "
              f"({d['count']} detection{s})  {d['sci']}")

    solid = [(n, d) for n, d in species if tier(d) in ("confident", "likely")]

    if not a.no_note:
        if not solid:
            print("\n(No confident or likely detections, so no field note.)")
        else:
            try:
                note, source = field_note(solid, a.model)
                print(f"\nField note ({source}):\n  {note}")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    print(f"\n(Skipped field note: model '{a.model}' not found. "
                          f"Run: ollama pull {a.model})")
                else:
                    print(f"\n(Skipped field note: Ollama error - {e})")
            except Exception as e:
                print(f"\n(Skipped field note: Ollama not reachable - {e})")

    if not a.no_log:
        log_session(a.audio, a.lat, a.lon, species)
        print(f"\nSaved to {LOG_FILE}. Your personal bird list: "
              f"{life_list_size()} species.")


if __name__ == "__main__":
    main()