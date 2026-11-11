"""Picks which Ollama model to use when the config says model = "auto".

The idea is simple: look at everything installed in Ollama, throw out what
can't chat, and take the biggest model (most parameters) that still fits on
the graphics card. Bigger usually means better translations and fewer grammar
mistakes. "Fits" matters because a model that spills out of VRAM into normal
RAM gets several times slower, and a reply that shows up 20 seconds late is
useless in a live conversation

Everything in here is plain functions over the JSON Ollama returns, so it can
be tested without Ollama running
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GB = 1024 ** 3

# Below this much video memory we assume the model runs on the CPU. Integrated
# graphics report a few hundred MB here, which is useless for a language model
_MIN_USEFUL_VRAM = 2 * GB

# On the CPU anything past roughly an 8B model at 4 bit is too slow to keep up
# with someone talking, so that's the cap when there is no usable GPU
_CPU_BUDGET = int(5.5 * GB)

# Whisper large-v3-turbo sits on an NVIDIA card too, about 1.6 GB plus the
# CUDA context, so 2 GB. On AMD or Intel Whisper runs on the CPU, so the card
# only needs room for Windows and the desktop
_RESERVE_NVIDIA = 2 * GB
_RESERVE_OTHER = 1 * GB

# A loaded model needs a bit more than its file size: the context cache and
# Ollama's own buffers. 10% plus a flat 400 MB covers it at num_ctx 2048.
# Measured on an 8 GB card, this lets an 8B model run next to Whisper
_LOAD_OVERHEAD = 1.10
_LOAD_EXTRA = 400 * 1024 ** 2

_PARAMS_RE = re.compile(r"^\s*(?:(\d+)\s*x\s*)?([\d.]+)\s*([KMBT])?\s*$", re.IGNORECASE)
_SCALE = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


@dataclass
class Candidate:
    name: str
    size: int          # bytes on disk
    params: float      # parameter count, 0 when Ollama didn't say
    family: str


def parse_params(text: str) -> float:
    """Turn Ollama's parameter_size ("4.0B", "567M", "8x7B") into a number"""
    match = _PARAMS_RE.match(text or "")
    if not match:
        return 0.0
    experts, number, unit = match.groups()
    value = float(number) * _SCALE[(unit or "").upper()]
    if experts:
        # 8x7B style mixture models list the per expert size
        value *= int(experts)
    return value


def _is_cloud(entry: dict) -> bool:
    # Ollama can list "cloud" models that actually run on someone else's
    # server. Picking one by accident would send every conversation off this
    # machine, so they are never chosen automatically
    name = str(entry.get("name", "")).lower()
    return (
        bool(entry.get("remote_host") or entry.get("remote_model"))
        or name.endswith(":cloud")
        or "-cloud" in name
    )


def _is_embedding(entry: dict) -> bool:
    # Embedding models turn text into numbers for search, they can't reply.
    # They are mostly BERT based and usually say "embed" in the name
    details = entry.get("details") or {}
    families = [str(details.get("family", ""))] + [str(f) for f in details.get("families") or []]
    name = str(entry.get("name", "")).lower()
    return "embed" in name or any("bert" in f.lower() for f in families)


def candidates(tags: list[dict]) -> list[Candidate]:
    """The installed models that could be used for chat, from /api/tags"""
    found: list[Candidate] = []
    for entry in tags:
        name = entry.get("name") or entry.get("model")
        if not name or _is_cloud(entry) or _is_embedding(entry):
            continue
        details = entry.get("details") or {}
        found.append(Candidate(
            name=str(name),
            size=int(entry.get("size") or 0),
            params=parse_params(str(details.get("parameter_size", ""))),
            family=str(details.get("family", "")),
        ))
    return found


def memory_budget(vram: int, vendor: str) -> tuple[int, str]:
    """How many bytes a model may take up, and a sentence saying why"""
    if vram < _MIN_USEFUL_VRAM:
        return _CPU_BUDGET, "no graphics card with enough memory, sized for the CPU"
    reserve = _RESERVE_NVIDIA if vendor == "nvidia" else _RESERVE_OTHER
    budget = max(0, vram - reserve)
    return budget, f"{vram / GB:.0f} GB of video memory, {budget / GB:.1f} GB free for the model"


def fits(candidate: Candidate, budget: int) -> bool:
    return candidate.size * _LOAD_OVERHEAD + _LOAD_EXTRA <= budget


def strength(candidate: Candidate) -> tuple[float, int, str]:
    """Sort key, bigger is better.

    Parameter count first. When two models have the same count the bigger
    file wins, since that usually means a less squashed (more accurate)
    version of the same model. The name is last only so the order is stable
    """
    params = candidate.params or candidate.size / 0.6  # rough guess at 4-5 bit
    return (params, candidate.size, candidate.name)


def choose(found: list[Candidate], budget: int) -> tuple[Candidate | None, str]:
    """Pick the strongest model that fits, with a short note about the choice"""
    if not found:
        return None, "no chat models installed in Ollama"

    fitting = [c for c in found if fits(c, budget)]
    if fitting:
        best = max(fitting, key=strength)
        skipped = [c.name for c in found if c not in fitting]
        note = f"picked {best.name}, the strongest model that fits"
        if skipped:
            note += f" (too big to run quickly here: {', '.join(sorted(skipped))})"
        return best, note

    # Nothing fits. Take the smallest one, it is the one that will run the
    # least badly, and say so in the log so it's not a mystery why it's slow
    smallest = min(found, key=lambda c: (c.size, c.name))
    return smallest, (f"nothing installed fits in memory, using the smallest one "
                      f"({smallest.name}), expect slow replies")
