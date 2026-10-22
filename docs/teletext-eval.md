# Teletext through each mode: the evaluation

Printed by `python3 tools/teletext_eval.py` (its docstring says how the channel is built). Simulator only: nothing here has been on air. CNR is over the whole sample band, as in docs/narrow-eval.md. PER: the fraction of packets (32 a frame, random characters, on 75 % colour bars) not received exactly. SNR: the decoder's own video SNR estimate at that CNR. 'Plan': whether ohsatv.narrow's plan says the mode carries teletext (the engine inserts it only then). Here it is inserted in every mode, refused ones too, to show that it would not survive - so in those the SNR reads low: the mangled data fills the blanked lines the decoder measures noise on, and no slicer finds it there to leave it out. In the program a refused mode sends nothing on those lines.

## Thresholds

| mode | video bw | plan | PER <= 1 % from | PER <= 10 % from | PER at 40 dB |
| --- | --- | --- | --- | --- | --- |
| AM 5 MS/s (USB Pluto) | 2.40 MHz | no | - | - | 100.0% |
| AM 6.25 MS/s | 3.00 MHz | no | - | - | 100.0% |
| AM 8 MS/s | 3.84 MHz | yes | 26 dB | 24 dB | 0.0% |
| AM 10 MS/s (standard) | 4.80 MHz | yes | 24 dB | 22 dB | 0.0% |
| AM 12.5 MS/s | 5.50 MHz | yes | 24 dB | 22 dB | 0.0% |
| AM 16 MS/s (full System I) | 5.50 MHz | yes | 24 dB | 22 dB | 0.0% |
| FM 20 MS/s, 7 MHz p-p (IARU V.2) | 5.00 MHz | yes | 10 dB | 10 dB | 0.0% |
| FM 16 MS/s, 4 MHz p-p (Ethernet) | 5.00 MHz | yes | 16 dB | 14 dB | 0.0% |
| FM 12.5 MS/s, 1.2 MHz p-p (8-bit RX) | 4.90 MHz | yes | 26 dB | 24 dB | 0.0% |
| FM 10 MS/s, 3 MHz p-p (8-bit TX) | 3.00 MHz | no | - | - | 100.0% |
| FM 6.25 MS/s, 1.5 MHz p-p | 2.00 MHz | no | - | - | 100.0% |
| FM 5 MS/s, 1.2 MHz p-p (USB Pluto) | 1.60 MHz | no | - | - | 100.0% |

## Packet error rate against CNR

