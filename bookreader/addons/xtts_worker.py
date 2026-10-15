"""XTTS worker process. Runs inside the separate XTTS runtime (its own Python with torch + coqui-tts),
started by the main app. Protocol: one JSON object per line on stdin -> one JSON reply per line on stdout.

    {"id": 1, "cmd": "load"}                         -> {"id": 1, "ok": true, "speakers": [...], "device": "cuda"}
    {"id": 2, "cmd": "synth", "text": "...", "lang": "pl",
     "voice": {"type": "clone", "wavs": [...]} | {"type": "builtin", "name": "..."},
     "out": "C:/.../x.npy"}                           -> {"id": 2, "ok": true}
    {"id": 3, "cmd": "ping"}                          -> {"id": 3, "ok": true}
Errors: {"id": n, "ok": false, "error": "..."}.  Log lines: {"event": "log", "msg": "..."}.

This file must not import anything from the main app (it runs in a different Python).
"""
import json
import os
import sys

_out = sys.stdout
sys.stdout = sys.stderr          # libraries print freely; only our protocol goes to the real stdout

DUMMY = os.environ.get("XTTS_WORKER_DUMMY") == "1"
MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"


def send(obj):
    _out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _out.flush()


def log(msg):
    send({"event": "log", "msg": msg})


class Engine:
    def __init__(self):
        self.tts = None
        self.latents = {}

    def load(self):
        if DUMMY:
            return {"speakers": ["Dummy Speaker"], "device": "cpu"}
        if self.tts is None:
            import torch
            from TTS.api import TTS
            log("Ładowanie modelu XTTS (pierwszy raz: pobieranie ~1,9 GB)…")
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self.tts = TTS(MODEL_NAME, progress_bar=False).to(device)
            self.device = device
            log(f"Model gotowy na: {device}")
        try:
            speakers = sorted(self.tts.synthesizer.tts_model.speaker_manager.speakers.keys())
        except Exception:
            speakers = []
        return {"speakers": speakers, "device": self.device}

    def conditioning(self, voice):
        key = json.dumps(voice, sort_keys=True)
        if key not in self.latents:
            model = self.tts.synthesizer.tts_model
            if voice["type"] == "clone":
                self.latents[key] = model.get_conditioning_latents(audio_path=voice["wavs"])
            else:
                s = model.speaker_manager.speakers[voice["name"]]
                self.latents[key] = (s["gpt_cond_latent"], s["speaker_embedding"])
        return self.latents[key]

    def synth(self, text, lang, voice, out):
        import numpy as np
        if DUMMY:
            t = np.arange(int(24000 * 0.3)) / 24000
            np.save(out, (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32))
            return {}
        self.load()
        gpt, emb = self.conditioning(voice)
        res = self.tts.synthesizer.tts_model.inference(text, lang, gpt, emb, temperature=0.65,
                                                       repetition_penalty=5.0, enable_text_splitting=False)
        wav = res["wav"]
        wav = wav.cpu().numpy() if hasattr(wav, "cpu") else np.asarray(wav)
        np.save(out, wav.astype(np.float32))
        return {}


def main():
    eng = Engine()
    send({"event": "ready"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        rid = req.get("id")
        try:
            cmd = req["cmd"]
            if cmd == "ping":
                result = {}
            elif cmd == "load":
                result = eng.load()
            elif cmd == "synth":
                result = eng.synth(req["text"], req["lang"], req["voice"], req["out"])
            elif cmd == "quit":
                send({"id": rid, "ok": True})
                return
            else:
                raise ValueError(f"unknown command {cmd}")
            send({"id": rid, "ok": True, **result})
        except Exception as e:
            send({"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
