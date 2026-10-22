"""The microphone is opened only while it is the sound source.

On macOS opening an input stream is what asks for microphone permission,
and when ATV opened one at every start the prompt held the window back at
first launch (the notarised .dmg launched and showed nothing). These run the
real engine and the real PortAudio backend over a stand-in for pyaudio that
records which streams are open, so "no input device was opened" is
observed, not inferred. Nothing here touches a sound card or a radio."""
import sys
import types
import unittest
from pathlib import Path

from helpers import tmp_dir
from ohsatv.radio import Radio


class FakeStream:
    def __init__(self, pa, kw):
        self.pa, self.kw = pa, kw
        self.input = bool(kw.get("input"))
        self.started = self.closed = False

    def start_stream(self):
        self.started = True

    def stop_stream(self):
        self.started = False

    def close(self):
        self.closed = True


class FakePyAudio:
    """One output device and one input device; every stream it opens is
    kept, closed or not."""
    paFloat32 = 1
    paContinue = 0
    opened: list[FakeStream] = []
    refuse_input = False

    DEVICES = [{"name": "Speakers", "maxInputChannels": 0, "maxOutputChannels": 2, "defaultSampleRate": 48000.0,
                "hostApi": 0},
               {"name": "Microphone", "maxInputChannels": 1, "maxOutputChannels": 0, "defaultSampleRate": 48000.0,
                "hostApi": 0}]

    def PyAudio(self):
        return self

    def get_device_count(self):
        return len(self.DEVICES)

    def get_device_info_by_index(self, i):
        return dict(self.DEVICES[i], index=i)

    def get_default_output_device_info(self):
        return self.get_device_info_by_index(0)

    def get_default_host_api_info(self):
        return {"index": 0}

    def is_format_supported(self, *a, **kw):
        return True

    def open(self, **kw):
        if kw.get("input") and self.refuse_input:
            raise OSError("Invalid input device")
        s = FakeStream(self, kw)
        self.opened.append(s)
        return s

    def terminate(self):
        pass

    # -- what the tests look at
    def inputs_open(self):
        return [s for s in self.opened if s.input and not s.closed]

    def outputs_open(self):
        return [s for s in self.opened if not s.input and not s.closed]


class MicOnlyWhenChosen(unittest.TestCase):
    def setUp(self):
        self.pa = FakePyAudio()
        self.pa.opened = []
        saved = sys.modules.get("pyaudio")
        sys.modules["pyaudio"] = types.SimpleNamespace(PyAudio=self.pa.PyAudio, paFloat32=1, paContinue=0)
        self.addCleanup(lambda: sys.modules.__setitem__("pyaudio", saved) if saved is not None
                        else sys.modules.pop("pyaudio", None))

    def make(self, **over):
        tmp = tmp_dir(self)
        cfg = dict(device="sim", audioBackend="portaudio", system="am", band="23cm", rxFreq=1249.0e6,
                   txFreq=1249.0e6, amRate=10_000_000)
        cfg.update(over)
        r = Radio(cfg, conf_path=tmp / "c.json", data_dir=tmp / "data")
        self.addCleanup(r.stop)
        self.toasts = []
        r.toast = lambda kind, msg: self.toasts.append((kind, msg))
        return r

    def test_a_tone_start_opens_no_microphone_choosing_it_does_and_leaving_closes(self):
        r = self.make(audioSource="tone")
        r.start()
        self.assertEqual(r.audio.name, "portaudio")
        self.assertEqual(len(self.pa.outputs_open()), 1)        # the speakers, as ever
        self.assertEqual(self.pa.inputs_open(), [])

        r.set(audioSource="mic")
        (mic,) = self.pa.inputs_open()
        self.assertTrue(mic.started)
        self.assertEqual(mic.kw["input_device_index"], None)    # audioIn empty: the system default

        r.set(audioSource="file")
        self.assertEqual(self.pa.inputs_open(), [])
        self.assertTrue(mic.closed)
        self.assertEqual(len(self.pa.outputs_open()), 1)        # sound out carries on

        r.set(audioSource="mic")
        self.assertEqual(len(self.pa.inputs_open()), 1)
        r.set(audioSource="none")
        self.assertEqual(self.pa.inputs_open(), [])

    def test_a_saved_choice_of_microphone_opens_it_at_start(self):
        r = self.make(audioSource="mic", audioIn="Microphone")
        r.start()
        (mic,) = self.pa.inputs_open()
        self.assertEqual(mic.kw["input_device_index"], 1)

    def test_stopping_closes_the_microphone(self):
        r = self.make(audioSource="mic")
        r.start()
        self.assertEqual(len(self.pa.inputs_open()), 1)
        r.stop()
        self.assertEqual(self.pa.inputs_open(), [])
        self.assertEqual(self.pa.outputs_open(), [])

    def test_a_microphone_that_will_not_open_is_said_when_chosen(self):
        self.pa.refuse_input = True
        r = self.make(audioSource="tone")
        r.start()
        self.assertEqual(self.toasts, [])                       # nothing to say until it is wanted
        r.set(audioSource="mic")
        self.assertEqual(self.pa.inputs_open(), [])
        self.assertEqual(len(self.toasts), 1)
        self.assertIn("No microphone", self.toasts[0][1])


if __name__ == "__main__":
    unittest.main()
