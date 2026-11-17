import numpy as np
import sounddevice as sd
import whisper

class VoiceListener:
    def __init__(self, model_name="small", sample_rate=16000):
        self.model_name = model_name
        self.sample_rate = sample_rate
        self.model = None
        self.recording = False
        self.chunks = []
        self.stream = None
        self.level = 0.0  # anlık mikrofon seviyesi (0..1), HUD dalga formu için

    def load(self):
        if self.model is None:
            self.model = whisper.load_model(self.model_name)

    def _callback(self, indata, frames, time_info, status):
        if status:
            print("Microphone:", status)
        if self.recording:
            self.chunks.append(indata.copy())
            rms = float(np.sqrt(np.mean(np.square(indata))))
            self.level = min(1.0, rms * 14.0)

    def start(self):
        self.load()
        self.chunks = []
        self.recording = True
        self.stream = sd.InputStream(
            samplerate=self.sample_rate, channels=1, dtype="float32",
            blocksize=1600, callback=self._callback
        )
        self.stream.start()

    def stop(self):
        if not self.recording:
            return None
        self.recording = False
        self.level = 0.0
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if not self.chunks:
            return None
        audio = np.concatenate(self.chunks, axis=0).flatten()
        result = self.model.transcribe(
            audio, language="tr", task="transcribe",
            fp16=False, temperature=0, condition_on_previous_text=False
        )
        return result.get("text", "").strip() or None

    def close(self):
        if self.recording:
            try:
                self.stop()
            except Exception:
                pass
