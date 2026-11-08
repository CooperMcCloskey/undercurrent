# Story headlines and summaries from a small language model

An implementation brief for an agent. It replaces the story header in the web app's story panel (the title and the line under it) with a neutral headline and a one-sentence summary written by a small instruction-tuned model from Hugging Face.

**Status:** not started. **Code owner:** workstream B (`stories/`, Cooper). Agree the plan with Cooper before you change `stories/export.py`. A standalone prototype (step 2) touches no one's code. The web part (step 6) is workstream D's.

## The problem

The story panel's header comes from the exporter, `pipeline/undercurrent/stories/export.py`:

- **Title:** a single scraped headline, copied word for word. `Exporter.titles()` picks the week's most central English headline (`Source.english_titles()`). Stories without one get `fallback_title()`: their top names and place ("Emmanuel Macron · Paris"). So the header is whatever one outlet wrote: clickbait, a site name, the wrong register.
- **Summary:** never written. `Exporter.details()` sets `summary=None` on every `StoryDetail`. The panel already shows a summary when one exists (`web/src/story/StoryPanel.tsx`, the line under the title).

## What to build

```
story's top English headlines (up to 10)  ──►  small instruct LM  ──►  {"headline", "summary"}
                                                                       │ checked, cached
                                                                       ▼
                                                 StoryDetail.title and StoryDetail.summary
```

- **Generate it in the pipeline, at export time.** Do it once per story, and never in the API or the browser: a forward pass per click is too slow, and the API serves the exported files unchanged.
- **No contract change.** `StoryDetail.title` and `StoryDetail.summary` already exist in `contracts.py`.
- **No new heavy dependency.** `transformers` (5.22) and `torch` (2.15) are already in `uv.lock`, pulled in by `sentence-transformers`. Declare `transformers` directly with `uv add transformers` and commit the lockfile on its own, as `AGENTS.md` says.

### Model

| Model | Size | Use |
|---|---|---|
| **`Qwen/Qwen2.5-0.5B-Instruct`** (default) | 0.5B, ~1 GB | Good enough at following the format and sticking to the input. About 1 s a story on CPU; much faster batched on a GPU |
| `HuggingFaceTB/SmolLM2-360M-Instruct` | 360M | Faster, but strays from the facts more often |
| `Qwen/Qwen2.5-1.5B-Instruct` | 1.5B | Better writing; use it on the GPU VMs |

All three are Apache-2.0. Keep the model name in one constant and include it in the cache key (below), so changing the model regenerates everything.

## Steps

### 1. Dependency

```bash
uv add transformers
```

Commit `pyproject.toml` and `uv.lock` together in their own commit (`stories: declare transformers`) and push straight away.

### 2. `pipeline/undercurrent/stories/headline.py`

The module takes `story_id -> its headlines` and returns `story_id -> (headline, summary)`, leaving out any output that fails the checks. The callable has the same shape as the exporter's `Classifier`, so it plugs in the same way. A starting point:

```python
"""A story's headline and one-sentence summary, written by a small instruct model from its own
English headlines. Run at export time (stories/export.py), never in the API.

    uv run python -m undercurrent.stories.headline --sample 20     # try it on real stories
"""

from undercurrent import config  # noqa: F401  (before transformers: models cache in data/cache/)

import json
import re

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
PROMPT_VERSION = 1  # bump when PROMPT changes; it's part of the cache key
HEADLINES_IN = 10
PROMPT = """Headlines about one news story:
{headlines}

Write a neutral headline (at most 12 words) and a one-sentence summary (at most 30 words) of
what the story is about. Use only facts in the headlines. Answer as JSON:
{{"headline": "...", "summary": "..."}}"""


class Headliner:
    def __init__(self, model: str = MODEL) -> None:
        self.model = model
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tok = AutoTokenizer.from_pretrained(model, padding_side="left")
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.lm = AutoModelForCausalLM.from_pretrained(model, dtype=dtype).to(self.device).eval()

    @torch.inference_mode()
    def __call__(self, stories: dict[str, list[str]], batch: int = 16) -> dict[str, tuple[str, str]]:
        ids, out = list(stories), {}
        for i in range(0, len(ids), batch):
            chunk = ids[i : i + batch]
            prompts = [
                self.tok.apply_chat_template(
                    [{"role": "user", "content": PROMPT.format(
                        headlines="\n".join(f"- {h}" for h in stories[s][:HEADLINES_IN]))}],
                    tokenize=False,
                    add_generation_prompt=True,
                )
                for s in chunk
            ]
            enc = self.tok(prompts, return_tensors="pt", padding=True).to(self.device)
            # Greedy decoding: the same input always gives the same header.
            gen = self.lm.generate(**enc, max_new_tokens=80, do_sample=False)
            for sid, g in zip(chunk, gen[:, enc.input_ids.shape[1] :]):
                if ok := checked(self.tok.decode(g, skip_special_tokens=True), stories[sid]):
                    out[sid] = ok
        return out


def checked(text: str, headlines: list[str]) -> tuple[str, str] | None:
    """The model's (headline, summary), or None if it isn't usable: not JSON, empty, too long, or
    naming something that isn't in the input headlines (a guard against invented names)."""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict):
        return None
    headline, summary = str(d.get("headline", "")).strip(), str(d.get("summary", "")).strip()
    if not headline or not summary or len(headline.split()) > 16 or len(summary.split()) > 45:
        return None
    source = " ".join(headlines).lower()
    for text_part in (headline, summary):
        names = re.findall(r"\b[A-Z][\w'-]{2,}\b", text_part)
        if any(n.lower() not in source for n in names[1:]):  # names[0]: sentence-initial capital
            return None
    return headline, summary
```

