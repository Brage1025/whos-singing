# Who's Singing?

An offline bird-call identifier for your walks. Record 30-60 seconds of audio on your phone, run one command, and get a list of which birds were singing, how much to trust each one, and a short note written by a local LLM.

Built for the Hacktoberfest Open-Source AI Challenge, Week 1: **Touch Grass**.

**Demo:** [Watch it run with Wi-Fi off](https://youtu.be/g2nv-HsVla0)

The screen is the shortest part of the experience: you spend your time outside listening, and only check the laptop at the end.

## Why open-source AI?

Two open models do the work, both running on your own machine:

- **Works with no signal.** After a one-time download of the models, no internet is needed. Tested with Wi-Fi turned off.
- **Your recordings stay with you.** Audio, locations, and your bird list are never uploaded anywhere.
- **Free to run.** No API keys, no per-request costs.
- **Swappable and inspectable.** The note-writing model is any model in [Ollama](https://ollama.com), chosen with `--model`. When a small model made mistakes, I could see exactly what it did, change the prompt, add checks, and compare models side by side. That isn't possible with a closed API.

## How it works

1. **[BirdNET-Analyzer](https://github.com/birdnet-team/BirdNET-Analyzer)** analyzes the recording in 3-second windows and scores each species. Passing your latitude/longitude (plus the current week of the year) filters it to species likely near you right now, which cuts false positives sharply.
2. The script merges the windows into one row per species and assigns a **confidence tier**.
3. Only confident and likely birds go to a **local LLM** (via Ollama), which writes a short note.
4. The note is **checked in code**. If the model invented, repeated, or dropped a bird, the script discards it and uses a plain template instead.
5. The session is appended to a **local CSV log**, building a private personal bird list.

### Confidence tiers

| Tier        | Rule                                                   | Meaning                        |
| ----------- | ------------------------------------------------------ | ------------------------------ |
| `confident` | 3+ detections and best score 60%+                      | Heard repeatedly and clearly   |
| `likely`    | 2+ detections with best score 50%+, or one hit at 80%+ | Probably there                 |
| `possible`  | anything else                                          | One weak hit, treat as a maybe |

A single high score isn't proof, which is why repeated detections count for more than one strong window.

## Requirements

- Python 3.9+ (3.11 or 3.12 recommended, since TensorFlow can lag behind the newest Python)
- About 3 GB of disk space (BirdNET model plus the LLM)
- [Ollama](https://ollama.com) (optional, only for the note)

## Install

```bash
# create and activate a virtual environment
python -m venv .venv

# Windows (PowerShell / cmd)
.venv\Scripts\activate
# Windows (Git Bash)
source .venv/Scripts/activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

For the optional note, install Ollama, then:

```bash
ollama pull qwen2.5:3b
```

The BirdNET model downloads automatically the first time you run the script. Do this while you still have internet.

## Usage

```bash
python whos_singing.py recordings/my-walk.mp3 --lat 59.91 --lon 10.75
```

Replace the coordinates with where you recorded. Without them, BirdNET checks against its full global species list and you get far more false positives.

| Option             | Default      | What it does                                    |
| ------------------ | ------------ | ----------------------------------------------- |
| `--lat`, `--lon`   | none         | Your location. Filters to species likely nearby |
| `--min-conf`       | 0.25         | Minimum confidence for a detection              |
| `--min-detections` | 1            | Hide species heard fewer times than this        |
| `--model`          | `qwen2.5:3b` | Ollama model used for the note                  |
| `--no-note`        | off          | Skip the LLM note                               |
| `--no-log`         | off          | Don't save to `bird_log.csv`                    |

Supported audio formats include wav, mp3, and flac.

## Example output

```
Who's singing:

  Great Tit                  confident  best  90.7%  (6 detections)  Parus major
  Eurasian Siskin            possible   best  36.7%  (1 detection)   Spinus spinus

Field note (written by qwen2.5:3b):
  On this walk, I clearly heard a Great Tit, six times.

Saved to bird_log.csv. Your personal bird list: 1 species.
```

The siskin is labelled `possible` and left out of the note, because a single 36.7% hit is weak evidence.

## What I learned about small local models

The LLM only polishes wording. BirdNET does the identification. Getting the note right took several rounds, and each failure led to a safeguard:

1. **First version:** the model was handed only species names and invented facts (it described an American bird's call for a European one, and gave a Great Tit "brightly colored" features it doesn't have).
2. **Grounded prompt:** facts stopped, but the note became clumsy and hedged confident birds with "probably".
3. **Prompt with an example:** the model copied the example's Wren into the note, a bird that was never detected.
4. **Stricter validation:** it then repeated a real bird twice. The check now rejects notes that miss a bird, repeat one, mention a bird that wasn't detected, or use "clearly" or "probably" wording that doesn't match the tiers.

Certainty wording is now decided in code, not by the model.

### Model comparison (same recording, final prompt and check)

| Model           | Result                                                                  |
| --------------- | ----------------------------------------------------------------------- |
| `qwen2.5:3b`    | Passed the check 5 out of 5 runs                                        |
| `llama3.2` (3B) | Passed the check 1 out of 5 runs; the other 4 fell back to the template |

This is a small test on one recording, not a benchmark. The point is that with open models you can run this comparison yourself in minutes, and swap the winner in with one flag.

## Testing on real recordings

I couldn't get out on a walk this week, so I tested on real-world bird recordings instead: an old recording of my own and two shared by a friend (who prefers to stay anonymous), all run with Wi-Fi off. Only the last recording has ground truth, because my friend knew what was there.

**Recording from a friend's trip** (`--lat 59.91 --lon 10.75`):

```
  Great Tit                  confident  best  66.2%  (4 detections)
  Great Spotted Woodpecker   likely     best  80.1%  (2 detections)
  Eurasian Blue Tit          possible   best  41.2%  (2 detections)
```

**Ground truth.** The recording was made just outside Oslo. My friend, who was there, saw both the Great Tits and the Eurasian Blue Tits, and heard the Great Spotted Woodpecker without seeing it. So all three detections were real.

| Species                  | Tool said                      | Actually there? |
| ------------------------ | ------------------------------ | --------------- |
| Great Tit                | `confident` (4 detections)     | Yes, seen       |
| Great Spotted Woodpecker | `likely` (2 detections, 80%)   | Yes, heard      |
| Eurasian Blue Tit        | `possible` (2 detections, 41%) | Yes, seen       |

**What this showed:**

- BirdNET found all three birds, and none of its detections were wrong.
- The tiers were cautious in a way that cost something. The blue tit was real, but two weak detections (best score 41%) put it in `possible`, so the written note left it out. I had made the `likely` rule stricter after an earlier version labelled that same bird `likely`, and in hindsight that pushed a true bird down a tier. I haven't re-tuned the threshold, because one recording is too little to tune on. The trade-off is deliberate: I'd rather the tool under-claim than confidently name a bird that wasn't there. The thresholds are my judgment, not validated.
- Every bird is still printed in the list with its tier and score. Only the written note is selective.
- The tool is built for use on a trail with no signal, but I haven't field-tested it outdoors myself yet. These results come from recordings, run offline.

## Privacy

`bird_log.csv` records when and where you were, so it is listed in `.gitignore` and stays on your machine.

## Troubleshooting

- **`ollama: command not found`**: restart VS Code or your terminal after installing Ollama, or try PowerShell.
- **"model not found. Run: ollama pull ..."**: pull the model named in the message.
- **"Ollama not reachable"**: make sure the Ollama app is running (or run `ollama serve`). The bird list still works without it.
- **No birds detected**: try `--min-conf 0.1`, and make sure the recording is loud and clear enough.
- **`pip install` fails on Windows**: check your Python version and try 3.11 or 3.12.

## Credits and licenses

- [BirdNET](https://github.com/birdnet-team/BirdNET-Analyzer) by the K. Lisa Yang Center for Conservation Bioacoustics (Cornell Lab of Ornithology) and Chemnitz University of Technology. The model is open-weight but has non-commercial license conditions, so check their repository for the current terms.
- [Ollama](https://ollama.com), [Qwen2.5](https://huggingface.co/Qwen), and [Llama 3.2](https://www.llama.com/llama3_2/license/), each under its own license.
- The code in this repository is released under the MIT License. See `LICENSE`.
