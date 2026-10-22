# Narrow formats: the evaluation

> **Sound in syncs** ("PAL mono + sound in syncs", the FM and AM sections
> below) was rejected on these figures and Brian dropped it on 4 October
> 2026: its code (`ohsatv/sis.py`) is removed and `tools/narrow_eval.py` no
> longer runs it. Its tables are kept as the record of why; a fresh run of
> the tool prints the other formats only.

> **Mini-MAC's sound in real time** (5 October 2026). On air its tone
> measured ~17 dB SINAD; the burst's recording
> (`~/openhamshack-atv-captures/2026-10-04/narrow/narrow-mac-b2-minus14`)
> decoded offline gives 37–39 dB in every second after acquisition, as
> the tables below predict. The loss was the receiver falling behind at
> 16 MS/s and dropping blocks of signal, which carry Mini-MAC's sound;
> the engine now skips pictures instead (DESIGN.md "Mini-MAC's sound on
> air"). The same recording played through the engine in real time on
> shack, 12 s, before / after: sound delivered 5.4–5.8 / 9.3–10 s of ~10,
> median SINAD 26–28 / 38.1 dB, pictures 136–144 / 195–242. Nothing here
> measures that (the tool decodes offline), and the transmitted waveform
> did not change; the Mini-MAC tables were re-run after the decoder's
> picture was made cheaper and differ only in the last digit.
>
> The same re-run printed 40 ms (PAL, 6.25 MS/s) and 38 ms (narrow colour
> PAL, 5 MS/s) of picture lost to the 2 ms transmit gap, where the tables
> below say 5 and 6. The code before these changes (main at 2ced13e) prints
> the same 40 and 38, so it came in with an earlier change; not yet looked
> into.

Printed by `python3 tools/narrow_eval.py` (the module docstring says how the channel is built). Simulator only: nothing here has been on air. CNR is over the whole sample band; C/N0 compares the two rates. Columns: fraction of lines with sync found and field/frame sync (V); the decoder's own SNR estimate; luma PSNR of the decoded test card against the source; on 75 % bars the mean hue error (degrees) and saturation ratio of the six coloured bars; SINAD of a 1 kHz tone.

Pass marks (the 'threshold' lines, the lowest CNR of an unbroken run of passes from the top): lock = at least 90% of lines and frame sync; picture = locked and luma PSNR >= 15 dB; colour = locked, hue within 15 degrees, saturation 0.6-1.4; sound = locked and SINAD >= 20 dB. Narrow profile: 5 MS/s 1.2 MHz p-p / 1.6 MHz video, 6.25 MS/s 1.5 / 2.0, CCIR 405 (Mini-MAC flat); AM-VSB at the plan's 2.4 / 3.0 MHz.

## PAL - FM

5 MS/s: 99 % occupied 0.65 MHz, outside +-0.475 fs -64.6 dBc; picture lost to a 2 ms TX gap 5 ms, to a 777-sample RX slip 5 ms (at 20 dB CNR)
| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 91 | 1.00 V | 27.0 | 22.3 | - | - | - |
| 22 | 89 | 1.00 V | 25.0 | 21.7 | - | - | - |
| 20 | 87 | 1.00 V | 23.0 | 20.8 | - | - | - |
| 18 | 85 | 1.00 V | 21.0 | 19.7 | - | - | - |
| 16 | 83 | 1.00 V | 19.0 | 18.4 | - | - | - |
| 14 | 81 | 0.99 V | 16.9 | 16.9 | - | - | - |
| 12 | 79 | 0.96 V | 14.9 | 15.3 | - | - | - |
| 10 | 77 | 0.89 V | 12.8 | 13.7 | - | - | - |
| 8 | 75 | 0.79 V | 10.8 | 12.2 | - | - | - |
| 6 | 73 | 0.67 V | 8.6 | 10.7 | - | - | - |
| 4 | 71 | 0.55 V | 5.5 | 9.4 | - | - | - |

threshold: lock 12 dB, picture 12 dB, colour -, sound -

6.25 MS/s: 99 % occupied 0.87 MHz, outside +-0.475 fs -60.4 dBc; picture lost to a 2 ms TX gap 5 ms, to a 777-sample RX slip 5 ms (at 20 dB CNR)
| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 92 | 1.00 V | 28.3 | 23.6 | - | - | - |
| 22 | 90 | 1.00 V | 26.3 | 22.9 | - | - | - |
| 20 | 88 | 1.00 V | 24.3 | 21.9 | - | - | - |
| 18 | 86 | 1.00 V | 22.3 | 20.8 | - | - | - |
| 16 | 84 | 1.00 V | 20.3 | 19.5 | - | - | - |
| 14 | 82 | 1.00 V | 18.3 | 18.0 | - | - | - |
| 12 | 80 | 0.99 V | 16.2 | 16.3 | - | - | - |
| 10 | 78 | 0.97 V | 14.2 | 14.6 | - | - | - |
| 8 | 76 | 0.90 V | 12.0 | 13.0 | - | - | - |
| 6 | 74 | 0.79 V | 9.9 | 11.4 | - | - | - |
| 4 | 72 | 0.67 V | 6.4 | 9.9 | - | - | - |

threshold: lock 10 dB, picture 12 dB, colour -, sound -


## PAL mono + sound in syncs - FM

5 MS/s: 99 % occupied 0.66 MHz, outside +-0.475 fs -64.6 dBc; picture lost to a 2 ms TX gap 6 ms, to a 777-sample RX slip 7 ms (at 20 dB CNR)
| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 91 | 1.00 V | 25.5 | 19.5 | - | - | 38.7 |
| 22 | 89 | 1.00 V | 23.5 | 19.0 | - | - | 31.7 |
| 20 | 87 | 1.00 V | 21.5 | 18.3 | - | - | 23.6 |
| 18 | 85 | 0.99 V | 19.5 | 17.5 | - | - | 15.1 |
| 16 | 83 | 0.99 V | 17.5 | 16.5 | - | - | 7.9 |
| 14 | 81 | 0.97 V | 15.5 | 15.3 | - | - | -4.9 |
| 12 | 79 | 0.94 V | 13.7 | 14.0 | - | - | 0.0 |
| 10 | 77 | 0.89 V | 11.8 | 12.7 | - | - | 0.0 |
| 8 | 75 | 0.82 V | 9.9 | 11.5 | - | - | 0.0 |
| 6 | 73 | 0.72 V | 8.3 | 10.5 | - | - | -22.9 |
| 4 | 71 | - - | - | - | - | - | - |

threshold: lock 12 dB, picture 14 dB, colour -, sound 20 dB

6.25 MS/s: 99 % occupied 0.90 MHz, outside +-0.475 fs -60.4 dBc; picture lost to a 2 ms TX gap 5 ms, to a 777-sample RX slip 6 ms (at 20 dB CNR)
| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 92 | 1.00 V | 28.3 | 17.5 | - | - | 29.1 |
| 22 | 90 | 1.00 V | 26.3 | 17.4 | - | - | 22.0 |
| 20 | 88 | 1.00 V | 24.3 | 17.1 | - | - | 16.9 |
| 18 | 86 | 1.00 V | 22.2 | 16.7 | - | - | 11.4 |
| 16 | 84 | 0.99 V | 20.2 | 16.2 | - | - | 6.8 |
| 14 | 82 | 0.99 V | 18.2 | 15.4 | - | - | 1.0 |
| 12 | 80 | 0.96 V | 16.2 | 14.5 | - | - | 0.0 |
| 10 | 78 | 0.93 V | 14.1 | 13.4 | - | - | 0.0 |
| 8 | 76 | 0.88 V | 12.1 | 12.2 | - | - | 0.0 |
| 6 | 74 | 0.80 V | 9.7 | 11.0 | - | - | 0.0 |
| 4 | 72 | 0.70 V | 7.6 | 9.8 | - | - | 0.0 |

threshold: lock 10 dB, picture 14 dB, colour -, sound 22 dB


## Narrow colour PAL (1.6 MHz colour, 2.2 MHz sound) - FM

5 MS/s: 99 % occupied 0.96 MHz, outside +-0.475 fs -40.1 dBc; picture lost to a 2 ms TX gap 6 ms, to a 777-sample RX slip 6 ms (at 20 dB CNR)
| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 91 | 1.00 V | 25.6 | 21.2 | 2.8 | 0.99 | 38.6 |
| 22 | 89 | 1.00 V | 23.6 | 20.9 | 2.8 | 0.99 | 36.6 |
| 20 | 87 | 1.00 V | 21.6 | 20.4 | 2.7 | 0.99 | 34.6 |
| 18 | 85 | 1.00 V | 19.6 | 19.9 | 2.8 | 0.99 | 32.6 |
| 16 | 83 | 1.00 V | 17.6 | 19.1 | 2.8 | 0.99 | 30.5 |
| 14 | 81 | 0.98 V | 15.5 | 18.0 | 2.6 | 0.99 | 27.7 |
| 12 | 79 | 0.96 V | 13.5 | 16.8 | 2.5 | 0.98 | 21.0 |
| 10 | 77 | 0.88 V | 11.4 | 15.5 | 2.6 | 0.97 | 12.8 |
| 8 | 75 | 0.77 V | 9.4 | 13.8 | - | - | 7.5 |
| 6 | 73 | 0.65 V | 7.3 | 12.2 | - | - | 1.7 |
| 4 | 71 | 0.56 V | 5.1 | 10.6 | - | - | -3.4 |

threshold: lock 12 dB, picture 12 dB, colour 12 dB, sound 12 dB

6.25 MS/s: 99 % occupied 1.34 MHz, outside +-0.475 fs -46.1 dBc; picture lost to a 2 ms TX gap 6 ms, to a 777-sample RX slip 6 ms (at 20 dB CNR)
| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 92 | 1.00 V | 28.1 | 21.3 | 2.9 | 0.97 | 39.1 |
| 22 | 90 | 1.00 V | 26.1 | 21.1 | 3.0 | 0.97 | 37.1 |
| 20 | 88 | 1.00 V | 24.1 | 20.9 | 3.1 | 0.97 | 35.1 |
| 18 | 86 | 1.00 V | 22.1 | 20.5 | 3.0 | 0.97 | 33.1 |
| 16 | 84 | 1.00 V | 20.1 | 20.0 | 3.0 | 0.97 | 31.0 |
| 14 | 82 | 1.00 V | 18.0 | 19.3 | 3.1 | 0.97 | 28.2 |
| 12 | 80 | 1.00 V | 16.0 | 18.4 | 3.0 | 0.96 | 23.4 |
| 10 | 78 | 0.97 V | 13.9 | 17.2 | 3.0 | 0.96 | 15.7 |
| 8 | 76 | 0.91 V | 11.8 | 15.9 | 3.0 | 0.94 | 9.0 |
| 6 | 74 | 0.80 V | 9.6 | 14.1 | - | - | 3.5 |
| 4 | 72 | 0.65 V | 6.3 | 12.3 | - | - | -2.1 |

threshold: lock 8 dB, picture 8 dB, colour 8 dB, sound 12 dB


## Mini-MAC (colour + digital sound) - FM

5 MS/s: 99 % occupied 1.92 MHz, outside +-0.475 fs -50.4 dBc; picture lost to a 2 ms TX gap 2 ms, to a 777-sample RX slip 0 ms (at 20 dB CNR)
| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 91 | 1.00 V | 32.6 | 20.9 | 0.4 | 0.99 | 39.2 |
| 22 | 89 | 1.00 V | 30.6 | 20.8 | 0.3 | 0.99 | 39.2 |
| 20 | 87 | 1.00 V | 28.6 | 20.6 | 0.3 | 0.99 | 39.2 |
| 18 | 85 | 1.00 V | 26.5 | 20.2 | 0.3 | 0.99 | 39.2 |
| 16 | 83 | 1.00 V | 24.5 | 19.7 | 0.4 | 0.99 | 39.2 |
| 14 | 81 | 1.00 V | 22.5 | 19.0 | 0.5 | 0.99 | 39.2 |
| 12 | 79 | 1.00 V | 20.4 | 18.1 | 0.6 | 0.99 | 39.2 |
| 10 | 77 | 1.00 V | 18.3 | 17.0 | 0.7 | 0.99 | 39.2 |
| 8 | 75 | 1.00 V | 16.2 | 15.7 | 0.8 | 0.99 | 38.8 |
| 6 | 73 | 1.00 V | 13.7 | 14.2 | 1.1 | 0.99 | 28.4 |
| 4 | 71 | 0.97 V | 11.1 | 12.5 | 1.5 | 0.98 | 18.2 |

threshold: lock 4 dB, picture 8 dB, colour 4 dB, sound 6 dB

6.25 MS/s: 99 % occupied 2.09 MHz, outside +-0.475 fs -52.7 dBc; picture lost to a 2 ms TX gap 2 ms, to a 777-sample RX slip 0 ms (at 20 dB CNR)
| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 92 | 1.00 V | 32.4 | 21.9 | 0.3 | 0.99 | 39.2 |
| 22 | 90 | 1.00 V | 30.4 | 21.7 | 0.3 | 0.99 | 39.2 |
| 20 | 88 | 1.00 V | 28.4 | 21.4 | 0.3 | 0.99 | 39.2 |
| 18 | 86 | 1.00 V | 26.4 | 21.0 | 0.3 | 0.99 | 39.2 |
| 16 | 84 | 1.00 V | 24.4 | 20.4 | 0.3 | 0.99 | 39.2 |
| 14 | 82 | 1.00 V | 22.4 | 19.6 | 0.4 | 0.99 | 39.2 |
| 12 | 80 | 1.00 V | 20.4 | 18.6 | 0.4 | 0.99 | 39.2 |
| 10 | 78 | 1.00 V | 18.3 | 17.4 | 0.5 | 0.99 | 39.2 |
| 8 | 76 | 1.00 V | 16.2 | 16.0 | 0.5 | 0.98 | 39.2 |
| 6 | 74 | 1.00 V | 14.0 | 14.4 | 0.7 | 0.98 | 33.2 |
| 4 | 72 | 0.97 V | 11.4 | 12.6 | 0.6 | 0.98 | 21.6 |

threshold: lock 4 dB, picture 8 dB, colour 4 dB, sound 4 dB


## PAL - AM

5 MS/s: occupied (plan) 3.65 MHz; picture lost to a 2 ms TX gap 7 ms, to a 777-sample RX slip 6 ms (at 20 dB CNR)
| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 91 | 1.00 V | 23.8 | 23.9 | - | - | - |
| 22 | 89 | 1.00 V | 21.8 | 22.1 | - | - | - |
| 20 | 87 | 1.00 V | 19.8 | 20.3 | - | - | - |
| 18 | 85 | 1.00 V | 17.8 | 18.4 | - | - | - |
| 16 | 83 | 0.98 V | 15.9 | 16.6 | - | - | - |
| 14 | 81 | 0.93 V | 13.9 | 14.8 | - | - | - |
| 12 | 79 | 0.86 V | 12.0 | 13.2 | - | - | - |
| 10 | 77 | 0.75 V | 10.3 | 11.8 | - | - | - |
| 8 | 75 | 0.64 V | 8.5 | 10.5 | - | - | - |
| 6 | 73 | 0.55 V | 6.8 | 9.4 | - | - | - |
| 4 | 71 | - - | - | - | - | - | - |

threshold: lock 14 dB, picture 16 dB, colour -, sound -

6.25 MS/s: occupied (plan) 4.25 MHz; picture lost to a 2 ms TX gap 6 ms, to a 777-sample RX slip 6 ms (at 20 dB CNR)
| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 92 | 1.00 V | 23.7 | 23.9 | - | - | - |
| 22 | 90 | 1.00 V | 21.7 | 22.1 | - | - | - |
| 20 | 88 | 1.00 V | 19.7 | 20.2 | - | - | - |
| 18 | 86 | 1.00 V | 17.6 | 18.3 | - | - | - |
| 16 | 84 | 0.99 V | 15.7 | 16.5 | - | - | - |
| 14 | 82 | 0.95 V | 13.7 | 14.7 | - | - | - |
| 12 | 80 | 0.88 V | 11.8 | 13.0 | - | - | - |
| 10 | 78 | 0.77 V | 10.0 | 11.6 | - | - | - |
| 8 | 76 | 0.66 V | 8.2 | 10.2 | - | - | - |
| 6 | 74 | 0.57 V | 6.5 | 9.2 | - | - | - |
| 4 | 72 | - - | - | - | - | - | - |

threshold: lock 14 dB, picture 16 dB, colour -, sound -


## PAL mono + sound in syncs - AM

5 MS/s: occupied (plan) 3.65 MHz; picture lost to a 2 ms TX gap 6 ms, to a 777-sample RX slip 6 ms (at 20 dB CNR)
| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 91 | 1.00 V | 22.4 | 19.8 | - | - | 35.4 |
| 22 | 89 | 1.00 V | 20.4 | 18.9 | - | - | 24.3 |
| 20 | 87 | 1.00 V | 18.4 | 17.7 | - | - | 13.7 |
| 18 | 85 | 0.99 V | 16.4 | 16.5 | - | - | 7.9 |
| 16 | 83 | 0.97 V | 14.6 | 15.1 | - | - | -1.2 |
| 14 | 81 | 0.94 V | 12.7 | 13.7 | - | - | 0.0 |
| 12 | 79 | 0.88 V | 10.8 | 12.1 | - | - | 0.0 |
| 10 | 77 | 0.81 V | 9.2 | 11.0 | - | - | 0.0 |
| 8 | 75 | - - | - | - | - | - | - |
| 6 | 73 | - - | - | - | - | - | - |
| 4 | 71 | - - | - | - | - | - | - |

threshold: lock 14 dB, picture 16 dB, colour -, sound 22 dB

6.25 MS/s: occupied (plan) 4.25 MHz; picture lost to a 2 ms TX gap 0 ms, to a 777-sample RX slip 4 ms (at 20 dB CNR)
| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 92 | 1.00 V | 23.5 | 15.7 | - | - | 18.2 |
| 22 | 90 | 1.00 V | 21.6 | 15.4 | - | - | 11.8 |
| 20 | 88 | 0.99 V | 19.6 | 15.0 | - | - | 7.1 |
| 18 | 86 | 0.98 V | 17.7 | 14.3 | - | - | 1.3 |
| 16 | 84 | 0.96 V | 15.7 | 13.6 | - | - | 0.0 |
| 14 | 82 | 0.93 V | 13.7 | 12.6 | - | - | 0.0 |
| 12 | 80 | 0.89 V | 11.8 | 11.7 | - | - | 0.0 |
| 10 | 78 | - - | - | - | - | - | - |
| 8 | 76 | - - | - | - | - | - | - |
| 6 | 74 | - - | - | - | - | - | - |
| 4 | 72 | - - | - | - | - | - | - |

threshold: lock 14 dB, picture 22 dB, colour -, sound -

