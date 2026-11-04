# live-translator

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)

Translates voice chat while you play, in a small window that sits on top of the game. It reads
what comes out of your speakers, so it hears the people in your Discord call or game lobby and
not you.

Each line gives you the language, what they said, the translation, and three replies you can
send back in their language. The box at the bottom does the other direction: type English, get
it in theirs.

It needs an NVIDIA card to keep up properly. AMD and Intel work, slower, see
[AMD and Intel graphics](#amd-and-intel-graphics).

![The overlay](docs/overlay.png)

## Start it

Double-click `live-translator.bat`. Right-click that file, then Send to, then Desktop, if you
want a shortcut somewhere easier to reach.

From PowerShell:

```powershell
cd C:\path\to\live-translator
.\run.ps1
```

The X in the corner closes it.

## Install

Windows 10 or 11, and 8 GB of disk free for the models.

```powershell
winget install --id Python.Python.3.11 --scope user
winget install --id Ollama.Ollama
```

Close PowerShell and open it again so the new commands are on your PATH, then:

```powershell
git clone https://github.com/codext4fn-afk/live-translator.git
cd live-translator
.\setup.ps1
```

`setup.ps1` builds the virtual environment, installs the packages, pulls the language model and
writes a config file. Around ten minutes, nearly all of it downloading. It checks the result at
the end and names anything that is missing.

The first launch downloads the speech model, 1.6 GB. Launches after that take about a second.

If something went wrong, `.\run.ps1 --check` tests each part and says which one failed.

## Updating

```powershell
cd C:\path\to\live-translator
git pull
.\setup.ps1
```

Running `setup.ps1` again is safe. Your settings survive it.

`config.toml` is part of the repo, so if you edited it and a pull wants to change that same
file, git stops rather than overwrite you. Run `git stash`, then `git pull`, then
`git stash pop` to get your values back.

Installs from before hardware detection have `device = "cuda"` in `config.toml`. On an AMD or
Intel machine that puts a GPU-sized model on the CPU, which is the install everyone thought was
broken. `setup.ps1` rewrites those keys to `auto` and keeps your old file as
`config.toml.backup`. Check with `.\run.ps1 --check`: the `speech backend` line says what you
actually ended up with.

## Using it

Click a reply to copy it, then paste it into your chat. Under each reply is a pronunciation line
written in English letters, so Japanese or Arabic is still readable out loud.

Drag the window from anywhere that is not a button. The corner resizes it. Buttons across the
top are pause, settings, clear, dimmer, brighter, close. Settings holds the window colour and
the language you want everything translated into.

## Settings

Everything lives in `config.toml` and every key has a comment next to it. The ones people
change:

| Setting | Default | What it does |
| --- | --- | --- |
| `languages.my_language` | English | What you want things translated into |
| `languages.ignore_languages` | `["en"]` | Languages skipped completely |
| `languages.allowed` | 20 languages | Languages it will try to detect. Cut this to the ones you hear |
| `audio.silence_ms` | 700 | How long a pause has to be to end a sentence |
| `suggest.count` | 3 | How many replies to write. Two is faster |
| `suggest.model` | `qwen3:4b` | Any model you have in Ollama |
| `ui.font_size` | 13 | Text size, everything scales with it |

### Languages

All 100 languages Whisper knows are available, and they are all in the Settings picker.
`languages.allowed` starts with 20 of them, including Filipino, Malay, Vietnamese, Thai,
Korean and Chinese. Add any code you want to that list.

The codes are Whisper's own and some are not what you would guess. Filipino is `tl`, not
`fl` or `ph`. Malay is `ms`, and `my` is Burmese, not Malaysian. Names work too, so
`allowed = ["filipino", "malay"]` is fine, and a wrong code gets corrected or dropped with a
line in the log instead of stopping transcription.

### English does not show up

That is deliberate. You already speak it, so English is thrown out before anything else runs,
which is also where most of the speed comes from. Take `"en"` out of `ignore_languages` if you
want English lines with replies. Changing your language in Settings edits that list for you.

### Hearing only the voice chat

It captures whatever your speakers are playing, music and game audio included. Send the voice
chat somewhere separate to fix that:

1. Settings, then System, then Sound, then Volume mixer.
2. Set Discord's output device to something other than your speakers. VB-Audio Virtual Cable is
   free and works well here.
3. Name that device in `config.toml`:

```toml
[audio]
device = "CABLE"
```

`.\run.ps1 --list-devices` prints the names you can use.

## AMD and Intel graphics

faster-whisper is built on CTranslate2, which only accelerates on CUDA. Without an NVIDIA card
transcription runs on your CPU.

Six core CPU, 5.7 second clip:

| Model | Time | Speed |
| --- | --- | --- |
| large-v3-turbo | 13.3 s | 2.3x slower than real time |
| small | 3.3 s | 0.58x, keeps up |
| base | 1.2 s | 0.21x |
| tiny | 0.5 s | 0.08x, least accurate |

Anything slower than real time falls further behind the longer someone talks, which is why
large-v3-turbo on a CPU looks like it has hung. It picks `small` now when there is no CUDA
device.

Cutting the language list down makes a bigger difference than anything else you can change.
Working out which of thirteen languages someone is speaking costs a whole extra pass over the
audio:

```toml
[languages]
allowed = ["fr"]
```

Same clip, 3.14 s with thirteen languages, 1.97 s with one.

If that is still not enough, use a smaller model:

```toml
[asr]
model = "base"
```

### Why not the GPU

ROCm, DirectML and Vulkan all come up for this. None of them work:

- ROCm: CTranslate2 has no ROCm build, and AMD's ROCm for Windows covers very few cards anyway.
- DirectML: it loads Whisper and then transcribes wrong. A clip the CPU read as "Hello there!
  Can you hear me clearly on this" came back as "Hello detrimental detrimental detrimental". It
  was slower too.
- Vulkan: works in whisper.cpp, but the project ships no Vulkan build for Windows x64, only CPU,
  BLAS and CUDA. Using it would mean compiling it yourself.

Replies are a different story. Those come from Ollama, which does support AMD through ROCm, so
if your card is on
[Ollama's list](https://github.com/ollama/ollama/blob/main/docs/gpu.md) that half already runs
on your GPU.

## Speed

On an RTX 3070:

| | |
| --- | --- |
| Window opens | 0.4 s |
| Ready to listen | 3 to 6 s |
| End of a sentence detected | 0.7 s |
| Transcribed | 0.5 s |
| Translation appears | 0.4 s later |
| Replies appear | 1.3 to 2.2 s |
| Typed translation | 0.4 to 0.9 s |

Transcribing is quick. Writing the replies is the part you wait for, and it scales with how much
text the model produces, so `suggest.count` moves the number more than anything else.

It holds 2 GB for the speech model and 3 GB for the language model, which leaves 3 GB free on an
8 GB card. Set `asr.compute_type` to `int8` if a game wants that memory back.

## When it breaks

Run `.\run.ps1 --check` first. It tests each part and names the problem.

**Nothing appears.** Wrong capture device. Run `--list-devices` and put part of the name in
`[audio].device`.

**The words are wrong.** Sentences are being cut short. Open `logs\live-translator.log`:

```
heard 3.8s en(0.74) in 0.55s: 'How much do you like it?'
filtered (fragment (0.41s of audio)): 'you'
```

If those durations are around a second, it is cutting people off mid sentence and guessing the
rest. Raise `audio.silence_ms`. Shortening `languages.allowed` helps as well.

**It went slow.** The log says which problem it is. `suggested in 1.4s` is normal. Tens of
seconds means a game took the VRAM, and it prints that directly:
`qwen3:4b is only 40% on the GPU`. Close something, or switch `asr.compute_type` to `int8`. One
slow line after a quiet spell is just the model loading back in.

**It translates my music.** See [Hearing only the voice chat](#hearing-only-the-voice-chat).

**It is invisible over the game.** Fullscreen draws over everything. Set the game to Borderless
or Windowed.

**It says Ollama is not connected.** Start Ollama from the Start menu, or run `ollama serve`.

## Commands

| | |
| --- | --- |
| `.\run.ps1` | Start it |
| `.\run.ps1 --check` | Test everything and show the capture device |
| `.\run.ps1 --demo` | The window with fake content, no audio or models |
| `.\run.ps1 --list-devices` | List audio devices |
| `.\run.ps1 --language es` | Skip detection, assume one language |
| `.\run.ps1 --no-suggest` | Translate only, no replies |
| `.\run.ps1 --debug` | Verbose logging |

## How it works

```
speakers
  WASAPI loopback capture, resampled to 16 kHz mono
  voice detection with an adaptive noise gate
  faster-whisper large-v3-turbo on CUDA
  qwen3:4b through Ollama, streamed
  overlay
```

Worth knowing if you want to change it:

An utterance ends on a real pause. Hand the speech model a one second fragment and it guesses
both the words and the language, which is where wrong translations come from. Once someone has
been talking for a while it starts cutting at smaller pauses instead, so long speech arrives in
pieces rather than all at the end.

The model's answer is read as it streams in. The translation comes first in the schema, so it
shows up while the replies are still being written.

Speech models invent text when they hear music or silence, usually subtitle filler along the
lines of "Thanks for watching". Results are scored on confidence, length, repetition and
language, and anything that looks made up is dropped with a reason in the log.

## Files

```
src/livetranslator/
  audio.py     loopback capture and resampling
  vad.py       speech detection and sentence boundaries
  asr.py       transcription and filtering
  suggest.py   translation and replies via Ollama
  overlay.py   the window
  theme.py     colours, fonts, icons
  motion.py    animations
  glass.py     Windows window effects
  pipeline.py  threading
```

## Models

`suggest.model` takes anything you have in Ollama. `gemma3:4b` is worth a try if you prefer how
it translates.

```powershell
.\run.ps1 --suggest-model gemma3:4b
```

A 4B model gets grammar wrong now and then, so read the English under a reply before you send
it. Pronunciation lines are close enough to be understood, not exact. `qwen3:8b` is better at
both and wants more memory.

## Fonts

Inter is in `assets/fonts` and loads at startup, so there is nothing to install and the window
looks the same everywhere. It is under the SIL Open Font License, which allows it to be included
and passed on. Set `ui.font_family` in `config.toml` for a different font.

## Privacy

Audio is handled in memory and never written to disk. The only file it writes is
`logs\live-translator.log`, which has transcribed text in it for troubleshooting, is ignored by
git, and can be deleted whenever you like.

This records calls you are part of. Recording other people may need their permission where you
live.

## Contact

Discord: njf7, if you want to talk about the repo or send feedback. Thanks to RRosemont for
testing.

## Licence

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