Also add the following:

- **A cache,** `data/cache/headlines.parquet`, with columns `key, headline, summary`. The key is a SHA-1 of `MODEL`, `PROMPT_VERSION` and the joined input headlines. Wrap `Headliner` so it only generates for keys that aren't cached yet, then appends. Re-running an export must not regenerate anything.
- **A CLI**, `--sample N`, which takes N stories from `data/processed` and prints the current title next to the generated headline and summary, plus seconds per story. Use it to judge quality before wiring anything in. Put a few example rows in `PLAN.md` notes.

### 3. Exporter integration (`stories/export.py`, B's file: agree it first)

Follow the existing `classify` pattern exactly:

- A type alias next to `Classifier`: `HeadlineWriter = typing.Callable[[dict[str, list[str]]], dict[str, tuple[str, str]]]`.
- `Exporter.__init__` and `run()` take `headline: HeadlineWriter | None = None`.
- In `Exporter.details()`, get the inputs with `self.src.category_titles(story_ids, end, HEADLINES_IN)`. That returns the most central English headlines per story, up to the export's end. Call the writer once for all stories. Where it returned something, set `StoryDetail.title` and `StoryDetail.summary`. Otherwise keep today's title and `summary=None`.
- Count outcomes in `self.stats`, as the exporter already does: `"headlines: written"`, `"headlines: rejected by the checks"`, `"headlines: no English headlines"`.
- CLI: **`--ai-headlines`**, off by default to begin with (the model is a ~1 GB download). In `main()`, import `Headliner` only when the flag is set, as `classify_titles` is imported only when needed.

### 4. Point in time (read before touching per-day titles)

A `StoryDetail` is written from everything up to `--end`, as the module docstring already says. So a generated summary can describe developments after the day a user has scrubbed to. That's acceptable for the detail, but **don't use the detail's headline as the per-day title in snapshots.** `StorySummary.title` must stay "as of day t" (`AGENTS.md`, point-in-time correctness).

If per-day generated titles are wanted later, generate them per story per week, from `english_titles()` limited to that week, and cache them by `(story_id, week)`. That costs one call per story-week instead of one per story. Measure the speed first (step 2's `--sample`).

### 5. Tests (`pipeline/tests/`)

**Never download a model in tests.**

- `test_story_headline.py`: unit tests for `checked()`. Cover valid JSON; JSON wrapped in extra text; invalid JSON; an empty field; a too-long headline; a name that isn't in the inputs (rejected); a name that is (kept).
- `test_stories_export.py`: inject a fake `HeadlineWriter` (a plain function) into the exporter. Assert that the detail's title and summary come from it, and that a story the fake leaves out keeps the old title with `summary=None`.
- `npm run check` must pass.

### 6. Web (workstream D)

The panel's `<h2>` shows `story.title`, the snapshot's per-day title. Once details carry a generated headline, show it in the panel, with the day's title as a fallback:

- `web/src/story/useArticles.ts`: also return `title: query.data?.title ?? null`.
- `web/src/story/StoryPanel.tsx`: take a `headline` prop and render `headline ?? story.title`. The summary line already renders `summary`.
- `web/src/App.tsx`: pass `selectedArticles.title`.

Leave the marker tooltip and the legend on the per-day title (point in time, step 4).

### 7. Running it on real data

- On a GPU VM (`vm.sh frontend`, or Cooper's merge), add `--ai-headlines` to the `undercurrent.stories.export` command. On an L4 with the 0.5B model, a few thousand stories should take minutes. Check with `--sample` first.
- On CPU it works, but budget about a second per story.
- If the Hugging Face download fails in a sandbox, see "Sandboxed agents" in `AGENTS.md` (`SSL_CERT_FILE`, `HF_HUB_DISABLE_XET=1`, then `HF_HUB_OFFLINE=1` once cached).

## Done means

- [ ] `uv run python -m undercurrent.stories.headline --sample 20` prints old and new headers side by side, with the time per story, and most new headers read better than the old ones.
- [ ] `uv run python -m undercurrent.stories.export --ai-headlines ...` writes a title and summary into each story detail it can, and logs how many were written, rejected and skipped.
- [ ] Re-running the export generates nothing new (everything comes from the cache).
- [ ] Without `--ai-headlines`, the export is unchanged byte for byte.
- [ ] `npm run check` passes; no test downloads a model.
- [ ] The web panel shows the generated headline and summary for a story that has them, and the old title for one that doesn't.
- [ ] `PLAN.md` is updated in the owner's section, with a note on speed and a few before/after examples.