| CNR | AM 5 MS/s (USB Pluto) | AM 6.25 MS/s | AM 8 MS/s | AM 10 MS/s (standard) | AM 12.5 MS/s | AM 16 MS/s (full System I) | FM 20 MS/s, 7 MHz p-p (IARU V.2) | FM 16 MS/s, 4 MHz p-p (Ethernet) | FM 12.5 MS/s, 1.2 MHz p-p (8-bit RX) | FM 10 MS/s, 3 MHz p-p (8-bit TX) | FM 6.25 MS/s, 1.5 MHz p-p | FM 5 MS/s, 1.2 MHz p-p (USB Pluto) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 40 dB | 100.0% (11) | 100.0% (11) | 0.0% (39) | 0.0% (39) | 0.0% (39) | 0.0% (40) | 0.0% (46) | 0.0% (45) | 0.0% (36) | 100.0% (11) | 100.0% (13) | 100.0% (13) |
| 38 dB | 100.0% (11) | 100.0% (11) | 0.0% (37) | 0.0% (37) | 0.0% (37) | 0.0% (38) | 0.0% (46) | 0.0% (44) | 0.0% (34) | 100.0% (11) | 100.0% (13) | 100.0% (13) |
| 36 dB | 100.0% (11) | 100.0% (11) | 0.0% (35) | 0.0% (35) | 0.0% (35) | 0.0% (36) | 0.0% (45) | 0.0% (42) | 0.0% (32) | 100.0% (11) | 100.0% (13) | 100.0% (13) |
| 34 dB | 100.0% (11) | 100.0% (11) | 0.0% (33) | 0.0% (33) | 0.0% (33) | 0.0% (34) | 0.0% (44) | 0.0% (41) | 0.0% (30) | 100.0% (11) | 100.0% (13) | 100.0% (13) |
| 32 dB | 100.0% (11) | 100.0% (11) | 0.0% (31) | 0.0% (31) | 0.0% (31) | 0.0% (32) | 0.0% (43) | 0.0% (39) | 0.0% (28) | 100.0% (11) | 100.0% (13) | 100.0% (13) |
| 30 dB | 100.0% (11) | 100.0% (11) | 0.0% (29) | 0.0% (29) | 0.0% (29) | 0.0% (30) | 0.0% (41) | 0.0% (37) | 0.0% (26) | 100.0% (11) | 100.0% (13) | 100.0% (13) |
| 28 dB | 100.0% (11) | 100.0% (11) | 0.0% (27) | 0.0% (27) | 0.0% (27) | 0.0% (28) | 0.0% (40) | 0.0% (35) | 0.0% (24) | 100.0% (11) | 100.0% (13) | 100.0% (13) |
| 26 dB | 100.0% (11) | 100.0% (11) | 0.0% (25) | 0.0% (25) | 0.0% (25) | 0.0% (26) | 0.0% (38) | 0.0% (33) | 0.3% (22) | 100.0% (11) | 100.0% (13) | 100.0% (13) |
| 24 dB | 100.0% (11) | 100.0% (11) | 2.3% (23) | 0.0% (23) | 0.3% (23) | 0.0% (24) | 0.0% (36) | 0.0% (31) | 6.2% (20) | 100.0% (11) | 100.0% (12) | 100.0% (13) |
| 22 dB | 100.0% (11) | 100.0% (10) | 27.8% (21) | 8.8% (21) | 6.2% (21) | 1.4% (22) | 0.0% (34) | 0.0% (29) | 59.9% (18) | 100.0% (11) | 100.0% (12) | 100.0% (13) |
| 20 dB | 100.0% (10) | 100.0% (10) | 84.9% (19) | 64.5% (19) | 54.0% (19) | 18.2% (20) | 0.0% (32) | 0.0% (27) | 98.6% (16) | 100.0% (11) | 100.0% (12) | 100.0% (13) |
| 18 dB | 100.0% (10) | 100.0% (10) | 99.7% (17) | 100.0% (17) | 97.7% (17) | 82.1% (18) | 0.0% (30) | 0.0% (25) | 100.0% (9) | 100.0% (11) | 100.0% (12) | 100.0% (13) |
| 16 dB | 100.0% (10) | 100.0% (10) | 100.0% (9) | 100.0% (9) | 100.0% (15) | 100.0% (16) | 0.0% (28) | 0.0% (23) | 100.0% (8) | 100.0% (11) | 100.0% (12) | 100.0% (12) |
| 14 dB | 100.0% (9) | 100.0% (9) | 100.0% (9) | 100.0% (8) | 100.0% (9) | 100.0% (9) | 0.0% (26) | 2.8% (21) | 100.0% (7) | 100.0% (10) | 100.0% (12) | 100.0% (12) |
| 12 dB | 100.0% (8) | 100.0% (8) | 100.0% (8) | 100.0% (8) | 100.0% (8) | 100.0% (8) | 0.0% (24) | 28.7% (19) | 100.0% (6) | 100.0% (10) | 100.0% (11) | 100.0% (11) |
| 10 dB | 100.0% (8) | 100.0% (7) | 100.0% (7) | 100.0% (7) | 100.0% (7) | 100.0% (7) | 0.3% (22) | 91.2% (17) | 100.0% (5) | 100.0% (10) | 100.0% (10) | 100.0% (10) |
| 8 dB | 100.0% (7) | 100.0% (6) | 100.0% (6) | 100.0% (5) | 100.0% (6) | 100.0% (6) | 11.6% (20) | 100.0% (9) | no lock | 100.0% (10) | 100.0% (9) | 100.0% (9) |
| 6 dB | 100.0% (5) | 100.0% (5) | 100.0% (4) | no lock | no lock | 100.0% (5) | 80.7% (18) | 100.0% (8) | no lock | 100.0% (9) | 100.0% (8) | 100.0% (8) |
| 4 dB | no lock | no lock | no lock | no lock | 100.0% (4) | no lock | 100.0% (8) | 100.0% (6) | no lock | 100.0% (7) | 100.0% (5) | 100.0% (5) |

