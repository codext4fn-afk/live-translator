"""Overlay preview with canned content - no audio, no models."""

from __future__ import annotations

from . import config as config_mod
from .config import Config
from .overlay import Overlay, timestamp
from .suggest import Reply

# Deliberately spans Latin, CJK and RTL scripts
SAMPLES = [
    (
        "es", 0.98,
        "Oye bro, vamos a jugar otra ronda? Pero esta vez no te mueras tan rápido jajaja",
        "Hey bro, want to play another round? But don't die so fast this time haha",
        [
            Reply("Va, pero tú cúrame esta vez", "Sure, but you heal me this time"),
            Reply("Dame cinco minutos y entro", "Give me five minutes and I'll join"),
            Reply("Jajaja cállate, si moriste primero", "Haha shut up, you died first"),
        ],
    ),
    (
        "ja", 0.96,
        "ごめん、ちょっと待って。今ちょうど親が呼んでるから五分だけ離れるね",
        "Sorry, hold on. My parents are calling me, so I'll be away for five minutes.",
        [
            Reply("りょうかい、待ってるよ", "Got it, I'll wait"),
            Reply("おけ、そのあいだ練習しとく", "OK, I'll practise in the meantime"),
        ],
    ),
    (
        "ar", 0.93,
        "يا صاحبي الصوت عندك ضعيف جدا، ما أقدر أسمعك",
        "Dude, your audio is really quiet, I can't hear you.",
        [
            Reply("ثانية، أرفع الصوت", "One sec, I'll turn it up"),
            Reply("جرب الآن، أحسن؟", "Try now, is it better?"),
        ],
    ),
]


def run(cfg: Config) -> None:
    overlay = Overlay(
        width=cfg.ui.width,
        height=cfg.ui.height,
        opacity=cfg.ui.opacity,
        font_size=cfg.ui.font_size,
        max_history=cfg.ui.max_history,
        always_on_top=cfg.ui.always_on_top,
        my_language=cfg.languages.my_language,
        font_family=cfg.ui.font_family,
        acrylic=cfg.ui.acrylic,
        gradient=cfg.ui.gradient,
    )
    overlay.set_gradient_hook(
        lambda name: config_mod.save_ui_value(cfg.source_path, "gradient", name)
    )
    overlay.set_quit_hook(lambda: None)
    overlay.set_pause_hook(lambda _paused: None)

    def fake_compose(text: str, target_code: str) -> None:
        """Echo something plausible so the compose box can be tried offline."""
        from .suggest import Outgoing

        overlay.show_outgoing(
            Outgoing(
                natural=f"[demo] {text}",
                alternative="[demo] a second phrasing would appear here",
                note="demo mode - no model is running, so this is not a real translation",
            )
        )

    overlay.set_compose_hook(fake_compose)

    def populate() -> None:
        for code, confidence, original, translation, replies in SAMPLES:
            card = overlay.add_card(
                original=original, language=code, confidence=confidence,
                meta=f"{timestamp()}  ·  demo",
            )
            card.set_translation(translation)
            for reply in replies:
                card.add_reply(reply)
            card.finish()
        overlay.set_target_language("es")
        overlay.set_status(
            "demo mode - no audio is being captured. Drag the title bar to move, "
            "corner to resize, ＋／－ for opacity."
        )
        overlay.set_listening(True)

    overlay.post(populate)
    overlay.run()
    overlay.destroy()