(In brackets: the decoder's SNR estimate, dB.)

## Video bandwidth

PER at high CNR (AM 12.5 MS/s at 40 dB, no sound carrier; FM 20 MS/s 7 MHz p-p at 30 dB, no subcarrier) with the modem's video filter set to each bandwidth.

| video bw | AM | FM |
| --- | --- | --- |
| 3 MHz | 100.0% | 100.0% |
| 3.5 MHz | 0.0% | 0.0% |
| 4 MHz | 0.0% | 0.0% |
| 4.25 MHz | 0.0% | 0.0% |
| 4.5 MHz | 0.0% | 0.0% |
| 4.75 MHz | 0.0% | 0.0% |
| 5 MHz | 0.0% | 0.0% |
| 5.5 MHz | 0.0% | 0.0% |

## The cable feed on UHF: 625-line PAL, System I

Printed by `python3 tools/teletext_eval.py --cable`. The chain and channel are the ones above; each point is 40 frames (1184 packets measured), CNR in 1 dB steps. System I as the feed sends it: AM-VSB, 5.5 MHz video where the rate allows, the 1.25 MHz vestige and the FM sound carrier at +5.9996 MHz, 13 dB down.

| mode | video bw | plan | PER <= 1 % from | PER <= 10 % from | PER = 0 from |
| --- | --- | --- | --- | --- | --- |
| UHF cable, AM 8 MS/s | 3.84 MHz | yes | 25 dB | 23 dB | 27 dB |
| UHF cable, AM 10 MS/s | 4.80 MHz | yes | 24 dB | 22 dB | 25 dB |
| UHF cable, AM 12.5 MS/s | 5.50 MHz | yes | 23 dB | 22 dB | 25 dB |
| UHF cable, AM 16 MS/s | 5.50 MHz | yes | 23 dB | 21 dB | 24 dB |

| CNR | UHF cable, AM 8 MS/s | UHF cable, AM 10 MS/s | UHF cable, AM 12.5 MS/s | UHF cable, AM 16 MS/s |
| --- | --- | --- | --- | --- |
| 40 dB | 0.00% (39) | 0.00% (39) | 0.00% (39) | 0.00% (40) |
| 38 dB | 0.00% (37) | 0.00% (37) | 0.00% (37) | 0.00% (38) |
| 36 dB | 0.00% (35) | 0.00% (35) | 0.00% (35) | 0.00% (36) |
| 34 dB | 0.00% (33) | 0.00% (33) | 0.00% (33) | 0.00% (34) |
| 32 dB | 0.00% (31) | 0.00% (31) | 0.00% (31) | 0.00% (32) |
| 30 dB | 0.00% (29) | 0.00% (29) | 0.00% (29) | 0.00% (30) |
| 29 dB | 0.00% (28) | 0.00% (28) | 0.00% (28) | 0.00% (29) |
| 28 dB | 0.00% (27) | 0.00% (27) | 0.00% (27) | 0.00% (28) |
| 27 dB | 0.00% (26) | 0.00% (26) | 0.00% (26) | 0.00% (27) |
| 26 dB | 0.17% (25) | 0.00% (25) | 0.00% (25) | 0.00% (26) |
| 25 dB | 0.76% (24) | 0.00% (24) | 0.00% (24) | 0.00% (25) |
| 24 dB | 2.96% (23) | 0.17% (23) | 0.17% (23) | 0.00% (24) |
| 23 dB | 8.95% (22) | 1.35% (22) | 0.59% (22) | 0.08% (23) |
| 22 dB | 26.52% (21) | 9.54% (21) | 5.07% (21) | 1.35% (22) |
| 21 dB | 54.31% (20) | 27.79% (20) | 20.02% (20) | 4.90% (21) |
| 20 dB | 83.28% (19) | 63.01% (19) | 51.86% (19) | 21.37% (20) |
| 19 dB | 98.06% (18) | 91.72% (18) | 83.78% (18) | 51.18% (19) |
| 18 dB | 99.49% (17) | 99.49% (17) | 98.40% (17) | 82.85% (18) |
| 17 dB | 100.00% (16) | 100.00% (16) | 99.92% (16) | 98.40% (17) |
| 16 dB | 100.00% (15) | 100.00% (9) | 100.00% (9) | 99.92% (16) |
| 15 dB | 100.00% (14) | 100.00% (9) | 100.00% (9) | 100.00% (9) |
| 14 dB | 100.00% (9) | 100.00% (8) | 100.00% (9) | 100.00% (9) |

(In brackets: the decoder's SNR estimate, dB. Above 30 dB every other step is left out: all 0.)
