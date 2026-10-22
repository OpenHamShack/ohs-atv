# Analogue ATV transceiver on Pluto-family SDRs: research notes

Compiled 2026-10-04 for OpenHamShack ATV (GM4BRI, UK Full licence, Scotland). Every number has a source. Anything I worked out myself rather than found stated is marked **[derived]**. Anything I could not confirm is marked **[unverified]**. Anything I read in a project's source code is marked **[read in source]**.

The headline findings, because three of them change the scope:

1. **Analogue ATV is no longer in the 70cm bandplan.** The RSGB removed "Fast Scan (Analogue) TV" from 70cm on 4 Dec 2020. IARU Region 1 says that "as of 1-Jan-2021 Analogue ATV and SATV is no longer permitted in the 430-440 MHz band". Only narrow DATV (437 MHz, QPSK 2 Msymbol/s maximum) remains. The licence itself does not forbid it; the bandplan does.
2. **The UK 6cm allocation is not 5650–5850 MHz.** It is three blocks: **5650–5680, 5755–5765 and 5820–5850 MHz**. Most 5.8 GHz FPV channels fall outside them. The one channel that UK ATV actually uses is **5665 MHz**.
3. **A stock Pluto over USB 2.0 cannot carry standard FM-ATV.** Measured USB throughput is about 6.8 MS/s at 16-bit IQ. Standard FM-ATV needs about 17–20 MHz. Ethernet devices (Pluto+, LibreSDR), or 8-bit sample mode, are needed.

---

## 1. UK bandplans for ATV

The current edition is the **RSGB 2026 Band Plan**, "Updated: January-2026". The source is <https://rsgb.services/public/bandplans/docs/260125_rsgb_band_plan_2026.pdf>, linked from <https://rsgb.org/main/operating/band-plans/>. The IARU Region 1 reference is the **VHF Handbook v10.03** (Feb 2026): <https://www.iaru-r1.org/wp-content/uploads/2026/02/VHF_Handbook_V10_03_final.pdf>.

The RSGB's general definition, quoted from ITU-R SM.328 on every page: "**Necessary bandwidth**: For a given class of emission, the width of the frequency band which is just sufficient to ensure the transmission of information at the rate and with the quality required under specified conditions."

### 1.1 70cm (430–440 MHz)

| Segment (MHz) | RSGB 2026 usage | Max bandwidth stated |
|---|---|---|
| 435.0000–436.0000 | Satellites only | – |
| 436.0000–438.0000 | "Satellites and Experimental DATV/Data" | Note 14: "QPSK 2 Mega-symbols/second maximum recommended" |
| 437.0000 | "Experimental DATV/Data Centre of Activity (Note 14)" | as above |

- **RSGB change log, 4 Dec 2020:** "70cm: Removal of Fast Scan (Analogue) TV and Note-4 that related to it". Also "Split of 435-438 MHz to more clearly designate Satellite and Wideband experimentation".
- **IARU R1 (VHF Handbook 10.03, 430–440 footnotes):** "As of 1-Jan-2021 Analogue ATV and SATV is no longer permitted in the 430-440 MHz band. ATV operators are encouraged to use the microwave allocations where available, but may use narrower bandwidth DATV modes in the 436-438 MHz band … In case of interference between DATV/Data and the Amateur Satellite Service, the Satellite Service shall have priority. (VGC2020)". The same footnotes say "ATV Repeater outputs are not permitted in the 435MHz band (Varna 2014)".
- **IARU R1 §1.5.4 (436–438 MHz DATV):**
  - DATV repeater outputs are not permitted. DATV repeater inputs are permitted.
  - Simplex use is permitted.
  - "Transmission times by DATV/Data users should be as short as possible."
  - DATV must be compliant with the Region 1 DATV technical recommendations "and in particular the maximum bandwidth".
- **RSGB licence notes (70cm page):**
  - The amateur service is a secondary user. The amateur-satellite service is a secondary user in 435–438 MHz.
  - "Exclusion: 431-432 MHz not available within 100km radius of Charing Cross, London".
  - "Power Restriction: 430-432 MHz is 40W erp maximum".
- **BATC:** "it is possible to receive Analogue ATV transmissions on 437 MHz using an RTL-SDR Dongle" (<https://wiki.batc.org.uk/70cms_Analogue_TV>). BATC's getting-started leaflet: "Simplex activity is centred on 437 MHz" for DATV.
- **Historical 70cm AM-ATV frequencies:** I did not find them in any current source. The 2019 and 2020 RadCom bandplan PDFs are images, not text **[unverified]**. I have not listed old frequencies from memory.

**[derived]** OpenHamShack ATV should not offer analogue TX on 70cm by default. Analogue receive (monitoring) is harmless.

### 1.2 23cm (1240–1325 MHz)

RSGB 2026, the 1.3 GHz table ("effective from 1st January 2018"), ATV-relevant rows:

| Segment (MHz) | RSGB 2026 usage |
|---|---|
| 1242.000–1249.000 | "TV Repeaters (Note 9)". "New DATV repeater inputs (Note-10)". "Original ATV repeater inputs: 1248, 1249" |
| 1250.00 | "In order to prevent interference to Primary Users, caution must be exercised prior to using 1250-1290MHz in the UK" |
| 1260.000–1270.000 | "Amateur Satellite Service – Earth to Space uplinks only" |
| 1291.494–1296.000 | All modes |
| 1296.000–1300.000 | Narrowband, beacons, FM/DV, DD (not for ATV) |
| 1300.000–1325.000 | "TV repeaters (UK only) (Note 9)". "New DATV repeater outputs (Note-10)". "Original ATV repeater outputs: 1308.0, 1310.0, 1311.5, 1312.0, 1316.0, 1318.5 MHz" |

- **Note 8:** "The band 1240-1300MHz is subject to major replanning. Contact the Microwave Manager for further information."
- **Note 9:** "Repeaters and Migration to DATV, inc option for new DATV simplex are subject to further development and coordination."
- **Note 10:** "QPSK 4 Mega-symbols/second maximum recommended."
- **Licence notes (RSGB):** "Amateur Service: Secondary User … Amateur Satellite Service: 1,260-1,270 MHz: Secondary User Earth to Space only … Note specific conditions within 1240-1325MHz and within 50km of SS206127 (Bude), SE202577 (Harrogate), or in Northern Ireland."
- **The RSGB 2026 plan has no analogue FM-ATV simplex frequency, and no maximum bandwidth for ATV.** The ATV rows have an empty "Necessary Bandwidth" column.
- **Common UK FM-ATV usage (BATC wiki, <https://wiki.batc.org.uk/23cms_ATV>):**
  - "1248 – 1249 MHz = repeater inputs"
  - "1255 MHz = simplex"
  - "1275 MHz = simplex or alternative repeater input as determined by the CAA (primary user)"
  - "1300 – 1325 MHz = repeater outputs"
- **BATC getting-started leaflet:** "Simplex operation takes place between 1255 and 1275 MHz and broadcast quality pictures using FM are regularly exchanged over distances in excess of 100 KM" (<https://wiki.batc.org.uk/images/0/09/GettingstartedinATVbatc.pdf>).
- **IARU R1 1200–1300 MHz plan (VHF Handbook 10.03):**

| Segment (MHz) | Mode | Max BW |
|---|---|---|
| 1243.250–1260.000 | ATV / Digital ATV. Repeater outputs 1258.150–1259.350 (ch. R20–R68) | footnote (d): "Bandwidth limits according to national regulations" |
| 1260.000–1270.000 | Satellite service | (d) |
| 1272.000–1290.994 | ATV / Digital ATV | (d) |
| 1291.494–1296.000 | All modes | (d) |

  The IARU notes say the Cavtat 2008 Recommendation CT08_C5_27 "makes a series of recommendations for replanning other parts of the band for DATV and Digital Voice & Data".

- **UK TV repeaters** (ukrepeater.net, <https://www.ukrepeater.net/tvr.htm>, read 2026-10-04):
  - "All 30 TV Repeaters".
  - The 23cm outputs are at 1304, 1308, 1310, 1311.5, 1312, 1315, 1316, 1318, 1318.5 and 1322 MHz.
  - **None is in Scotland.** The nearest are GB3KM (Spennymoor) and GB3EY (Hull).

#### Galileo E6 and 23cm

- **ECC Decision (25)01** (in force 27 June 2025, <https://www.iaru-r1.org/wp-content/uploads/2025/07/ECC-Decision-2501.pdf>):
  - Galileo E6 occupies "1258.29-1299.21 MHz".
  - The annex sets measures for CEPT administrations. For ATV: "s) For broadband (bandwidth > 150 kHz), including amateur television (ATV), applications operating in the amateur service: i) 1258-1300 MHz: Maximum value of e.i.r.p. = −17 dBW/1MHz".
  - For narrowband amateur service in 1258–1296 MHz the limit is −17 dBW e.i.r.p.
  - Administrations may set a transitional period, "expectation this could take up to three years".
- **The UK position:** AMSAT-UK reports that the UK "informed the meeting that it will implement decides 1 (designation of the frequency bands for Galileo) but will not apply the technical conditions on amateur and amateur-satellite in decides 2" (<https://amsat-uk.org/tag/rnss/>). That is a secondary source; I did not find the Ofcom statement itself **[partly unverified]**.
- **WRC-23** adopted a footnote reminding administrations and amateurs to protect RNSS in 1240–1300 MHz. Guidance is in ITU-R M.2164-0 (IARU R1, <https://www.iaru-r1.org/2023/23cm-band-outcome-approved-at-the-7th-plenary-meeting-of-wrc-23/>). I did not read M.2164 itself **[unverified details]**.
- **[derived]** A ±9 MHz FM-ATV signal on 1255 MHz spans about 1246–1264 MHz. That overlaps both the bottom of E6 and the 1260–1270 satellite uplink segment. On 1275 MHz it sits in the middle of E6. Repeater inputs at 1248/1249 MHz are below E6 for the video main lobe. The RSGB's "caution … 1250-1290MHz" note applies to both simplex frequencies.

### 1.3 6cm (UK: 5650–5680, 5755–5765, 5820–5850 MHz)

RSGB 2026, 5.7 GHz table:

| Segment (MHz) | Necessary BW | RSGB usage |
|---|---|---|
| 5650.000–5668.000 | – | All modes. Amateur Satellite Service Earth-to-Space only |
| 5668.000–5670.000 | 2.7 kHz | 5668.200 Alternative narrowband centre |
| 5670.000–5680.000 | – | All modes |
| 5755.000–5760.000 | – | All modes |
| 5760.000–5762.000 | 2.7 kHz | Narrowband (CW, SSB, MGM, EME). 5760.100 preferred centre. 5760.750–5760.800 local beacons 10 W erp max |
| 5760.800–5760.995 | – | Propagation beacons only |
| 5762.000–5765.000 | – | All modes |
| 5820.000–5830.000 | – | All modes |
| 5830.000–5850.000 | – | All modes. Amateur Satellite Service Space-to-Earth only |

- **RSGB licence notes:**
  - "Amateur Service: 5,650-5,680 MHz – Secondary User. 5,755-5,765 and 5,820-5,850 MHz – Secondary User: Users must accept interference from ISM users."
  - "Note specific conditions apply within 50km of SS206127 (Bude) or SE202577 (Harrogate)."
  - (The plan has a typo, "5.755.000".)
- **RSGB 5.7/10 GHz page** (<https://rsgb.org/main/operating/band-plans/microwaves/5-7-and-10ghz/>):
  - "5.650–5.680 GHz, 5.755–5.765 GHz and 5.820–5.850 GHz".
  - The bands accommodate "ATV operation as well as repeaters for ATV and data".
  - Commercial 5 GHz kit "can be adapted … although care should be exercised to ensure it operates on the correct frequencies and bandwidth within the restricted UK allocations".
- **IARU R1 5650–5850 MHz plan** (VHF Handbook 10.03): continuous 5650–5850 MHz with no ATV segment, and **no maximum bandwidth** for the all-mode segments. Footnote (d): "Any wideband system shall protect narrowband applications, which have priority."
- **UK ATV practice: 5665 MHz** (BATC wiki, <https://wiki.batc.org.uk/5.6_GHz>):
  - "GB3KM has a licensed input on 5665 MHz and this has been chosen as the primary ATV operating frequency."
  - Audio WB-FM users are on 5825 and 5840 MHz, and "5825 MHz is too close to the band edge for ATV, and 5840 MHz is marginal. So 5665 is the general ATV preference."
  - BATC measured cheap FPV TX at "TX bandwidth is 25–30MHz". The receivers use a 480 MHz IF and quote about −80 dBm sensitivity.
  - "Horizontal polarisation has been chosen as the standard."
- **FPV channel tables** (Richwave RTC6705 datasheet, the common FPV VTX chip, <https://der-frickler.net/_media/modellbau/fpv/sky-rf/rtc6705-dst-001.pdf>):

| Band | CH1 | CH2 | CH3 | CH4 | CH5 | CH6 | CH7 | CH8 |
|---|---|---|---|---|---|---|---|---|
| A | 5865 | 5845 | 5825 | 5805 | 5785 | 5765 | 5745 | 5725 |
| B | 5733 | 5752 | 5771 | 5790 | 5809 | 5828 | 5847 | 5866 |
| E | 5705 | 5685 | **5665** | 5645 | 5885 | 5905 | 5925 | 5945 |

  - The datasheet table is indexed CS[2:0] = 000…111. I have relabelled these as CH1–8 **[derived]**.
  - Other common bands (Fatshark "F", Raceband "R") are not in this datasheet. I have not listed them **[unverified]**.
  - **[derived]** With the BATC-measured 25–30 MHz occupancy:
    - Only **E3 = 5665 MHz** keeps the signal inside a UK block (5665 ± 15 = 5650–5680).
    - A2 (5845) spills above 5850. A3 (5825) spills below 5820. A6 (5765) is on the band edge.
    - Every other listed channel is outside the UK allocation.
- **Ofcom on FPV under the amateur licence** (guidance §4.3.2): drone FPV "is not the purpose of Amateur Radio and this type of use is not compatible with the terms of the Licence … the 2.4 GHz and 5 GHz bands used by most drones are in Secondary allocations where no airborne use is permitted". Ground-to-ground ATV with FPV modules is a different thing, and is what BATC describes.

Sources: <https://rsgb.services/public/bandplans/docs/260125_rsgb_band_plan_2026.pdf>, <https://www.iaru-r1.org/wp-content/uploads/2026/02/VHF_Handbook_V10_03_final.pdf>, <https://wiki.batc.org.uk/23cms_ATV>, <https://wiki.batc.org.uk/5.6_GHz>, <https://www.ukrepeater.net/tvr.htm>, <https://www.iaru-r1.org/wp-content/uploads/2025/07/ECC-Decision-2501.pdf>

---

## 2. UK licence conditions (Ofcom)

Source: **Amateur Radio Wireless Telegraphy Licence Conditions Booklet OFW611, "Published 21 February 2024"** (<https://www.ofcom.org.uk/__data/assets/pdf_file/0027/62991/amateur-terms.pdf>). Also Ofcom's licensing guidance "Updated 14 October 2025" (<https://www.ofcom.org.uk/siteassets/resources/documents/manage-your-licence/amateur/amateur_radio_licence_guidance_for_licensees.pdf>), and the Notice of Coordination (21 Feb 2024).

### 2.1 Power and status (Schedule 1, PEP unless marked)

| Band (MHz) | Status (amateur service) | Full (Table C) | Intermediate (Table B) | Foundation (Table A) |
|---|---|---|---|---|
| 430–431 | Secondary | **40 W (16 dBW) ERP** | 40 W ERP | 25 W ERP |
| 431–432 | Secondary. "Not available for use within 100km radius of Charing Cross" | 40 W (16 dBW) ERP | 40 W ERP | 25 W ERP |
| 432–435 | Secondary | **400 W (26 dBW)** | 100 W (20 dBW) | 25 W (13.98 dBW) |
| 435–438 | Secondary (sat. also secondary) | 400 W | 100 W | 25 W |
| 438–440 | Secondary | 400 W | 100 W | 25 W |
| 1240–1260 | Secondary | **400 W (26 dBW)** | 100 W | not allocated |
| 1260–1270 | Secondary (sat. Earth-to-space) | 400 W | 100 W | not allocated |
| 1270–1325 | Secondary | 400 W | 100 W | not allocated |
| 5650–5670 | Secondary (sat. Earth-to-space) | **400 W (26 dBW)** | 100 W | 2 W (3 dBW) |
| 5670–5680 | Secondary | 400 W | 100 W | 2 W |
| 5755–5765 | Secondary. "Users must accept interference from ISM users." | 400 W | 100 W | 2 W |
| 5820–5830 | Secondary. Must accept ISM interference | 400 W | 100 W | 2 W |
| 5830–5850 | Secondary. Must accept ISM interference (sat. Space-to-Earth) | 400 W | 100 W | **not listed** |

- **Notes to Schedule 1:**
  - PEP is "the average power supplied to the antenna by a transmitter during one radio frequency cycle at the crest of the modulation envelope taken under normal operating conditions".
  - "Unless airborne power limits are stipulated for the frequency band, the use of Radio Equipment is not permitted airborne." No airborne limit is stipulated for any of these bands.
- **[derived]** FM-ATV is constant-envelope, so its PEP equals carrier power. For negative-modulation AM-VSB the crest is sync tip, so PEP is sync-tip power.
- **EMF (guidance §4.2):** "the use of transmit powers above 10 Watts EIRP or 6.1 Watts ERP is only permitted if the licensee can demonstrate (prior to transmitting) that their use of radio equipment complies with the EMF licence condition" (Licence Condition 9).
- **Coordination** (Notice of Coordination, Table 2):
  - 1240–1325 MHz: "Deployment of a repeater, beacon, gateway or data station in this band is subject to coordination with the MOD and Civil Aviation Authority (CAA)".
  - 430–440 MHz: a repeater or beacon "over 25 Watts ERP … is subject to coordination with the Ministry of Defence (MOD)".
  - 5670–5680 MHz: no unattended beacon within 50 km of NGR SS 206127 or SE 202577.
  - Simplex attended operation needs no clearance.
- **Condition 6(2):** equipment must be used "so that its use does not cause any Undue Interference to any wireless telegraphy".
- **Unwanted emissions (guidance §4.1.1):** "close attention should be given to the transmitted bandwidth and to avoid spectral regrowth". The references are CEPT ERC Rec 74-01 Annex 6 Table 16 (spurious) and ITU-R SM.1541-6 Annex 9 (out-of-band).
- **Bandwidth:** the licence sets **no maximum bandwidth** for any of these bands. Bandwidth is governed by the bandplan and the interference conditions above.

### 2.2 Identification (Condition 6, clauses 21–24)

- **Clause 21:** "The licensee that [sic] shall ensure that: a) the station is clearly identifiable at all times; b) the call sign is transmitted as frequently as is practicable during transmissions; and c) the Call sign is given in voice or other appropriate format consistent with the mode of operation."
- **Clause 22:** an RSL "may be inserted into the transmitted call sign". **Clause 24** table: Scotland = "M". So GM4BRI is correct, and GM4BRI/P is allowed by clause 26.
- **The 2024 licence has no fixed interval.** The previous licence (2013 conditions, clause 13(1)) required the callsign "(b) at least once every 15 minutes … (d) by the same type of transmission that is being used for the communication". That has been replaced by "as frequently as is practicable" (<https://www.ofcom.org.uk/__data/assets/pdf_file/0017/220832/Annex-1-Amateur-Radio-Licence-Conditions-2013.pdf>).
- **Nothing in the licence or guidance says "in vision".** BATC's practical advice is "Don't forget that you will need to display your call sign to remain legal" (5.6 GHz page).
- **[derived]** The safest reading of 21(a)–(c) for ATV is a callsign caption burned into the picture for the whole transmission. That makes the station "clearly identifiable at all times" in a format "consistent with the mode". A voice ID on the sound subcarrier is a useful extra.

Sources: <https://www.ofcom.org.uk/__data/assets/pdf_file/0027/62991/amateur-terms.pdf>, <https://www.ofcom.org.uk/siteassets/resources/documents/manage-your-licence/amateur/amateur_radio_licence_guidance_for_licensees.pdf>, <https://www.ofcom.org.uk/siteassets/resources/documents/manage-your-licence/amateur/notice-of-coordination-procedures>

---

## 3. Analogue TV standards: 625-line PAL, Systems I and G

The source is **ITU-R BT.470-6** (1998), "Conventional television systems", fetched from <https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.470-6-199811-S!!PDF-E.pdf>. The ITU link for **BT.1700** (and for BT.470-7) returned an HTML page rather than the PDF, so **I could not read BT.1700**. Everything below is BT.470-6.

### 3.1 Line timing (Table 1-1, column "B, B1, G, H, I, D, D1, K, K1, L")

| Item | Value | Note |
|---|---|---|
| Lines / field rate / line rate | 625 / 50 Hz / 15 625 Hz | System I tolerance ±0.00002 % (Table 1) |
| H, nominal line period | **64 µs** | |
| a, line-blanking interval | **12 +0.0 −0.3 µs** | |
| b, OH to back edge of line blanking | **10.5 µs** | "average calculated value, for information" |
| c, front porch | **1.5 +0.3 −0.0 µs** | |
| d, line sync pulse | **4.7 ± 0.2 µs** | |
| e, blanking edge rise (10–90 %) | 0.3 ± 0.1 µs | |
| f, sync edge rise (10–90 %) | 0.2 ± 0.1 µs; **System I: 0.25 ± 0.05 µs** | |

- **[derived]**
  - Back porch = b − d = 10.5 − 4.7 = **5.8 µs**.
  - Active line = H − a = **52 µs**.
  - The often-quoted front porch of **1.65 µs** is the mid-point of BT.470's 1.5…1.8 µs tolerance. I did not find 1.65 µs stated in BT.470 **[unverified as a standard value]**.
  - hacktv uses active_left = 10.40 µs and active_width = 51.95 µs for PAL-I **[read in source]**.
- **Levels** (Table 1, note 9): sync = 0, blanking = 30, peak white = 100. Black = blanking for B/G/I PAL. "Peak white-level = 100 corresponds to 1.0 V measured across a matched 75 Ω termination". Sync is −43 on the blanking-based scale.

### 3.2 Field timing (Table 1-2)

| Item | Value |
|---|---|
| Field period | 20 ms |
| j, field-blanking interval | **25 H + a** |
| l, first equalising sequence | **2.5 H** |
| m, field-sync (broad) sequence | **2.5 H** |
| n, second equalising sequence | **2.5 H** |
| p, equalising pulse width | **2.35 ± 0.1 µs** |
| q, field-sync (broad) pulse width | **27.3 µs** nominal (System I: 27.3 ± 0.1) |
| r, interval between broad pulses (serration) | **4.7 ± 0.2 µs** (System I: 4.7 ± 0.1) |
| s, sync/equalising edge rise | 0.2 ± 0.1 µs (System I: 0.25 ± 0.05) |

- Equalising and broad pulses come at H/2 intervals (Fig. 2-1c). **[derived]** q + r = 27.3 + 4.7 = 32.0 µs = H/2.
- **Line numbering** (BT.470-6 Fig. 5a, which I read from the rendered figure):
  - The field-sync datum O_V of the first field is at the start of line 1. Its five equalising pulses occupy lines 623 (second half), 624 and 625. The broad pulses occupy lines 1, 2 and the first half of 3. The second equalising group runs from the middle of line 3 to the end of line 5.
  - The second field's O_V is at the middle of line 313. Its equalising pulses are on 311, 312 and the first half of 313. The broad pulses run from mid-313 to the end of 315. Equalising follows on 316, 317 and the first half of 318.
  - **[derived]** These match l = m = n = 2.5 H.

### 3.3 PAL colour (Table 2, I/PAL and B/G PAL columns)

| Item | Value |
|---|---|
| Luminance | E′Y = 0.299 E′R + 0.587 E′G + 0.114 E′B |
| Colour difference | **E′U = 0.493 (E′B − E′Y)**, **E′V = 0.877 (E′R − E′Y)** |
| Chroma bandwidth (I/PAL) | E′U, E′V < 3 dB at 1.3 MHz, > 20 dB at 4 MHz (B/G: > 20 dB at 4 MHz) |
| Subcarrier | **4 433 618.75 Hz**: ± 1 Hz for I/PAL, ± 5 Hz for B/G |
| Subcarrier relation | f_sc = (1135/4 + 1/625) f_H |
| Burst start (g) | **5.6 ± 0.1 µs after O_H** |
| Burst duration (h) | **2.25 ± 0.23 µs (10 ± 1 cycles)** |
| Burst peak-to-peak | **3/7 of the blanking-to-peak-white difference**, ± 10 % ("For systems D and I the tolerance is ± 3 %") |
| Burst phase | **±135° relative to the E′U axis**, alternating line by line (Swinging burst, Fig. 4b) |
| Burst blanking | "9 lines of the field-blanking interval": lines 311–319, 623–6, 310–318 and 622–5 across the four-field burst-blanking sequence (Fig. 5a) |
| Gamma | Receiver assumed 2.8 |

- **8-field sequence** (Table 2 note): "Field 1 of the sequence of eight colour fields is defined as that field, where the phase φ_EU′ of the extrapolated E′U component of the video burst at the half amplitude point of the leading edge of the line synchronizing pulse of line 1 is in the range −90° ≤ φ_EU′ < 90°."
- Fig. 5a labels burst-blanking fields I–IV as "first and fifth, second and sixth, third and seventh, fourth and eighth fields".
- **[derived]** The 1135/4 quarter-line offset plus the 25 Hz term make the subcarrier phase repeat every 4 frames, i.e. 8 fields.

### 3.4 Radiated signal: System I (UK) and System G (Table 3)

| Item | System I | System G (B/G) |
|---|---|---|
| Channel bandwidth | 8 MHz | 8 MHz (B: 7) |
| Sound carrier relative to vision | **+5.9996 ± 0.0005 MHz** | +5.5 ± 0.001 MHz |
| Nearest channel edge relative to vision | −1.25 MHz | −1.25 MHz |
| Main sideband | **5.5 MHz** | 5 MHz |
| Vestigial sideband | **1.25 MHz** | 0.75 MHz |
| Min. VSB attenuation | **20 dB at −3.0 MHz**, **30 dB at −4.43 MHz** | 20 dB at −1.25 MHz, 20 dB at −3.0 MHz, 30 dB at −4.43 MHz |
| Vision modulation | C3F **negative** | C3F negative |
| Sync level | **100 %** of peak carrier | 100 % |
| Blanking level | **76 ± 2 %** | 75 ± 2.5 % |
| Black − blanking | 0 (nominal) | 0 to 2 % (nominal) |
| Peak white | **20 ± 2 %** | 10 to 15 % |
| Sound modulation | F3E | F3E |
| Sound deviation | **± 50 kHz** | ± 50 kHz |
| Sound pre-emphasis | **50 µs** | 50 µs |
| Vision/sound e.r.p. ratio | **5/1**. Note 25: "The ratio 20:1 is used in the United Kingdom." | 20/1 to 10/1 |

- **UK NICAM** (note 7): "The second sound carrier is 6.552 MHz above the vision carrier and is DQPSK modulated with a 728 kbit/s sound and data multiplex … ratio between vision and sound e.r.p. … 100/1."
- **"System II"** (note 32): for UK PAL "in the upper-adjacent channel to a DVB-T service, the nominal width of the vestigial sideband is proposed to be 0.75 MHz, with minimum attenuations of … 20 (−1.25), 45 (−1.45) in dB at MHz".
- **[derived]** The occupied spectrum of PAL-I with FM sound is about −1.25 MHz to +6.1 MHz around the vision carrier, i.e. **about 7.3 MHz**.

Sources: <https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.470-6-199811-S!!PDF-E.pdf>

---

## 4. Amateur FM-ATV conventions

### 4.1 IARU Region 1 Technical Recommendation V.2, "Standards for Microwave FM ATV" (Torremolinos 1991, Lillehammer 1999)

The table is printed in VHF Handbook 6.91/7.00 §8.8.10 (<https://rsgb.org/main/files/2013/05/VHF-Handbook_7.00.pdf>, p.119). The current handbook 10.03 keeps only the narrative. The PDF's label and value columns are misaligned: there are 8 labels and 10 values. I have paired them as below **[derived pairing; verify against an original]**.

| Parameter | > 1 GHz, < 24 GHz | > 24 GHz |
|---|---|---|
| Mode of emission | F5/F3 | F5/F3 |
| Video bandwidth (3 dB) | 5 MHz | 5 MHz |
| Pre-emphasis | **CCIR Rec 405.1** | CCIR Rec 405.1 |
| Colour subcarrier | 4.433618 MHz | 4.433618 MHz |
| Max instantaneous mod. index | – | .05 |
| Peak deviation (with pre-emphasis) | **3.5 MHz** | 3.5 MHz |
| Channel bandwidth | **12 MHz at −40 dB, 18 MHz at −50 dB** | 12 MHz at −40 dB |
| Sound subcarrier | **5.5 MHz** | 5.5 or 6 MHz |
| Sound subcarrier amplitude (re peak video) | – | −14 dB |
| Sound subcarrier mod. index | 0.07 | 0.2 |

- **Notes to V.2:**
  - "1. A video filter having a −3 dB bandwidth of 5 MHz should be included in the modulating amplifier."
  - "2. A video peak clipper should be included after the pre-emphasis but before the video filter."
  - "3. DC clamping of the video signal should be included to prevent the nominal carrier frequency from changing with different television scenes."
  - "4. An RF output filter should be included…"
- **The narrative (still in v10.03, §26.1):**
  - "a channel bandwidth of 18 MHz (−60 dB) was recommended".
  - In 1996 "it was decided that it was not possible to adhere to the given bandwidth limitation when complying with all other parts of the recommendation. This was due to the recommended audio sub-carriers at 5.5 or 6 MHz."
  - The older on-line commentary adds that the standard would "probably [delete] the audio-carriers … which are not essential for ATV where often NBFM in the 145 MHz band is used for 'talk-back'" (<http://www.users.zetnet.co.uk/ross/radio/iaru-1.htm>).
- **[derived]** Carson bandwidth = 2 × (3.5 + 5) = **17 MHz** for video alone. With a 6 MHz sound subcarrier it is about 2 × (3.5 + 6.05) ≈ **19 MHz**.
- **BATC "ATV standards" page** shows a "Proposed Spectrum Mask for ATV above 1240 MHz", captioned "Spectrum mask for 16 MHz FM ATV signal" (<https://wiki.batc.org.uk/ATV_standards>). Read from the graph **[approximate]**:
  - 0 dBc to about ±8–9 MHz
  - −30 dBc at about ±10 MHz
  - −40 dBc at about ±14 MHz
  - −70 dBc at about ±17 MHz
  - Its status ("proposed") and origin are not stated.

### 4.2 CCIR (ITU-R F.405-1) 625-line video pre-emphasis

Source: ITU-R F.405-1 (1959–1970), <https://www.itu.int/dms_pubrec/itu-r/rec/f/R-REC-F.405-1-197007-W!!PDF-E.pdf>.

- **"relative deviation (dB) = 10 log [(1 + C f²)/(1 + B f²)] − A"**, with f in MHz.
- **625 lines:** **A = 11.0, B = 0.4083, C = 10.21**. Cross-over frequency **1.512 MHz**. "Deviations (peak-to-peak) at low frequencies" **2.255 MHz**.
- **Note 1:** "a peak-to-peak signal of 1 V … produces, in the absence of pre-emphasis, a peak-to-peak deviation of 8 MHz. When there is pre-emphasis, a sinusoidal wave of 1 V peak-to-peak at a video-frequency corresponding to 0 dB relative deviation (cross-over frequency) produces a peak-to-peak deviation of 8 MHz."
- The network must be minimum-phase. The tolerance is ±(0.1 + 0.05 f/fc) dB from 0.01 MHz to the top of the video band.
- **[derived] As a transfer function:**
  - H(s) = 10^(−11/20) · (1 + s τ₁)/(1 + s τ₂).
  - τ₁ = √C/(2π) µs = **508.5 ns** (zero at 313.0 kHz).
  - τ₂ = √B/(2π) µs = **101.7 ns** (pole at 1.565 MHz).
  - Low-frequency gain is **−11.0 dB**. The asymptotic HF gain is 10 log(C/B) − 11 = **+2.98 dB**, so the total lift is ≈ 14 dB.
  - Values: −5.92 dB at 0.5 MHz, −1.99 dB at 1 MHz, 0 dB at 1.512 MHz, +1.98 dB at 3 MHz, +2.49 dB at 4.43 MHz, +2.59 dB at 5 MHz.
- **Amateur use:**
  - IARU V.2 specifies it (above).
  - G3RFL: "There is a Standard for FM ATV Pre_emphasis and De_emphasis … The pre-emphasis network raises the level of the hf component by 14db with respect to the lf components" (<http://g3rfl.co.uk/ccir.htm>).
  - Wood & Douglas sold a "VP/D1 … dual function board for video pre-emphasis … or de-emphasis", which "made a big improvement to picture quality (1-2 picture grades)" (BATC W&D page).
  - WB9OQM notes that the ATVQ component values "indicate CCIR Recommendation 405-1" (<http://mathison.freeshell.org/fmtv/preemph.htm>).
  - **The Richwave RTC6705 FPV datasheet gives no video pre-emphasis.** It lists only audio pre-emphasis. Whether FPV links use CCIR 405 is **[unverified]**.

### 4.3 Deviation, sound subcarriers and occupied bandwidth in practice

- **Peak deviation:**
  - IARU V.2: 3.5 MHz (above).
  - RF-Links (US, NTSC) worked example: "If the deviation is chosen to be 4 MHz and the maximum video frequency of 3.5 MHz, the resulting RF bandwidth is 15 MHz" (<https://www.rf-links.com/newsite/pdf/am%20versus%20fm%20tv%20signals.pdf>).
  - CCIR F.405/J.61 broadcast-link scaling: 8 MHz p-p per volt at cross-over.
  - **I did not find a sourced figure for typical UK 23cm FM-ATV deviation** (Comtech modules and so on) **or for 5.8 GHz FPV video deviation** **[unverified]**.
- **Sound subcarriers:**
  - **6.0 MHz** is the UK amateur norm. W&D's FIF1 "was a 6MHz sound sub-carrier reception board". The Solent TX had "6 MHz intercarrier sound". G3RFL traps "the 6MHz intercarrier sound".
  - **5.5 or 6 MHz** in IARU V.2. RF-Links: "Normally the audio subcarrier … is either 6 or 5.5 MHz."
  - **FPV (RTC6705):** "two modulated audio subcarriers at 6MHz and 6.5MHz respectively" (left 6.0, right 6.5). THD is specified "with ±25KHz frequency deviation". The audio pre-emphasis corner is 12 kHz. "Video carrier to audio carrier ratio" is −25 dBc typical. BATC says FPV audio subcarriers are "at a level 20dB lower than that of the main (video) carrier".
  - **hacktv's PAL-FM (satellite) mode** uses 6.5 MHz, ±85 kHz, 50 µs **[read in source]**.
- **Occupied bandwidth:**
  - IARU 12 MHz at −40 dB and 18 MHz at −50 dB.
  - BATC mask "16 MHz FM ATV".
  - BATC measured 25–30 MHz for FPV units.
- **What receivers expect:**
  - Classic UK FM-ATV receivers (W&D VIDIF, Comtech) use a 50 MHz or 479.5/480 MHz IF with a PLL demodulator, de-emphasis, and a 6 MHz sound strip. The RF-Links note says "479.5 MHz chosen by the Europeans". BATC says FPV RX "uses a 480MHz IF (same as the Comtech)".
  - Monitors with "video squelch" blank noisy pictures, so BATC recommends sync processors (Syncsmurf, ACE).

Sources: VHF Handbook 7.00 §8.8.10, <https://www.itu.int/dms_pubrec/itu-r/rec/f/R-REC-F.405-1-197007-W!!PDF-E.pdf>, <https://wiki.batc.org.uk/ATV_standards>, <https://wiki.batc.org.uk/Wood_%26_Douglas_FM_TV_equipment>, <https://wiki.batc.org.uk/Solent_ATV_units>, <http://g3rfl.co.uk/ccir.htm>, RTC6705 datasheet (above)

---

## 5. Pluto-family hardware

### 5.1 Host throughput

| Path | Figure | Source |
|---|---|---|
| ADI spec page | USB 2.0 "streams up to 4MSPS with no dropped samples" | <https://wiki.analog.com/university/tools/pluto/devs/specs> |
| ADI measured, RX, `iio_readdev -u usb:` (libusb backend) | **26.1 MiB/s** over 1 h 19 min | <https://wiki.analog.com/university/tools/pluto/devs/performance> |
| ADI measured, RX, `iio_readdev -n 192.168.2.1` (USB-RNDIS network) | **20.4 MiB/s** | same |
| ADI measured, RX, local on the Pluto's ARM | 107 MiB/s | same |
| pysdr.org | "USB 2.0 … limits the sample rate to around 5 MHz if you want to receive 100% of samples" | <https://pysdr.org/content/pluto.html> |
| rtl-sdr.com, 2017 | "anything higher than 4 MSPS causes lost samples" (SDR#) | <https://www.rtl-sdr.com/some-further-tests-with-the-adalm-pluto-sdr/> |
| Tezuka firmware (F5OEO) | "Complex 8-bit mode unlocks stable streaming up to **14 MHz over USB** and **45 MHz over GbE**" | <https://github.com/F5OEO/tezuka_fw> |
| pysdr.org, Pluto+ over GbE | 16-bit IQ at "roughly 28 MHz" ceiling. "Sample rates closer to 10 MHz are more realistic for Python based SDR applications with the Pluto+ over Ethernet" | pysdr.org |
| LibreSDR (hz12opensource) over GbE | "continuous 20MSPS with the libiio/PlutoSDR API over gigabit Ethernet" stock. "about 27.5 MSPS continuous" with the 1100 MHz CPU / 750 MHz DDR overclock. Needs a bigger libiio buffer above 20 MS/s | <https://github.com/hz12opensource/libresdr> |

- **[derived] Conversions at 4 bytes per IQ sample (16-bit I + 16-bit Q, which pysdr confirms is the default):**
  - 26.1 MiB/s ≈ **6.8 MS/s** (libusb).
  - 20.4 MiB/s ≈ **5.3 MS/s** (RNDIS).
  - At 2 bytes per sample (8-bit), 26.1 MiB/s ≈ 13.7 MS/s. That agrees with Tezuka's 14 MHz.
- **TX-only and RX+TX simultaneous:** I found **no ADI-measured figure** for either **[unverified]**.
  - A commenter on rtl-sdr.com quotes an explanation that "Since it is half duplex [USB], that would be ~22.5 Mbytes/second for transmission, and ~22.5 Mbytes/second for reception … What we actually achieve with the PlutoSDR is closer to 7.5 – 12 MSPS, but this depends on the USB host". The original is attributed to ADI, but I could not find it **[unverified]**.
  - **[derived]** USB 2.0 is a shared half-duplex bus. Full-duplex RX+TX at rate R costs about 2R of bus throughput, so plan for about **3 MS/s each way** when streaming both at once over USB.
  - Whether Tezuka's 8-bit mode covers TX is not stated **[unverified]**.

### 5.2 Tuning range, and detecting it at runtime

- **ADI customising page** (<https://wiki.analog.com/university/tools/pluto/users/customizing>):

| Transceiver | LO tuning range | Bandwidth | Channels |
|---|---|---|---|
| AD9363 (default ADALM-PLUTO) | 325 – 3800 MHz | 20 MHz | 2 Rx, 2 Tx |
| AD9364 | 70 – 6000 MHz | 56 MHz | 1 Rx, 1 Tx |
| AD9361 | 70 – 6000 MHz | 56 MHz | 2 Rx, 2 Tx |

  - The commands are `fw_setenv attr_name compatible`, `fw_setenv attr_val ad9364`, then `reboot`. "Starting with PlutoSDR firmware revision v0.32 an additional variable should be set: `fw_setenv compatible ad9364`".
  - Rev C with `ad9361` and `mode 2r2t` gives "2 Rx, 2 Tx, 30.72 MSPS max data rate".
  - ADI frames this as restoring early AD9364-based units. AB9IL: "the original limits may be restored, though actual performance may not be … [guaranteed]" (<https://www.ab9il.net/software-defined-radio/plutosdr.html>).
  - K0SM: performance has "High variability when AD9363 out of spec" (<https://www.rochesterham.org/meetings/2023-12-06%20Pluto%20SDR.pdf>).
- **Tezuka:** "Expanded tuning range from 47.5 MHz to 6 GHz".
- **Pluto+:** AD9363, "Can hack to AD9361 or AD9364", "RF Range 70MHZ~6GHZ", 40 MHz 0.5 ppm VCTCXO, GbE (<https://github.com/plutoplus/plutoplus>).
- **LibreSDR:** the hz12opensource README says it uses "LVDS mode for AD9361". Tezuka's board table links a vendor listing named "libresdr-zynqsdr-ad9363". **Check which chip Brian's unit has** (see §8).
- **Pluto nano:** Tezuka lists "Pluto Nano | 7010 | no SD | no GbE | … | usb-c", vendor "AliExpress HamGeek ?". I found no datasheet giving its transceiver part **[unverified]**.
- **The driver's limits** (analogdevicesinc/linux `drivers/iio/adc/ad9361_regs.h` and `ad9361.c`, **[read in source]**):
  - `AD9363A_MIN_CARRIER_FREQ_HZ 325000000`, `AD9363A_MAX_CARRIER_FREQ_HZ 3800000000`.
  - Otherwise `MIN_RX_CARRIER_FREQ_HZ 70000000`, `MIN_TX_CARRIER_FREQ_HZ 46875001`, `MAX_CARRIER_FREQ_HZ 6000000000`.
  - `ad9361_validate_rfpll()` returns `-EINVAL` outside the range.
  - `ad9361_validate_rf_bw()` clamps RF bandwidth to 200 kHz…20 MHz for AD9363A and 200 kHz…56 MHz otherwise.
  - `MAX_BASEBAND_RATE 61440000`.
- **Runtime detection via libiio:**
  - Read the `frequency_available` attribute on `ad9361-phy` channels **altvoltage0 (RX_LO)** and **altvoltage1 (TX_LO)**. The driver returns `"[min 1 max]"`, chosen by device ID.
  - The attribute was added in commit b96aacd90c, "Add *_available RANGE attributes", 2017-11-02. I confirmed this in the diff **[read in source]**.
  - ADI's own `iio_info` example shows a Pluto patched to AD9364 (<https://wiki.analog.com/resources/tools-software/linux-software/libiio/iio_info>):
    - context attributes `hw_model: Analog Devices PlutoSDR Rev.B (Z7010-AD9363)` and `ad9361-phy,model: ad9364`
    - RX LO `frequency_available value: [70000000 1 6000000000]`
    - TX LO `frequency_available value: [46875001 1 6000000000]`
    - `sampling_frequency_available value: [2083333 1 61440000]`
  - **[derived] Detection logic:**
    1. Prefer `frequency_available`.
    2. Fall back to the context attribute `ad9361-phy,model` (`ad9363a` means 325–3800 MHz).
    3. Finally, write `frequency` and treat an error, or a read-back that does not match, as out of range.
  - Note that `hw_model` names the physical chip, while `ad9361-phy,model` names the driver personality. A 6 GHz range on a "Z7010-AD9363" board is the out-of-spec hack.

### 5.3 TX power, RX noise figure

**Chip datasheet**, AD9361 Rev. F, specifications table (<https://www.analog.com/media/en/technical-documentation/data-sheets/ad9361.pdf>; read via the radiolocman mirror, pages 3–4):

| Parameter | 800 MHz | 2.4 GHz | 5.5 GHz |
|---|---|---|---|
| TX maximum output power | **8 dBm** (1 MHz tone, 50 Ω) | **7.5 dBm** | **6.5 dBm** (7 MHz tone) |
| TX noise floor (90 MHz offset) | −157 dBm/Hz | −156 dBm/Hz | −151.5 dBm/Hz |
| TX OIP3 | 23 dBm | 19 dBm | 17 dBm |
| RX noise figure (max RX gain) | **2 dB** | **3 dB** | **3.8 dB** |
| RX IIP3 (max gain) | −18 dBm | −14 dBm | −17 dBm |

TX general: centre frequency 46.875–6000 MHz, "Power Control Range 90 dB", resolution 0.25 dB. RX gain 0–74.5 dB, "65.5 dB At 5500 MHz".

**Measured on ADALM-Pluto units:**

| Frequency | TX out (K0SM, 2023) | Other reports |
|---|---|---|
| 70 MHz | −6.0 dBm | |
| 144 MHz | +1.0 dBm | |
| **432 MHz** | **+3.5 dBm** | |
| **1296 MHz** | **+2.5 dBm** | |
| 2400 MHz | +1.0 dBm | DD1US: "maximum output power of my unit is +2dBm" at 2.4 GHz |
| 3400 MHz | 0.0 dBm | |
| **5760 MHz** | **−3.0 dBm** | K0SM: "N2WK at 50km on 5760 with 6dBi and about −4dBm output" |
| 10368 MHz | −30 dBm | |

- **K0SM, more:**
  - "TX puts out about −4 to +5dBm depending on frequency*" (* "High variability when AD9363 out of spec").
  - Approximate NF "about 4.5dB at 2304 MHz, and something near 10 dB at 5760 MHz".
  - "Mine is pretty deaf on 5760 — 25dB gain and 1.0 dB NF preamp makes it a real station".
  - "LNA helpful on 3400 and 5760".
  - "TX doesn't filter odd order aliases … Filter where you need to."
  - The board "Uses '1GHz' baluns, but datasheet goes to 8 GHz — it's okay for 5760".
- K3XEC (2021, HP 437B + 8481A) measured the Pluto at up to **3.39 dBm** across his sweep. He does not state the swept range in the text (<https://k3xec.com/power-output/>).
- AB9IL: "Transmit output power is about 7 dBm."
- **Measured Pluto NF at 435 MHz and 1.3 GHz: none found** **[unverified]**.

**[derived] Conclusions:**
- With 0 to +3.5 dBm available at 435 MHz and 1.3 GHz, and about −3 dBm at 5.76 GHz, reaching even 1 W (+30 dBm) needs **≥ 30 dB of PA gain at 23cm and ≥ 33 dB at 6cm**. BATC's FPV units are 600 mW (+27.8 dBm) for comparison.
- An LNA is essential at 6cm (Pluto NF about 10 dB) and worthwhile at 23cm, with a band-pass filter after it. Both K0SM and BATC recommend the filter.
- A TX low-pass or band-pass filter is mandatory because of the aliases K0SM reports.

### 5.4 Bandwidth and the shared sample rate

- AD9363: "Tunable channel bandwidth: 200 kHz – 20 MHz". "Variable output data rates: 61.44 MSPS – 65.1 kSPS" (ADI specs page). AD9364/AD9361: 56 MHz (customising page).
- The driver clamps `rf_bandwidth` to 20 MHz on AD9363A (above).
- **RX and TX share one baseband sample rate.** pysdr: "The sample rate is shared between the RX and TX". In DJ0ABR's QO-100 code: "tx sample rate (must be same as RX samp rate)". The RX and TX LOs are independent.
- The minimum rate without the FIR is 25 MHz/12 = 2.083 MS/s. The driver reports `sampling_frequency_available [2083333 1 61440000]`.

Sources: listed in the tables above, plus <https://www.dd1us.de/Downloads/ADALM%20Pluto%20TX%20output%20power%20rev1.pdf>

---

## 6. hacktv as a reference (GPL-3.0: numbers only, no code)

Repo <https://github.com/fsphil/hacktv>, read at commit 80d98eab (2025-12-31), `src/hacktv.c`, `src/video.c`, `src/fir.c`.

- **Sample rate:**
  - The default is `s.samplerate = 16000000`.
  - The help text says "16MHz works well with PAL modes, and 13.5MHz for NTSC modes. 20.25MHz is ideal for the D/D2-MAC modes" and "you also need to ensure the sample rate is adequate to contain both the video signal and audio subcarriers".
  - "The number of samples per line is rounded to the nearest integer, which may result in a slight frame rate error." **[derived]** At 16 MS/s that is 1024 samples per line, exactly.
- **PAL-I config (`vid_config_pal_i`):**
  - `VID_VSB`, `vsb_upper_bw 5500000`, `vsb_lower_bw 1250000`.
  - Levels: `white_level 0.20`, `black_level 0.76`, `blanking_level 0.76`, `sync_level 1.00`. These are the BT.470 System I levels.
  - Sync: `hsync_width 4.70 µs`, `vsync_short_width 2.35 µs`, `vsync_long_width 27.30 µs`, `sync_rise 0.25 µs`.
  - Burst: `burst_left 5.6 µs`, `burst_width 2.25 µs`, `burst_rise 0.30 µs`, `burst_level 3/7`.
  - Colour: `colour_carrier 17734475/4` (4 433 618.75 Hz), `colour_bw 1.4e6` ("3dB gaussian filter"), `eu_co 0.493`, `ev_co 0.877`.
  - Sound: `fm_mono_carrier 6000000 − 400` Hz, `fm_mono_deviation 50000`, `fm_mono_preemph VID_50US`.
  - NICAM: `nicam_carrier 6552000`.
  - Relative levels: `video_level 0.71`, `fm_mono_level 0.22`, `nicam_level 0.07/2`. **[derived]** The vision/FM-sound amplitude ratio 0.71/0.22 is about 10:1 in power.
- **VSB filtering:**
  - A 51-tap complex band-pass is built by `fir_complex_band_pass(taps, 51, sample_rate, −vsb_lower_bw, vsb_upper_bw, 750000, 1)`. It is a Kaiser-windowed (β = 7.0) low-pass of half-width (upper + lower)/2, with a 750 kHz transition, frequency-shifted to the band centre.
  - This is applied to the complex baseband. The video is then on a carrier at 0 Hz.
- **FM video (`vid_config_pal_fm`, "PAL FM (satellite)"):**
  - `fm_deviation 16e6` ("16 MHz/V"). Levels run from `sync_level −0.50` to `white_level 0.50`. `fm_mono_carrier 6500000`, `fm_mono_deviation 85000`.
  - Energy dispersal is commented out ("1 MHz deviation (2 MHz p-p)").
  - The FM modulator is a lookup table: phase increment = 2π/fs · (f + x · deviation).
- **FM video pre-emphasis:**
  - It is fixed FIR taps, "Test taps for a CCIR-405 625 line video pre-emphasis filter at 28 MHz / 20.25 MHz / 20 MHz / 14 MHz (5.0 MHz video)". There are 67 taps per set, and the code warns if the sample rate is not one of 14, 20, 20.25 or 28 MHz.
  - **[derived]** I evaluated the 20.25 MHz taps: −11.00 dB at DC, −5.92 dB at 0.5 MHz, −1.99 dB at 1 MHz, 0.00 dB at 1.5 MHz, +1.98 dB at 3 MHz, +2.43 dB at 4.43 MHz, −3.43 dB at 5 MHz, −77.6 dB at 6 MHz.
  - So the taps are the exact F.405-1 625-line curve multiplied by a 5 MHz low-pass. That matches IARU V.2 note 1.
  - **[derived]** At hacktv's 16 MHz/V scale a 1.0-unit sync-to-white step gives 16 MHz p-p before emphasis, about 4.5 MHz p-p at low frequency after the −11 dB. That is twice F.405-1's 2.255 MHz LF figure.
- **Audio pre-emphasis** is FIR at 32 kHz ("40-15000Hz low pass … with no, 50us and 75us pre-emphasis"). The code comments that "the phase response of these filters is not a good match for a real FM pre-emphasis circuit".
- **Pluto via SoapySDR:**
  - hacktv supports `-o soapysdr[:<opts>]` and opens the TX at the video sample rate (`rf_soapysdr_open(..., s.vid.sample_rate, ...)`).
  - I found **no Pluto-specific notes** in the repo or online **[unverified]**.
  - **[derived]** The 16 MS/s default at 4 B/sample is 64 MB/s, well beyond a Pluto's USB link (§5.1).

---

## 7. Implications for OpenHamShack ATV

### 7.1 What each mode needs (all [derived] from §3–§5)

| Mode | Occupied RF | Min. complex sample rate (with filter margin) | Samples per 64 µs line at that rate |
|---|---|---|---|
| PAL-I AM-VSB + 6 MHz FM sound | −1.25…+6.1 MHz ≈ 7.3 MHz | **≈ 8–10 MS/s** with the LO offset about +2.4 MHz from vision | 8 MS/s → 512; 10 → 640 |
| PAL-I AM-VSB without sound | −1.25…+5.5 MHz ≈ 6.75 MHz | ≈ 8 MS/s | 512 |
| PAL-G AM-VSB + 5.5 MHz sound | −0.75…+5.6 MHz ≈ 6.4 MHz | ≈ 8 MS/s | 512 |
| FM-ATV to IARU V.2 (3.5 MHz peak dev, 5 MHz video) | ≈ 17 MHz (Carson); 18 MHz at −50 dB | **≈ 20 MS/s** | 1280 |
| FM-ATV + 6.0/6.5 MHz sound subcarrier | ≈ 19–20 MHz | ≈ 20–24 MS/s | 20.25 → 1296 |
| FPV-compatible FM (BATC measured 25–30 MHz) | 25–30 MHz | ≈ 28–32 MS/s | 28 → 1792 |
| Reduced FM-ATV (for example ±2 MHz peak, 3 MHz video, no subcarrier) | ≈ 10 MHz | ≈ 12 MS/s | 768 |

- Line-locked rates that make each line an integer number of samples: 8, 10, 12, 13.5, 16, 20, 20.25, 27 and 28 MS/s. All are within the AD936x's 2.083–61.44 MS/s range.
- If F.405 pre-emphasis is wanted at an odd rate, design the FIR for that rate rather than reuse a fixed table. hacktv's warning shows why.

### 7.2 What each device can carry

| Device / link | Sustained IQ rate (sourced → derived) | Fits |
|---|---|---|
| ADALM-Pluto, USB 2.0, 16-bit | ~6.8 MS/s RX (ADI 26.1 MiB/s); ADI spec "4MSPS no drops"; ~3 MS/s each way if full duplex [derived] | **Nothing standard.** Only reduced-bandwidth AM, or monochrome narrow FM. Half-duplex is essential |
| Pluto / Pluto nano, Tezuka 8-bit over USB | "up to 14 MHz" (RX; TX unverified) | PAL-I/G AM-VSB with sound (8–10 MS/s). Reduced FM-ATV (12 MS/s). **Not** standard 17–20 MHz FM-ATV |
| Pluto+ over GbE, 16-bit | ~28 MS/s ceiling; "closer to 10" in Python | AM-VSB comfortably. FM-ATV at 20 MS/s **with a compiled DSP path** |
| LibreSDR over GbE, 16-bit | 20 MS/s stock, 27.5 MS/s overclocked | FM-ATV at 20–20.25 MS/s. FPV-width at 28 only with overclock |
| Any, Tezuka 8-bit over GbE | "45 MHz" | Everything above, including FPV-width |

- **RF bandwidth:** AD9363 (stock Pluto, Pluto+) is clamped to a 20 MHz analogue filter. That just passes 17–19 MHz FM-ATV but not 25–30 MHz FPV-style signals. AD9361/AD9364 allow 56 MHz.
- **Half duplex:** ATV is naturally half duplex, with talkback on 2m. Since RX and TX share one sample rate, run RX and TX at the same mode rate and never stream both at once over USB.
- **Not verified:** an alternative for the stock USB Pluto is on-device modulation. Stream 8-bit real composite video at about 13.5 MS/s (13.5 MB/s, which USB can carry) and generate VSB or FM in the FPGA or ARM. Nobody I found has done this; it would need FPGA work.

### 7.3 Per band

| Band | Where | Device requirement | Power chain |
|---|---|---|---|
| 70cm | **No analogue TX** (RSGB 2020, IARU 2021). Analogue RX/monitoring only. DATV at 437 MHz is out of scope | Stock AD9363 tunes 435 MHz | n/a for TX |
| 23cm | FM-ATV: repeater inputs **1248/1249 MHz**; simplex **1255** (BATC); 1275 (BATC, CAA-dependent). AM-VSB possible but not current UK practice [unverified] | Stock AD9363 tunes 1.3 GHz | Pluto about +2.5 dBm, so PA needed; Full limit 400 W PEP. LNA and BPF recommended |
| 6cm | **5665 MHz only** for a ~25–30 MHz FM signal. 5650–5680 is all-mode and secondary. Wider signals cannot fit anywhere else | **Needs a 6 GHz range** (`compatible ad9364/ad9361`, or Tezuka, or an AD9361 board), detected at runtime (§5.2) | Pluto about −3 dBm; NF about 10 dB. **PA and LNA are mandatory** in practice |

- **Galileo:** keep FM-ATV on 23cm off 1258–1300 MHz where possible. Repeater inputs at 1248/1249 MHz do this; 1255 and 1275 MHz do not. Show a warning in the UI.
- **ID:** keep a callsign caption in vision for the whole transmission (Condition 6(21)). Optionally add a periodic voice or CW ID on the sound subcarrier. The application should not allow TX with the caption disabled.
- **Licence class gating:** Full is 400 W; Intermediate 100 W; Foundation has no 23cm, is 2 W on 6cm, and has no 5830–5850 MHz. This affects only PA advice, not the SDR.

---

## 8. Open questions for Brian

1. **70cm:** do you want analogue RX-only on 70cm, or drop 70cm from the first release? Analogue TX there is outside the bandplan.
2. **Which exact boards do you have?**
   - The LibreSDR: is it AD9361, or the AD9363 variant that the Tezuka table links?
   - What chip and firmware is on the Pluto nano?
   - Running `iio_info` and reporting `hw_model` and `ad9361-phy,model` on each would settle it.
3. **Host link:** is the app allowed to require Ethernet (Pluto+/LibreSDR) for FM-ATV, with USB Plutos limited to AM-VSB or reduced FM? Or should we chase Tezuka 8-bit, whose TX support is unverified?
4. **Modulation priority on 23cm:** standard FM-ATV, for compatibility with Comtech, W&D and FPV-type receivers and GB3-repeater inputs? Or AM-VSB, which fits a Pluto more easily but has no sourced current UK user base?
5. **Who will you work?**
   - There are no TV repeaters in Scotland (ukrepeater list). Is there local 23cm or 5.6 GHz simplex activity near you, and on what frequency and deviation?
   - That decides the deviation and sound-subcarrier defaults (6.0 vs 6.5 MHz), which I could not source for UK practice.
6. **6cm:** confirm 5665 MHz, horizontal polarisation (the BATC standard), and FPV-compatible deviation as the 6cm target.
7. **RF hardware:** which PAs, LNAs and filters do you have or intend to buy for 23cm and 6cm, and at what power? This decides the drive levels, the PTT/sequencing outputs, and whether the app needs PA-control GPIO.
8. **Pre-emphasis:** should CCIR 405 (625-line) be on by default for FM? It is the IARU V.2 recommendation. Whether FPV receivers de-emphasise is unverified.
9. **Talkback:** should the app also drive 2m NBFM talkback? The RSGB 2026 plan still lists "144.750 MHz ATV Talk-back"; the 144.525 SSB talkback was deleted in 2015. Or is that left to a separate rig?

---

## 9. Measured on hardware: the test laptop's Pluto nano (4 October 2026)

Receive only; nothing was transmitted and the transmitter was never
enabled. Laptop `openhamshack-laptop` (Lenovo V14 G2, i5-1135G7, Debian 13,
libiio 0.26), Pluto nano on USB.

**`iio_info -u ip:192.168.2.1` (context attributes):**

| attribute | value |
|---|---|
| hw_model | Nano PlutoSDR (Z7010-AD9361) |
| hw_model_variant | 1 |
| hw_serial | UQRUR7HPFYWAMKGY |
| fw_version | tezuka-v0.3.21 |
| ad9361-phy,model | **ad9361** |
| ad9361-phy,xo_correction | 40000000 |
| local,kernel | 6.12.77 |

- RX LO and TX LO `frequency_available`: **[46875001 1 6000000000]** -
  46.875 MHz to 6 GHz, so 70cm, 23cm and 6cm are all in range (Tezuka's
  "expanded tuning range", and the chip is an AD9361, not an AD9363).
- `sampling_frequency_available` [2083333 1 61440000]; RX `rf_bandwidth_available`
  [200000 1 56000000] on one channel and [200000 1 40000000] on others;
  RX `hardwaregain_available` [-1 1 73]; TX hardwaregain [-89.75 0.25 0].
- The TX LO was powered down (`powerdown 1`) throughout.
- `iio_info -s` lists the same radio three ways: ip:Nano.local, usb:3.4.5
  and (direct probe) ip:192.168.2.1; bare `usb:` also opens it.

**Receive throughput** (16-bit IQ, 30 M samples, `iio_readdev`, raw):

| route | 64 k-sample buffers | 1 M-sample buffers |
|---|---|---|
| usb:3.4.5 (libusb) | 6.61 MS/s | 7.08 MS/s |
| ip:192.168.2.1 (USB network / RNDIS) | 4.98 MS/s | 5.00 MS/s |

These agree with ADI's 26.1 MiB/s (6.8 MS/s) and 20.4 MiB/s (5.3 MS/s).

**OpenHamShack ATV receiving** (delivered rate measured by the driver over ~10 s):

| route, rate | first driver (refill in the receive thread) | reader thread, long buffers |
|---|---|---|
| usb:, 6.25 MS/s | 4.85 MS/s (short) | **6.25 MS/s (not short)** |
| usb:, 8 MS/s | 4.89 (short) | 7.42 (short) |
| ip:192.168.2.1, 6.25 MS/s | 4.66 (short) | 5.22 (short) |
| ip:192.168.2.1, 8 MS/s | 4.35 (short) | 5.36 (short) |

So a USB Pluto is used through `usb:` (now the default and the first
address discovery offers) at 6.25 MS/s; the USB network route is steered to
5 MS/s. At 23cm FM, 6.25 MS/s, with the window open: engine + window
about 57 % of one core of the i5-1135G7; headless engine 16-28 %. No
signal was on the band: the spectrum showed a flat floor and the LO's DC
spike, and the decoder stayed searching, as expected.

---

## 10. Measured on hardware: Brian's LibreSDR (4 October 2026)

Receive only from the app; nothing was transmitted. Same laptop, which
reached the LibreSDR over **wired Gigabit Ethernet** (enp2s0, 1000 Mb/s;
the Wi-Fi interface was down; ping 0.18 ms).

**`iio_info -u ip:198.51.100.74`:**

| item | value |
|---|---|
| context attributes | only `local,kernel` 6.1.70-35308-ge2e62cc28c80, `uri`, `ip,ip-addr` |
| hw_model | **none reported** |
| ad9361-phy,model | **none reported** |
| fw_version | none reported; iiod backend 0.24; description "198.51.100.74 Linux libre 6.1.70 … armv7l" |
| devices | ad9361-phy, xadc, cf-ad9361-dds-core-lpc, cf-ad9361-lpc |
| TX LO frequency_available | [46875001 1 6000000000] |
| RX range as the driver measures it (RX ∩ TX) | 70 MHz – 6 GHz |
| sampling_frequency_available | [2083333 1 30720000] (30.72 MS/s maximum) |
| rf_bandwidth_available | [200000 1 56000000] and [200000 1 40000000] on different channels |
| TX LO | powered down (`powerdown 1`) before, during and after every test |

`iio_info -s` listed it as ip:libre-2.local (once by IPv4, once by IPv6).

**Raw receive throughput** (`iio_readdev`, 1 M-sample buffers): 10 MS/s set →
9.6 delivered; 16 → 15.4; 20 → 16.4; 25 → 16.4; 30.72 → 16.4. At 20 MS/s
with 256 k / 1 M / 4 M buffers: 16.07 / 16.19 / 15.81. **The ceiling is
~16.2 MS/s** on this unit with its stock iiod over Gigabit — not the 20 its
README states — and buffer size does not move it, so the limit is on the
LibreSDR's side.

**OpenHamShack ATV receiving** (driver's delivered rate, ~10 s each): 10 MS/s →
10.00 (not short); 16 MS/s → 16.00 (not short); 20 MS/s → 16.40 (short,
flagged). Engine CPU headless on the i5-1135G7: 0.33 core at 10 MS/s, 0.8
at 16 and 20; with the window open at 16 MS/s about 1.2 cores.

**Full duplex** (a separate script, not the app): RX and TX buffers streamed
together, every TX sample zero, the TX LO powered down and TX attenuation at
−89.75 dB, all three read back every second (nothing could radiate):

| rate, each way | RX delivered | TX delivered |
|---|---|---|
| 6.25 MS/s | 6.27 | 6.58 (includes the initial queue fill) |
| 8 MS/s | 7.42 — short | 8.25 |
| 10 MS/s | 6.34 — short | 9.14 |

So full duplex holds at 6.25 MS/s and not at 8: the two directions share
about 0.8 × the one-way rate.

**Bands** (app, headless, steered automatically): 23cm FM at 16 MS/s, 23cm
AM at 10, 70cm AM at 10 (437 MHz) and 6cm FM at 16 (5665 MHz) all tuned and
streamed. Two signals of unknown origin were visible in the 23cm span, near
1250 and 1260 MHz; nothing from this program transmits.

---

## 11. The LibreSDR's firmware, and why its receive stops at ~16 MS/s (4 October 2026)

Brian asked for a firmware update to lift the 16.2 MS/s ceiling. What was
found, and why no firmware was written (backup and restore steps:
`~/libresdr-backup-2026-10-04/` on shack, `RESTORE.md` there).

**What is on the unit** (ssh root / analog — the default — worked):

| item | value |
|---|---|
| board | device tree model "LibreSDR Rev.5 (Z7020/AD9363)", Zynq 7020, 2 × Cortex-A9 |
| clocks | ARM PLL 1350 MHz → **CPU 675 MHz** (stock); DDR 525 MHz |
| firmware family | **DL1JM's DATV image**: Kuiper-based Debian 12 on the SD card, F5OEO DATV binaries modified for LibreSDR (`/boot/README.txt`), a DATV operation panel (nginx + PHP) |
| boot medium | **SD card**: FAT `mmcblk0p1` (`/boot`: BOOT.BIN, uImage, devicetree.dtb, uEnv.txt, dated Feb 2026) and ext4 root `mmcblk0p2`; QSPI mtd0–3 hold FSBL/u-boot, the u-boot environment (`mode=2r2t`, `refclk_source=internal`) and an unused Pluto-style firmware |
| kernel | 6.1.70-35308-ge2e62cc28c80 |
| iiod / libiio | 0.24-4 (Debian packages), libad9361 0.2 |

**The real bottleneck:** the image starts `/opt/pluto_dvb -m DVBS2 …` from
`/etc/init.d/datv.sh` → `/opt/udpts.sh` at boot; with no transport stream
it reads `/dev/null` and spins at **94 % of one of the two cores**. iiod gets
what is left. Freezing it (SIGSTOP, then SIGCONT — no state changed;
`iio_readdev`, 1 M buffers):

| set rate | pluto_dvb running | pluto_dvb frozen |
|---|---|---|
| 20 MS/s, 30 s | 16.77 | **19.12** |
| 22 MS/s, 30 s | — | 18.58 |
| 20 / 25 / 30.72 MS/s, 6 s | 16.17 / 16.44 / 16.72 | 18.95 / 17.80 / 18.07 |

So ~2.4 MS/s of the shortfall is that idle DATV process; the rest is iiod
0.24 running out of a 675 MHz CPU.

**Candidate firmware (verified, not installed):** F5OEO tezuka_fw v0.3.22
`tezuka-libre-v0.3.22-d99264f.zip` (2 Oct 2026), sha256
7852cf85…bed3f matching GitHub's published digest; its README lists
"LibreSDR/ZynqSDR, 7020" with SD card, GbE and DATV, installs by copying
`sdimg/` onto the SD card's FAT partition, and ships optional FSBLs for
CPU 750/950/1100 MHz with DDR up to 750 MHz; libiio 0.26. hz12opensource's
own release (v0.38, January 2024) is older; its 27.5 MS/s is with the 1100
MHz overclock.

**Why it was not flashed:** installing it replaces BOOT.BIN on the SD card
(FSBL and u-boot). If the new image does not come up on the network there is
no fallback boot — the BootROM loads whatever BOOT.BIN is on the card — and
the only recovery is taking the card out, which the instructions said to
stop for. It would also replace Brian's working DATV image (his operation
panel) until restored. Both are Brian's decisions.

---

## 12. The first real transmission (4 October 2026, authorised by Brian, GM4BRI, at the bench)

**Setup.** TX: the Pluto nano at `usb:3.7.5` (serial UQRUR7HPFYWAMKGY, Tezuka
v0.3.21), with an antenna, driven headless by the installed 0.1.3 engine from a
throwaway config (`allowHardwareTx` only there; Brian's own config was not
touched and stayed off). RX: the LibreSDR at ip:198.51.100.74 in the OpenHamShack ATV
window on Brian's desktop (KDE Wayland), receive only, AM-VSB at 10 MS/s. The
LibreSDR's RNDIS gadget (1d6b:0672) and the nano's USB network address were not
used; each radio was opened at the address identified by serial/model first.

**Transmission.** 70cm AM-VSB, vision carrier **433.0 MHz** (the guard accepted it;
at the nano's 6.25 MS/s the signal occupies 431.75-436.0 MHz, mono, 3 MHz
video, no sound), test card with GM4BRI in vision, app time-out 2 min. TX LO
433.875 MHz. The red OUTSIDE THE BANDPLAN warning showed; both bursts are in
the transmit log.

| burst | TX gain | estimated output | keyed | result |
|---|---|---|---|---|
| 1 | −24 dB | ≈ −20 dBm (K0SM's +3.5 dBm at 432 MHz, less 24 dB) | 60.1 s | carrier received 6.3 kHz low; no sync lock |
| 2 | −14 dB | ≈ −10 dBm | 60.1 s | carrier ~25 dB over the floor, line structure visible; no lock (antennas ~2 m apart); one false 10 % "lock" second |

Total keyed time 120.2 s. Both unkeyed cleanly; the nano read back TX LO
powered down and −89.75 dB after each. The LibreSDR's AGC sat at 70-72 dB
(RSSI 98.5 dB): a weak input, no overload or clipping.

**Why no picture - two causes found:**

1. **The nano cannot transmit 6.25 MS/s over USB.** The driver counted ~37 late
   pushes a second. Measured afterwards with zero samples, the TX LO powered
   down and −89.75 dB (nothing radiated): 6.25 MS/s → 5.14 MS/s pushed with
   field-sized buffers, 5.33 with 1 M buffers, every push late; 5 MS/s → keeps
   up (1 M buffers: 0 late in 74; 250 k: 4 in 302; 100 k: 16 in 751); 4 MS/s →
   keeps up. So the transmitted picture had gaps ~15 % of the time, which no
   receiver locks to - and receive (6.25 MS/s in full) had hidden this.
2. **The signal was weak** at −20 / −10 dBm with bench antennas 2 m apart: the
   picture sidebands were at or below the LibreSDR's noise floor.

**A spur:** while keyed, a cluster near **439.25 MHz**, ~15 dB below the vision
carrier, absent before keying - where the TX DAC image of the vision carrier
falls (TX LO 433.875 + 6.25 − 0.875 MHz). In the band but outside the intended
occupancy, so the test stopped there, per its abort rule. Not confirmed whether
it is the nano's (a TX image) or a receive-side artefact; it needs a
spectrum analyser or an attenuated cable into a second receiver before more
over-the-air transmission. The AD936x does not filter its TX aliases
(K0SM); a band-pass filter after the radio is advised regardless.

Screenshots of Brian's receive window: `docs/screenshots/hardware-first-tx-433-burst1.png`
(−24 dB) and `-burst2.png` (−14 dB).

### 12a. The repeat (same evening, authorised by Brian: antennas ~0.5 m apart)

Nano at `usb:3.8.5` (it re-enumerated; found again by serial UQRUR7HPFYWAMKGY),
0.1.4 engine, **5 MS/s** (the rate it can transmit over USB), 433.0 MHz AM-VSB,
test card with GM4BRI, gain −24 dB (≈ −20 dBm), throwaway config. Receive: the
LibreSDR in Brian's visible window at **16 MS/s** so the span (427.4–443.4 MHz)
showed outside 430–440 MHz; the window logged the carrier, the strongest line
in 435.8–440 MHz and the strongest outside 430–440 every second, and the
transmit script unkeyed on its own on any abort condition.

Pre-key baseline: floor ≈ −42 dB; outside the band only a −33 dB line at
441.7 MHz (local QRM, per Brian).

**Burst 1 aborted after 3.4 s** (all the keyed time of the repeat). Within the
first second the carrier was at 433.0 MHz −6.5 kHz, 6–7 dB over the floor
(a stronger picture than before: no lock yet in 3 s), and the nano's driver
counted one late buffer (the 5 MS/s fix held). But **a line at 440.42 MHz,
about −20 dB, appeared outside the band** - 12–13 dB over the pre-key level
there and stronger than the vision carrier itself - for exactly the seconds
keyed, gone at unkey. 440.4248 MHz is exactly 5.000 MHz above the LibreSDR's
receive LO (435.4248 MHz): likely a receive-side product of a strong nearby
signal rather than an emission, but not proven, so it was treated as ours and
the test stopped (abort rules: anything outside 430–440 MHz; > 10 dB over
baseline). The automatic spur check that actually fired (−4 dBc) was a false
trigger - a noise peak measured against a carrier only 6 dB up; a dBc check
needs the carrier well clear of the floor first.

To tell the 440.42 MHz line apart: receive at another rate or LO offset (a
receive artefact moves with the LibreSDR's LO; an emission stays put), or
look with a second receiver / spectrum analyser, ideally with the nano into
an attenuator and cable.

### 12b. The LO-shift burst: the 440.42 MHz line is not a fixed emission

One more burst, authorised by Brian: the nano (serial UQRUR7HPFYWAMKGY,
usb:3.8.5), 433.0 MHz AM-VSB at 5 MS/s, −24 dB (≈ −20 dBm), **15.4 s**. All
four radio ports now had antennas. The LibreSDR's receive LO moved 1 MHz, to
436.4248 MHz (window tuned to 434.0, 16 MS/s, span 428.4–444.4 MHz); receive
on RX1, port A_BALANCED, slow AGC at 69–70 dB, RSSI 97 dB before and 94.75
dB while keyed. Fresh pre-key baseline: floor −41.9 dB, 440.42 MHz −38.9,
outside the band −38.9, LO + 5 MHz −41.2 — all noise.

| while keyed | level | verdict |
|---|---|---|
| 440.42 MHz (fixed) | −39 … −41 dB: noise | **no emission there** - the earlier line did not stay at 440.42 |
| LO + 5 MHz (441.42) | −41 … −43 dB: noise | did not reappear tied to the LO either |
| 428.4248 MHz | −32.6 … −33.7 dB, ~6 dB over baseline | exactly LO − 8 MHz, the receiver's Nyquist edge (its first FFT bin): a receiver artefact |
| 433.0 MHz vision carrier | ≈ −32 dB, 9–10 dB over the floor | weak |
| spur region 435.8–440 MHz | noise | the dBc check (now only with the carrier ≥ 20 dB over the floor) never ran |

So the 440.42 MHz line of 12a was not a real emission at a fixed frequency;
everything seen outside 430–440 MHz moved with, or sat on, the receiver's own
LO. The 5 MS/s transmit held, though the nano's driver still counted 28 late
buffers in 15 s with the receive window on the same laptop (shared CPU).

**The weak carrier is the open question.** −20 dBm across 0.5 m should
arrive tens of dB over the floor; it arrived 10 dB over, with the LibreSDR at
its maximum gain (AGC 69–71 of a 73 dB maximum, so manual gain would add at
most 2–4 dB - not the answer). Suspects for next time: antennas not made for
70cm (if they are 2.4/5.8 GHz sticks, a 433 MHz link loses tens of dB), the
nano's real output at 433 MHz (never measured; the −20 dBm estimate is a
Pluto figure), and which physical connector each driver port maps to on these
two boards. A known 70cm antenna, or a measured cable and attenuator path,
would separate them. Screenshot: `docs/screenshots/hardware-tx-433-lo-shift.png`.

### 12c. Roles swapped: the LibreSDR transmits, the nano receives

Authorised by Brian. TX: the LibreSDR (ip:198.51.100.74), 433.0 MHz AM-VSB at
**10 MS/s** (colour and the 6 MHz sound carrier; the guard accepted it,
431.75–439.1 MHz), test card with GM4BRI and a tone, throwaway config; its
idle `pluto_dvb` frozen with SIGSTOP for the test and resumed afterwards
(PID 338, running, configuration untouched). Output estimated from the gain
on the same basis as the nano - K0SM's +3.5 dBm at 432 MHz for an AD9363 Pluto
at 0 dB (the LibreSDR is also an AD9363; its own output is unmeasured). RX:
the nano (usb:3.8.5) at 6.25 MS/s in Brian's visible window, mono; the
window was retuned to 440.0 and 430.0 MHz (spans 437.75–444.0 and
427.75–434.0 MHz) for the out-of-band checks, with pre-key baselines at each.

| burst | gain | keyed | carrier at 433.0 (per 6 kHz bin over the floor) | out of band |
|---|---|---|---|---|
| 1 | −24 dB (≈ −20 dBm) | 30.2 s | −32.6 dB (+26 dB) | above: nothing new (441.6 MHz QRM at baseline, one 1-s blip); below: only the existing 429.32 MHz signal, ≤ 3 dB over its baseline |
| 2 | −24 dB | 60.3 s | −33.5 dB (+25 dB), **+6.4 kHz** | (window on 433.0) |
| 3 | −14 dB (≈ −10 dBm) | 60.4 s | −22.7 dB (+36 dB) | above and below: nothing new at +10 dB power |

No aborts; the LibreSDR read back TX LO powered down and −89.75 dB after
every burst, and transmitted 10 MS/s over Ethernet without falling behind (1
late buffer per burst). **No sync lock** in any burst. The offset (+6.4 kHz
here, −6.3 kHz the other way) is the two radios' references, ~15 ppm apart.

**Why no picture yet:** link budget. 36 dB over a 6 kHz bin is about +9 dB
CNR over the 3 MHz video band - about +4 dB over the whole sample band, which
is how the decoder's tests specify it (they acquire from ~9–12 dB) - and the
spectrum shows line-rate structure within ±300 kHz of the carrier but little
of the picture's sidebands. −10 dBm was the authorised ceiling. With the
antennas 0.5 m apart, −10 dBm should arrive far stronger than this: check
that the antennas are 70cm ones.

**The 440.42 MHz question, settled:** it was not a fixed emission. It did not
recur at 440.42 when the LibreSDR's LO moved (12b), and with the LibreSDR
transmitting and the nano listening, nothing appeared outside 430–440 MHz
in either window at either power. The nano's 439.25 MHz in-band spur of the
first test (12) was likewise never seen again once its rate came down to 5
MS/s and the receive LO moved; it too was most likely a receive artefact.

Screenshots: `docs/screenshots/hardware-swap-libresdr-tx-433.png` (the nano
receiving the LibreSDR at −14 dB), `-above-band.png`, `-below-band.png`.
Totals for the evening, all bursts: 7, 289.9 s keyed.

### 12d. 23cm FM-ATV: the first off-air picture

Authorised by Brian, after the antennas proved to be the LibreSDR's supplied
whips (likely 2.4/5.8 GHz or "wideband" - poor at 70cm). TX: the LibreSDR,
**1255.000 MHz FM-ATV PAL** at 6.25 MS/s - the nano's reduced-deviation profile:
1.5 MHz p-p, 2 MHz video, CCIR 405 pre-emphasis, no sound subcarrier (6 MHz
does not fit 6.25 MS/s), normal polarity - test card with GM4BRI and a tone,
throwaway config, `pluto_dvb` frozen and resumed (PID 338). The guard accepted
1255.000: 1252.25–1257.75 MHz (Carson 5.5 MHz), clear of Galileo E6
(1258.29 MHz up), warning only for the RSGB's 1250–1290 MHz caution. RX: the
nano at 1255.000, the same parameters exactly, in Brian's visible window.
Output estimated as before (K0SM's +3.5 dBm for an AD9363 Pluto at 0 dB).

| burst | gain | keyed | result |
|---|---|---|---|
| 1 | −24 dB (≈ −20 dBm) | 60.4 s | **locked within 5 s**, 100 % of lines, field lock, mono, 25 fps for all 60 s; video SNR 21–24 dB; jitter 61–87 ns; CNR ≈ 19–21 dB over the occupied band |
| 2 | −14 dB (≈ −10 dBm) | 60.3 s | 12 s on Galileo E6 (1258.1–1264.4 MHz) and 12 s below (1245.6–1251.9), then locked at 1255: **SNR 30–31.7 dB, jitter 23–29 ns, CNR ≈ 27 dB** |

(CNR is the occupied band's mean power over the floor, (S+N)/N − 1, from the
window's spectrum; it reads +1.3 dB on noise alone, a bias to subtract.)

Out-of-band checks at −14 dB, against pre-key baselines at each tuning: in
Galileo E6 only a pre-existing line at 1260.04 MHz (−49.9 dB baseline,
−49.7…−51.4 keyed), the floor up ≈ 1 dB; below the channel only a
pre-existing −27.6 dB carrier at 1250.007 MHz, unchanged. Nothing new
anywhere. Caveat: the LibreSDR's first DAC image would be centred at
1261.25 MHz, which was the nano's own DC bin in that view and excluded from
the peak search; a wideband FM image would still have shown across the rest
of the view, and did not. Outside 1240–1325 MHz was never in view (the
images' second order would fall at 1242.5/1267.5 MHz, inside the
allocation). No frequency offset is measured in FM (the decoder clamps DC);
the two references are ~15 ppm apart (12c), ≈ 19 kHz at 1255 MHz.

The 23cm link worked at both powers with the same antennas, consistent with
them suiting 23cm far better than 70cm. Screenshots:
`docs/screenshots/hardware-23cm-fm-first-picture-minus24.png`,
`hardware-23cm-fm-minus14.png`, `hardware-23cm-galileo-check.png`,
`hardware-23cm-below-check.png`. Evening total: 9 bursts, 410.6 s keyed.

### 12e. 23cm the other way: the nano transmits, the LibreSDR receives

Authorised by Brian. TX: the nano (serial UQRUR7HPFYWAMKGY, usb:3.8.5),
1255.000 MHz FM-ATV at its USB transmit rate, **5 MS/s**, with 0.1.5's USB
profile: 1.2 MHz p-p, 1.6 MHz video, CCIR 405, normal polarity, no
subcarrier (none of 5.5/6.0/6.5 MHz fits 5 MS/s), mono; test card with GM4BRI;
throwaway config. Occupies 1252.8–1257.2 MHz (Carson 4.4 MHz); the guard
accepted it with the RSGB caution only. RX: the LibreSDR in Brian's window at
**16 MS/s** with the same parameters (the FM demodulator takes deviation,
video bandwidth and emphasis independently of the rate - proved first in
simulation, 5 MS/s resampled to 16), viewing 1247–1263 MHz. `pluto_dvb` left
running (receive only).

**The guard refused the first two attempts, correctly - a bug.** 0.1.4's USB
profile set a 1.6 MHz video bandwidth below the settings table's 2 MHz
minimum, without validation; in a config those values fell back to the
defaults (5 MHz video, 7 MHz deviation), which do not fit 5 MS/s. With the
limits as they were (deviation ≥ 1 MHz, video ≥ 2 MHz) no FM profile fitted
5 MS/s at all. Fixed in 0.1.5 (limits 0.5 MHz / 1 MHz, profiles validated,
every profile tested to be valid and to fit) and installed before keying.

Pre-key baseline: floor −44 dB; **lines already present at 1260.016 MHz
(−18.5 dB, inside Galileo E6) and 1249.984 MHz (−28 dB)** - exactly where the
nano's 5 MS/s images would fall, and there with nothing keyed (seen by the
nano's receiver too, earlier): 10 MHz multiples, most likely reference-clock
harmonics or local signals. So the image check used the median level across
each image region and across Galileo (a wideband FM image lifts the whole
region; a line already there does not), as well as the peaks.

| burst | gain | keyed | result |
|---|---|---|---|
| 1 | −24 dB (≈ −20 dBm) | 60.6 s | marginal: 8–37 % of lines, field lock on and off, jitter ≈ 300 ns, CNR ≈ 6–9 dB over 4.4 MHz (FM threshold) |
| 2 | −14 dB (≈ −10 dBm) | 60.4 s | mostly locked: 60–94 % of lines, field lock most of the time, **SNR 18–21.6 dB**, **jitter 83–145 ns**, **CNR ≈ 17–18 dB** |

Out of band: nothing raised at either power - the Galileo and image-region
medians and the 1250/1260 MHz lines all at or below baseline (the AGC turned
down 3 and 10 dB). Outside 1240–1325 MHz was not in view (the 5 MS/s images'
second order, 1245/1265 MHz, is inside the allocation). Third burst not used.

**Why the lock flickered even at 18 dB CNR: the nano's transmitter.** Its
driver counted 86 (−24 dB) and 257 (−14 dB) late buffers in 60 s. Each is
an underrun: the DAC's output pauses and resumes late, the transmitted
timeline slips, and the receiver's line phase jumps - a reacquisition.
Measured afterwards with nothing radiated (the installed driver, TX LO
power-up suppressed, −89.75 dB, zero samples, read back every second): the
same path ran 7–33 late in 15 s, and 4, 8 or 16 kernel buffers made no
difference - so the extra lateness when keyed comes from the full engine
around the writer, not from USB or buffering. (Brian's window decoding 16
MS/s at the same time showed 15 fps and 308 skipped frames at 226 % CPU;
that costs frame rate, not lock: the engine's receive blocks are exactly one
frame long, so a dropped block keeps line and field timing - checked in
simulation.)

Next for this direction: a lean transmit writer (the static loop converted to
int16 once, pushed from its own thread), then repeat; receive at 8 MS/s if the
laptop is also showing the window.

**Colour (Brian's request) - proposals only, nothing keyed.** PAL chroma at
4.43 MHz needs ≥ 4.73 MHz of video, which in FM needs ≥ 12.5 MS/s (Carson ≤
11.9 MHz with ≤ 1.9 MHz p-p) and in AM-VSB ≥ 10 MS/s. The nano transmits
~5.2 MS/s of 16-bit IQ over USB (21 MB/s). Honest routes:
1. **8-bit IQ on the nano** (Tezuka's mode: 14 MS/s over USB claimed for
   receive, transmit unconfirmed). 10 MS/s of 8-bit IQ is 20 MB/s - just
   inside what its USB transmit carries - enough for **AM-VSB in colour** at
   10 MS/s (23cm AM-VSB, received by the LibreSDR at 10–16 MS/s). Needs: a
   non-radiating throughput test of Tezuka's 8-bit TX, and 8-bit sample
   support in the driver (both directions). FM colour at 12.5 MS/s 8-bit (25
   MB/s) is over that budget.
2. **A OpenHamShack-only reduced chroma subcarrier** ("colour-under", chroma near
   ~1.5 MHz inside the narrow video band): fits 5 MS/s, but only OpenHamShack ATV
   would decode it - not a standard PAL receiver. Encoder and decoder work.
3. **A wideband transmitter**: the LibreSDR transmits colour today (10 MS/s AM
   or 16 MS/s FM), but then the receiver must be wideband too - the nano
   cannot receive above ~6.25 MS/s - so colour needs a second Ethernet radio
   or this pairing reversed with a different receiver.

**Sound (Brian's request) - not keyed; the app has no subcarrier that fits.**
`fmSub` offers 5.5, 6.0 and 6.5 MHz only. To carry sound at 5 MS/s:
add a low subcarrier choice (e.g. 2.0 MHz) with the video cut to ~1.4 MHz
below it (fm_plan already limits video to sub − 0.6 MHz and checks Carson,
≈ 4.7 MHz here); the FM sound modulator and demodulator are rate-independent
already. About a dozen lines, a test, and a clear label: **a OpenHamShack-only
mode** - standard ATV receivers expect 5.5/6.0/6.5 MHz and would not hear it.

Screenshots: `docs/screenshots/hardware-23cm-nano-tx-minus24.png`,
`hardware-23cm-nano-tx-minus14.png`. Evening total: 11 bursts, 531.6 s keyed.

## 13. Tezuka's 8-bit mode on the nano: receive only (4 October 2026, nothing radiated)

Route 1 above depended on Tezuka's "complex 8-bit mode" working for transmit.
It does not, on this nano's firmware (tezuka-v0.3.21).

**How the mode is selected.** There is no attribute or setting. Tezuka's own
`plutorx` and F5OEO's SoapyPlutoPAPR (`tezuka_format=CS8`) both enable only
`voltage0` of a buffer device and leave `voltage1` disabled. Each sample is
then one 16-bit word holding int8 I in byte 0 and int8 Q in byte 1.
SoapyPlutoPAPR does the same for transmit on `cf-ad9361-dds-core-lpc`.

**How it was tested.** No RF was involved. The AD9361 was put in digital
loopback (`ad9361-phy` debug attribute `loopback 1`), which returns transmit
samples to the receive path inside the chip, before the DAC. In addition:
- the TX LO stayed powered down at −89.75 dB, read back every second over
  `ip:192.168.2.1`;
- loopback was restored to 0 and the sample rate to 5 MS/s afterwards.

A 16-bit control tone came back as expected, at +1.25 MHz with the image
below −260 dBc.

**Transmit: not supported.**
- *Test:* a tone pair was sent with only `voltage0` enabled: 1.25 MHz in
  byte 0 and 2.5 MHz in byte 1.
- *Result:* receive Q was exactly zero. Receive I carried the 2.5 MHz tone at
  amplitude 964 (60 × 16) and the 1.25 MHz tone at about 4 (90 / 16).
- *Reading:* that is the stock ADI unpacker handling one 16-bit channel. The
  word is taken as a single 16-bit I sample, so byte 1 becomes the top bits,
  byte 0 becomes LSBs, and Q is fed zeros.
- *Consequence:* the bitstream has no 8-bit transmit path. A TX "rate" at
  8-bit would only measure two bytes per sample of real-only signal, so it
  was not reported as one. 10 MS/s AM-VSB colour from a USB nano is not
  available this way.

**Receive: works, and it is fast.**
- *Packing:* with only `voltage0` enabled on `cf-ad9361-lpc`, byte 0 carried
  I (1.25 MHz at 100 = 1600 >> 4) and byte 1 carried Q (2.5 MHz at 62 =
  1000 >> 4).
- *Throughput over USB:*

  | Rate requested | Delivered |
  | --- | --- |
  | 10 MS/s | 10.00 MS/s |
  | 12.5 MS/s | 12.50 MS/s |
  | 16 MS/s | 15.0 MS/s (the ceiling, close to Tezuka's 14 MS/s claim) |
  | 20 MS/s | 15.0 MS/s |

  For comparison, 16-bit receive delivers 6.25–7.4 MS/s.
- *Cost:* 8 bits gives about 48 dB of dynamic range. That is ample for an
  ATV signal well above the noise.
- *Consequence:* a USB nano on Tezuka could **receive** PAL colour. That
  means AM-VSB at 10 MS/s or FM at 12.5 MS/s, so the nano can be the
  receiver of a colour pairing with the LibreSDR transmitting (route 3 above,
  with no second Ethernet radio).
- *Status:* implemented (`devices/pluto.py` `_probe_rx8`, profile
  `usb-rx8`), and the engine was run on the nano with nothing keyed. The run
  used a dev copy with a fresh config, autoProfile on, and transmit disabled,
  for 60 s per system, receiving the empty 1255 MHz channel. The TX LO was
  read back every second and stayed down. Steering moved the nano from plain
  USB to the 8-bit profile on its own, opening in 0.7 s.

  | | AM-VSB, 10 MS/s | FM-ATV, 12.5 MS/s |
  | --- | --- | --- |
  | Receive bits | 8 | 8 |
  | Delivered | 10.00 MS/s | 12.49 MS/s |
  | `rxShort` | never | never |
  | Reader drops | 0 | 0 |
  | Decoder fed | 10.03 MS/s | 12.51 MS/s |
  | Decoder-queue drops | 0 | 0 |
  | Errors | none | none |
  | Engine CPU | 74 % of a core | 100 % of a core |

  The decoder searched the noise as it should. In FM it briefly reported
  `locked` on noise but never produced a frame. The off-air colour test
  (LibreSDR transmitting, nano receiving 8-bit) awaits Brian's
  authorisation.

### 13a. The colour test: LibreSDR transmits, the nano receives 8-bit (4 October 2026, authorised by Brian, GM4BRI)

**Set-up**

- **Transmitter:** the LibreSDR (ip:198.51.100.74), running from the dev
  copy with a throwaway config, sending the test card with GM4BRI and a
  1 kHz tone. `pluto_dvb` was frozen for the test and resumed afterwards.
- **Receiver:** the nano (usb:3.8.5, Tezuka, 8-bit receive), receive-only,
  in Brian's window.

**Carrier: 1251.000 MHz rather than 1255.** At 1255 the guard accepts both
signals, but it warns that each overlaps Galileo E6: FM reaches 1260.9 MHz
and AM 1261.1. At 1251 both stay below 1258.29 MHz with no warning except
the RSGB 1250–1290 caution:
- FM occupies 1245.1–1256.9 MHz (Carson 11.8 MHz).
- AM occupies 1249.75–1257.1 MHz.

The TX script also checked the occupied band against Galileo E6 before
keying.

**Gain:** −24 dB (about −20 dBm) and −14 dB (about −10 dBm, the limit).
The estimate uses the same basis as 12c.

**Bursts:** 6 in all, 328 s keyed. No abort came from an emission. The one
abort was a measurement error, described next.

**FM burst 1 abort: a measurement artefact.** FM burst 1 stopped at 27.9 s
on "new signal in Galileo E6, −21.5 dB at 1264.061 MHz". That was the
window's own measurement error. Its spectrum metrics max-held the last six
spectrum frames, and up to a second of samples taken before a retune is
still queued in the driver, arriving labelled with the new tuning. The
reading was our carrier as seen at the previous tuning, at 1251.061 MHz
(+61 kHz, −20 dB), relabelled as 1264.061 MHz in the first record after the
retune. The next record, and every Galileo-view record in the five bursts
that followed, matched the baseline. The window now skips two seconds of
metrics after a retune and keeps only frames taken at the current tuning.

**Per burst.** All ran 60 s except FM 1. In the table:
- *SNR* and *jitter* are the decoder's figures while locked.
- *Peak/floor* is the strongest bin of the signal against the noise floor
  in the same spectrum.
- *Sound* is the tone measured by FFT with a ±10 Hz notch, over 100 Hz to
  15 kHz.
- *Colour* is the share of locked one-second records with colour lock.

| Burst | Gain | Lock | SNR | Jitter | Colour | Sound: tone, SINAD | Peak/floor | Offset | Galileo view (baseline) | Below 1240 (baseline) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FM 1 | −24 dB | locked from 1.6 s, h 0.94–0.97 | 11–12.6 dB | 210–240 ns | ~55 %, picture looks mono | 1000 Hz, 33–37 dB | ~35 dB | — | (aborted on the artefact) | — |
| FM 2 | −24 dB | locked from 2.4 s, h 0.96 | 11–12.6 dB | 220–240 ns | ~40 % | 1000 Hz, 33–37 dB | ~35 dB | — | −37.5 (−37.2) dB | −49.6 (−49.1) dB |
| FM 3 | −14 dB | locked, h 0.99–1.0 | 19–20 dB | 70–90 ns | 54 %, picture looks mono | 1000 Hz, 43–44 dB | ~41 dB | — | −37.3 (−37.2) dB | −49.5 (−49.1) dB |
| AM 1 | −24 dB | locked, h 0.85–0.93; searching 21–27 s | 10.5–14 dB | 205–228 ns | while locked; **colour picture** | 1000 Hz, 46–47 dB | ~40 dB | +18.75 kHz | −38.0 (−37.9) dB | −53.0 (−52.2) dB |
| AM 2 | −14 dB | locked 3–18 s, then searching until a retune | 18–20 dB | 53–87 ns | while locked; colour | 1000 Hz, 54 dB | ~48 dB | +18.75 kHz | −38.0 (−37.9) dB | −53.0 (−52.2) dB |
| AM 3 | −14 dB, home view only | locked in stretches; searching 19–50 s | 16–19.6 dB | 60–87 ns | while locked; **colour picture** | 1000 Hz, 54 dB | ~47 dB | +18.75 kHz | — | — |

**Out of band.** Nothing new appeared anywhere:
- In the Galileo view and below 1240 MHz, nothing rose above the baseline.
- No line in view rose above its baseline, so the dBc check found no new
  spur. The check applied throughout, because the carrier was 35–48 dB over
  the floor.
- The strongest line beside the AM signal, −50 dB at 1249.74 MHz (about
  −36 dBc), is there with nothing keyed.

**Screenshots:**
- `hardware-23cm-am-colour-nano8-minus14.png`: AM in colour, PAL, SNR 18.5 dB.
- `hardware-23cm-am-colour-nano8-minus24.png`: AM in colour at −24 dB.
- `hardware-23cm-fm-colour-nano8-minus14.png`: FM, which shows "PAL" but
  the picture is almost mono.
- `hardware-23cm-am-galileo-check-nano8.png`: the Galileo view.

**IQ captures (Brian's request).** Two raw captures of 10 s each, taken in
the middle of AM bursts 1 and 2: cs8 (SigMF `ci8`), 10 MS/s, LO 1253.4248
MHz, with `.sigmf-meta` sidecars that carry the TX parameters. They are in
`~/openhamshack-atv-captures/2026-10-04/` on shack. No FM capture was possible:
all three FM bursts were used before the request.

**What the captures show: receive samples are being dropped.** The line
and frame phase of the received signal was tracked block by block (40 ms).
- **Clock offset:** the phase drifts by −6 samples per block, a 15 ppm
  sample-clock difference between the two radios. The decoder copes with
  that.
- **Jumps:** about every 0.5–0.8 s the content jumps forward by exactly
  **1,048,576 samples**, one receive buffer, 13 times in 10 s. That is a
  dropped receive buffer.
- **Not stale data:** no buffer repeats an earlier one byte for byte, at
  any lag from 1 to 8 buffers.
- **Not the transmitter:** a non-radiating run of the LibreSDR's transmit
  path pushed 10.08 MS/s at 10 MS/s, and 12.57 at 12.5, with no late
  buffers.
- **Not the USB link:** the reader thread saw the full 10.00 MS/s.

So the samples are lost inside the program. The driver's receive queue
holds one second; when the engine's receive thread falls further behind
than that, the oldest buffer is discarded (`rx_dropped`). This status was
not shown anywhere, which is why `rxShort` stayed False. On the laptop the
window and decoder took 230–240 % CPU, so the receive thread fell behind.
The headless engine, at 74 % CPU, dropped nothing (section 13).

Each drop:
- breaks the picture and the PAL sequence, which is why colour lock flickers;
- puts a click in the sound, which is why SINAD reads 54 dB in seconds
  without a drop and about 21 dB in seconds with one.

**Two separate faults, both to fix:**
1. The receive path cannot sustain 10–12.5 MS/s with the window open on
   this laptop, and the drops are invisible.
2. After such a jump the AM decoder can stay "searching" for tens of
   seconds. Decoded offline at full speed, capture AM 2 lost lock at the
   jump at 6.2 s and did not regain it in the remaining 3.8 s.

**FM colour.** There is no capture. The same drops apply (SINAD dips,
colour lock 54 %), but the picture also looks mono while "PAL" is shown.
Through the simulator, the same profile (1.2 MHz p-p, 4.9 MHz video,
CCIR 405, 5.5 MHz subcarrier) decodes colour at SNR 27 with a colour PSNR
of 22.8 dB. At SNR 17.5 it gives 17.3 dB, noisy but coloured. So narrow
deviation alone does not remove the chroma: at SNR 20 it should be visible
but noisy. Telling apart weak chroma (FM's triangular noise is greatest at
4.43 MHz), 8-bit quantisation, and the burst gate under the drops needs
an FM capture. One more FM burst would have to be authorised to get it.

## 14. The narrow formats on air: the nano transmits Mini-MAC and Low-PAL, the LibreSDR receives (4 October 2026, authorised by Brian, GM4BRI)

**Set-up**

- **Transmitter:** the nano (serial UQRUR7HPFYWAMKGY), FM at its USB rate
  of 5 MS/s: 1.2 MHz p-p, 1.6 MHz video, CCIR 405 (Mini-MAC runs flat, as
  its format specifies). Test card with GM4BRI and a 1 kHz tone, throwaway
  config, from the dev copy after the merge of `feature/narrow`.
- **Carrier: 1253.000 MHz.** It is in the 1251–1255 MHz range Brian set.
  The guard accepted it with only the RSGB 1250–1290 caution. The occupied
  band, 1250.755–1255.245 MHz, is 3 MHz clear of Galileo E6, which the TX
  script also checked.
- **Receiver:** the LibreSDR at 16 MS/s in Brian's window, with
  auto-detect on and starting on PAL. Each burst was recorded with the
  app's own Record IQ: 10 s from 8 s in, `ci16_le` at 16 MS/s, in
  `~/openhamshack-atv-captures/2026-10-04/narrow/`.

**Bursts:** 3 in all, 181 s keyed, with no abort.

The OOB tours stepped through three views, each against its baseline:
- home (1245–1261 MHz, which takes in the bottom of Galileo E6);
- Galileo (1258–1274 MHz);
- below the band (1232–1248 MHz, which takes in below 1240).

| Burst | Format | Gain | Detected | Lock | SNR | Jitter | Colour (sat, hue vs the card) | Sound SINAD (1 kHz) | OOB vs baseline |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Mini-MAC | −24 dB (≈ −20 dBm) | MAC at 3 s | h ≈ 0.7, field lock | ~3 dB | 115–155 ns | on; sat 0.2–0.45, hue −1° | 1–5 dB (data errors) | Galileo −21.8 (−19.3 base), below 1240 −39.7 (−39.7) |
| 2 | Mini-MAC | −14 dB (≈ −10 dBm) | MAC at 3 s | **h 0.99–1.0** | **23–25 dB** | **21–25 ns** | **on throughout; sat 0.85, hue +0.2°** | 8–29 dB, median ~17 | Galileo view −22.0 (−19.9), below −42.0 (−39.7) |
| 3 | Low-PAL | −14 dB | Low-PAL at 3 s | h 0.96–0.97 | 13–20 dB | 150–190 ns | on 72 % of records; **sat 0.94, hue +2.7°** | 15–25 dB, median ~22 | Galileo view −21.5 (−19.9), below −42.2 (−39.7) |

*Sat* and *hue* compare the decoded picture's colour with the test card
over its inner 84 %: the complex gain on U+jV, so 1.0 and 0° are perfect.
Mini-MAC's lower jitter is by design: its sync is data.

**Low-PAL checks** (from the burst 3 capture; 16k-point FFT, 1 kHz bins, 10 s):
- **The subcarrier** is a line at **+2.217 MHz**. That is 2.2 MHz plus
  the tone's deviation, inside the planned channel edge of 2.245 MHz, and
  8 dB over the floor where its surroundings are 2 dB. The Mini-MAC
  capture shows none there.
- **Power outside the channel** (beyond ±2.245 MHz) did not scale with
  transmit power:
  - The excess over the floor near the nano's 5 MS/s images (±5 MHz) was
    2.9 dB at −24 dB and 3.4 dB at −14 dB.
  - Over the same step, the in-channel density rose 9.5 dB to 18.9 dB
    over the floor.
  - So at most about −25 dBc (density) of it can be the transmitter. The
    rest is the receiver's own response and the lines already present at
    1249.98 and 1260.02 MHz.
  - Mini-MAC measured the same.

**What else showed**
- **The frame-skip catch-up worked on air.** The window at 16 MS/s fell
  behind 7 times in the Low-PAL burst (1.2 s skipped). Lock held at
  h 0.97 throughout, and the plan card said so. Before `ae92ed7`, each of
  those would have been a timing jump. The decoder itself ran 12–16 fps
  of 25 at 16 MS/s, with `decodeSkipped` climbing.
- **Playback works.** Both captures replay through the app
  (`python3 -m ohsatv --play …`):
  - Mini-MAC: SNR 25 dB, colour.
  - Low-PAL: SNR 16–19 dB, colour, once its metadata names the format.
- **Weakness: the first Low-PAL playback was mono.** The recorder had
  written the transmit-side choice ("pal"). PAL's decoder locks to
  Low-PAL's syncs, so auto-detect, which looks only when nothing is
  decoded, never switched. Fixed: the recorder now writes the format being
  decoded. The capture sidecars were corrected, with a `openhamshack:note`.
  Auto-detect's blind spot remains for a live signal that starts while PAL
  is already locked.
- **Plan card shows the wrong format:** it still described the PAL receive
  plan ("too narrow for colour: mono") while Mini-MAC was being received.
  It should follow `rxFormat`.

**Verdict**

- **Mini-MAC:** the better picture, with the steadiest lock and
  timing of anything the nano has sent: SNR 24 dB, jitter 22 ns, colour
  with hue right and saturation 0.85. It needs the stronger signal; at
  ≈ −20 dBm it held field lock but the picture and digital sound were poor.
  Its sound is its weak point: median SINAD ~17 dB at −14 dB.
- **Low-PAL:** colour that is more faithful (sat 0.94, hue +2.7°) and
  sound that is steadier (SINAD ~22 dB). Timing is noisier (jitter
  ~170 ns) and colour lock is less constant (72 % of records). Its 2.2 MHz
  subcarrier sits inside the channel, as planned, with nothing measurable
  outside it that scales with transmit power.

Neither is a standard; both are OpenHamShack-to-OpenHamShack only.

**Screenshots:**
- `hardware-23cm-minimac-nano-to-libre-minus14.png`
- `hardware-23cm-lowpal-nano-to-libre-minus14.png`
- `hardware-23cm-minimac-nano-to-libre-minus24.png`
- `hardware-23cm-narrow-galileo-check.png`

## 15. FM colour again: the LibreSDR's leftover DATV FIR (5 October 2026, authorised by Brian, GM4BRI)

**The question.** In 13a, FM showed PAL lock but a picture that was nearly
mono. The suspect was the decoder's line-phase saturation fault, fixed
afterwards in f8549eb, and confirming it needed an FM capture. Brian
authorised two bursts of about 30 s, with the same parameters as 13a FM:
- **Transmitter:** the LibreSDR (ip:198.51.100.74), 1251.000 MHz FM-ATV
  PAL, 12.5 MS/s, 1.2 MHz p-p, 4.9 MHz video, CCIR 405, 5.5 MHz sound
  subcarrier (index 0.07), test card with GM4BRI and a 1 kHz tone, gain
  −14 dB (≈ −10 dBm).
- **Receiver:** the nano (usb:3.14.5, Tezuka, 8-bit) in Brian's window.
- **Software:** main 2ced13e at both ends, run from a copy in /tmp on the
  laptop. Nothing was installed.
- **Scripts:** 13a's, with one change. The IQ is captured in the driver's
  reader thread, so it holds every buffer the radio delivered, whatever
  the engine dropped.

**The LibreSDR's state, before and after.** Every IIO attribute of all
four devices was read before keying and written back afterwards: rates
666 kS/s, TX LO 1255.000062 MHz powered down, TX gain −89 / −41 dB, RX LO
2400 MHz, RF bandwidths 382 950 / 18 000 000 Hz, DDS tones 39.4 kHz, FIR
enabled. The read-back differs from the start only in buffer lengths and
AGC/temperature readings. pluto_dvb was not running. The image's
`watchconsoletx.sh` and `libre-ptt.sh` (which drives GPIO 3 from the TX LO
powerdown) ran throughout, as they did on 4 October. The dumps are in
`~/openhamshack-atv-captures/2026-10-05/libre2-*-sysfs.txt`.

| Burst | Keyed | Receiver | Result |
| --- | --- | --- | --- |
| 1 | 30.4 s | 1251 MHz to 24 s, then the Galileo view (1264 MHz) | Locked from 1.4 s, h 1.0, field lock, **SNR 23.2–24.5 dB, jitter 35–45 ns, colour never** (0 of 24 records), sound SINAD 0 (no subcarrier). Galileo view −37.7 dB against a −37.6 baseline: nothing new. 20 s IQ captured. |
| 2 | 30.3 s | **Galileo view throughout (an error)** | Burst 1's retune plan had left the window at 1264 MHz, and burst 2 did not retune it back, so the signal was never in view. The only result is an out-of-band check with the FIR bypassed (below): Galileo −37.7 to −38.3 dB against −37.6, nothing new. Its capture holds no signal and was discarded. |

Total 60.7 s keyed. No abort, and no late transmit buffers.

**What the capture shows (burst 1, `rx5-fm-b1`, 20 s of ci8 at 12.5 MS/s).**
- **No chroma in the received signal at all.** Decoded offline, main's
  decoder found a burst of 0.019 V and 0.1.6's 0.012 V. The real burst is
  0.15 V (the transmitter's own loop decodes to 0.153 V, and to 0.161 V
  with noise added for a decoder SNR of 16 dB, where colour locks in every
  frame). V-switch agreement was 0.02, which is chance. Neither decoder
  claimed colour in any of 99 frames. The decoders are not the fault here.
- **The spectrum stops about 3 MHz from the carrier.** Each figure is the
  power in a band, relative to |f| < 0.5 MHz, with the transmitter's ideal
  signal in brackets:

  | Band | Received | Ideal |
  | --- | --- | --- |
  | 0.5–1.5 MHz | −24.9 dB | (−26.1) |
  | 1.5–3 MHz | −35.0 dB | (−35.9) |
  | 3–4 MHz | −39.9 dB | (−42.7) |
  | 4–4.8 MHz (chroma) | **−40.4 dB** | (−33.3) |
  | 5.3–5.7 MHz (sound) | **−40.4 dB** | (−22.3) |

  −40.4 dB is the noise floor. The sound subcarrier should be 18 dB
  above it, and on 4 October it decoded at 33–44 dB SINAD through the same
  nano at the same rate.
- **The cause is a FIR on the transmitter that was not ours.** The
  LibreSDR reported `FIR Rx: 128,4 Tx: 128,4` with
  `in_out_voltage_filter_fir_en = 1`. pluto_dvb, which Brian had used for
  DATV that morning, loads a root-raised-cosine (`rrcfir.h` is in its
  strings). At 12.5 MS/s the transmit path ran through it, interpolating
  ×4 (TF 50 MHz, TXSAMP 12.5 MHz). The nano reported `FIR Rx: 0,0 Tx: 0,0`
  (RF = RXSAMP = 12.5 MHz). OpenHamShack ATV's driver never loaded a FIR, and
  assumed none was there. An RRC for DVB at two samples a symbol has its
  edge at about (1 ± α) × fs/8. At 12.5 MS/s that falls between roughly
  2.5 and 4 MHz, which is where the received spectrum ends.
- **Burst 2 would have confirmed it.** For burst 2 the FIR was bypassed
  (rate set to 12.5 MS/s, then `in_out_voltage_filter_fir_en` 0; the
  coefficients stayed loaded). The receiver was not tuned to the signal, so
  there is no picture. The Galileo view showed nothing new with the
  unfiltered, full-width signal.

**What this does and does not settle**
- **Today's mono picture** is explained by the transmitter's FIR.
- **13a's "nearly mono" picture is not explained by it.** Sound passed
  then, so no RRC like this one can have been in the path. The FIR state on
  4 October was not recorded. The line-phase fault fixed in f8549eb is
  still the likely cause of 13a, unconfirmed.
- **The LibreSDR receive results in 12e and 14** went through whatever FIR
  the LibreSDR held at the time, and that state was not recorded either.
  Their narrow signals sit well inside ±3 MHz at 16 MS/s, so an RRC like
  this one would not have touched them.

**Fixed in da76a89, merged as be5f6f5, but wrongly; corrected in
af0d757 (see the addendum).** open() sets the rate first, then bypasses an
enabled FIR, says so in the notes and `firBypassed`, and close() turns it
back on after the transmitter is quiet.

**Still owed.** One FM burst with the fix (or the FIR bypassed by hand)
and the receiver on 1251 MHz. That finally answers whether FM colour works
on air and whether f8549eb cured 13a. It needs Brian's authorisation.
Captures, logs, screenshot and state dumps are in
`~/openhamshack-atv-captures/2026-10-05/` on shack.

### 15a. The third burst: FM colour on air (5 October 2026, authorised by Brian, GM4BRI)

**One burst, the same parameters as 15.** The LibreSDR transmitted, the
nano received (usb:3.19.5, after re-enumerating), 1251.000 MHz, FM 12.5 MS/s,
1.2 MHz p-p, −14 dB (≈ −10 dBm), a 30 s limit, IQ recorded. There was no
Galileo retune: the TX script refused to key unless the receive LO read
1251.0, and unkeyed at once if it moved. It never moved. It also refused to
key unless the driver reported `firBypassed`.

**be5f6f5 bypassed nothing on the radio, and so never keyed.** Its driver
looked for `in_out_voltage_filter_fir_en` as a device attribute. libiio
presents that sysfs file as input channel `out`, attribute
`voltage_filter_fir_en`, which is also how pyadi-iio reaches it. The test
fake had been written on the same wrong assumption, so the tests passed.
The script's check refused to key (0 s keyed). af0d757 addresses the
channel attribute, and the fake now has it as the radio does; be5f6f5's
driver fails its tests. Checked on the LibreSDR first, unkeyed: open at
12.5 MS/s read back `voltage_filter_fir_en` 0 with the rate holding, and
close() put it back to 1. The burst ran af0d757 at both ends.

**During the burst** (read on the LibreSDR at 12 s): FIR enable 0,
TF = TXSAMP = 12.5 MHz, TX LO 1250.999998 MHz powered up, −14 dB. After
close() the FIR enable read 1 again (the driver's restore). The full
starting state was then written back. The read-back is identical to the
start of this burst and to the start of the day, except buffer lengths and
AGC/temperature readings. `libre2-start3-sysfs.txt` and
`libre2-restored3-sysfs.txt` hold both dumps.

**Live, in the window (main af0d757):** keyed 30.2 s, no abort, no late
buffers, no receive drops. Locked from 1.8 s: h 1.0, field lock, **colour
lock in every record**, SNR 22.8–24.4 dB, jitter 33–44 ns, the 1 kHz tone
at **SINAD 44.1–44.9 dB**. The screenshot `rx5-b3-mid.png` (in docs/screenshots as
`hardware-23cm-fm-colour-libre-to-nano-minus14.png`) shows the card
in colour and the vectorscope on the bars' vectors.

**Offline, on the same 20 s capture (`rx5-fm-b3`):**

| Decoder | Colour frames | Burst | Burst/ideal | Alt | SNR | Sound SINAD |
| --- | --- | --- | --- | --- | --- | --- |
| main af0d757 | 498 / 499 | 0.112 V | 0.73 | 1.00 | 23.5 dB | 44.5 dB (44.1–44.9) |
| 0.1.6 | 498 / 499 | 0.070 V | 0.46 | 1.00 | 24.5 dB | 44.5 dB (44.1–44.9) |
| Transmitter's own loop, clean | 98 / 98 | 0.153 V | 1 | 1.00 | 92 dB | 89 dB |

- **0.1.6 shows the same signal at 0.63 of main's saturation.** Chroma is
  scaled by the received chroma level, not normalised to the burst, so the
  burst amplitude is the picture's saturation. 0.63 is the factor docs
  give for the line-phase fault at half a sample, fixed in f8549eb. Both
  versions lock colour on this capture, so the fault desaturates rather
  than removing colour. That is a large part of 13a's "nearly mono". The
  rest of 13a (colour lock in 54 % of records) fits the receive drops seen
  in 13 that day. This capture had none.
- **The remaining 0.73 is the channel.** In the received spectrum (below),
  the chroma band is 0–2.3 dB under the ideal and the sound subcarrier
  7–10 dB under it, both near the AD936x analogue filters' corners at half
  the 12.5 MHz RF bandwidth. Sound is unaffected (44.5 dB).

**Spectrum out to ±6 MHz.** Power in a band, relative to |f| < 0.5 MHz,
for each side:

| Band (MHz from carrier) | | 0.5–1.5 | 1.5–3 | 3–4 | 4–4.8 | 4.8–5.3 | 5.3–5.7 | 5.7–6.0 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Burst 3, FIR bypassed | + | −25.5 | −35.0 | −38.5 | −35.6 | −39.7 | −31.8 | −40.2 |
| | − | −24.0 | −34.6 | −38.3 | −33.6 | −39.1 | −29.0 | −40.2 |
| Burst 1, DATV FIR | + | −25.4 | −35.0 | −39.8 | −40.2 | −40.3 | −40.4 | −40.4 |
| | − | −24.4 | −35.0 | −40.2 | −40.6 | −40.6 | −40.6 | −40.5 |
| Ideal (the TX loop) | + | −25.6 | −35.7 | −42.6 | −33.3 | −40.0 | −22.3 | −40.1 |
| | − | −26.6 | −36.1 | −42.8 | −33.3 | −40.7 | −22.3 | −37.7 |

The receiver's floor is about −40 dB on this scale, so the 3–4 MHz
received figures are mostly noise. With the FIR bypassed, the chroma and
sound sidebands are back; with it, they are not.

**Verdict.**
- **FM-ATV colour and sound work on air**, LibreSDR to nano at 12.5 MS/s
  and 1.2 MHz p-p: colour in every frame, SNR ~23.5 dB, SINAD 44.5 dB.
- The mono picture in 15 was the DATV FIR.
- 13a's "nearly mono" was mostly the line-phase fault (f8549eb, now
  confirmed off air: 0.1.6 shows 0.63 of main's saturation on the same
  samples), and partly that day's receive drops.
- 0.1.6, still on the laptop, decodes FM colour desaturated. A build from
  current main fixes that, once Brian asks for one.

Keyed today: burst 1 30.4 s, burst 2 30.3 s, burst 3 30.2 s, so 90.9 s in
all. The refused attempt keyed nothing.

## 16. Mini-MAC again, with the receive fixes (5 October 2026, authorised by Brian, GM4BRI)

**The question.** In 14, Mini-MAC's sound measured a median SINAD of only
~17 dB live, where its own recording decodes at 37–39 dB offline. The
cause found since: the window's decoder fell behind at 16 MS/s and threw
away whole blocks, sound and all. b060d81 keeps decoding timing and sound
when it falls behind and skips building that picture, and c25e943 makes
the receive path ~0.6x as costly. One burst was authorised to confirm it
live, with 14's parameters:
- **Transmitter:** the nano (usb:3.19.5, Tezuka), FM 5 MS/s Mini-MAC,
  1.2 MHz p-p, 1.6 MHz video, 1253.000 MHz, test card with GM4BRI and a
  1 kHz tone, −14 dB (≈ −10 dBm), one burst of at most 30 s.
- **Receiver:** the LibreSDR at 16 MS/s in a window on Brian's desktop,
  auto-detect on, starting on PAL. Record IQ from 9 s in, 10 s.
- **Software:** main c25e943 at both ends, copied to /tmp on the laptop.
  Nothing was installed.
- **Guards in the TX script:** it refused to key unless the radio was the
  nano at 5 MS/s Mini-MAC, the guard accepted, the occupied band
  (1250.755–1255.245 MHz) was inside 1240 MHz and clear of Galileo E6, and
  the receiver's LO read 1253.0 MHz. It would have unkeyed at once if that
  LO moved or anything in Galileo E6 or outside 1240–1325 MHz rose 10 dB
  over the pre-key baseline.

**The LibreSDR's state.** Read before the receive bench that preceded this
(PERF.md, "The test laptop") and written back afterwards with every
attribute either could touch, in an order the driver accepts (FIR on, then
the 666 kS/s rate): rates 666 kS/s, TX LO 1255.000062 MHz powered down, TX
gain −89 / −41 dB, RX LO 2400 MHz, RF bandwidths 18 000 000 / 382 950 Hz,
quadrature tracking on, slow-attack AGC, DDS tones 39.4 kHz at scale 0,
FIR enabled with its 128 taps (`FIR Rx: 128,4 Tx: 128,4`). The read-back
differs from the start only in the AGC's receive gain, RSSI and
temperatures. pluto_dvb was not running; `libre-ptt.sh` and `datv.sh` ran
throughout. The driver's own close() had already turned the FIR back on
after each session, as af0d757 intends. Dumps: `libre2-start4-sysfs.txt`,
`libre2-after-bench4-sysfs.txt`, `libre2-restored4-sysfs.txt`.

**The burst: 30.5 s keyed, no abort.** One late transmit buffer, at
key-up. Auto-detect found Mini-MAC within 1.6 s of keying.

| | 14, burst 2 (0.1.6-era dev copy) | 16 (c25e943) |
| --- | --- | --- |
| Lock | h 0.99–1.0 | **h 1.0, field lock, every record** |
| SNR | 23–25 dB | 28–41 dB, median 30 |
| Jitter | 21–25 ns | 19–21 ns |
| Colour | sat 0.85, hue +0.2° | **sat 0.88–0.89, hue +0.1 to +0.3°** |
| Sound SINAD (1 kHz) | 8–29 dB, median ~17 | **39.1–39.2 dB, every record** |
| Sound delivered | (not measured; gaps audible in the figures) | **27.96 s in 28 s** |
| Receive skips / dropped buffers | 0 / 0 | 0 / 0 |
| Pictures skipped (`decodeSkipped`) | – | 40 in 28 s; the window decoded 23.5 fps |
| CNR over the floor (channel) | – | 20–22 dB |

- **The sound is fixed on air.** 39.2 dB is what its 8-bit samples carry,
  and it matches the 37–39 dB the 4 October recording gives offline. The
  pictures the decoder skipped to keep up cost no sound.
- **The plan card followed the received format**: "MAC / DIGITAL" and
  "Receiving Mini-MAC (colour + digital sound) - found on the air.
  Transmit stays PAL." (screenshot `rxm-mac-b1-mid.png`, in docs/screenshots as
  `hardware-23cm-minimac-sound-nano-to-libre-minus14.png`).
- The SNR is higher than in 14 (median 30 against ~24) and the cause is
  not established: the antennas were not measured or placed the same, and
  14 did not log the channel CNR. The sound and colour figures do not
  depend on it at this level; the sound did not fail for want of signal.
- **Out of band:** when keyed, the AGC backed off ~12 dB (the floor went
  from −45 to −57 dB on the window's scale) and the Galileo E6 view's peak,
  the receiver's own line at 1260.0 MHz, fell with it, from −24.1 to
  −35 dB. Nothing new appeared there; the view (1245–1261 MHz) does not
  reach below 1240 MHz.
- **The LibreSDR window cost 1.52 cores** with the transmit preview and
  IQ recording running: rx 0.38, decode 0.87, window 0.17.

**Seen before keying, not caused by the burst:** with nothing on the air,
the LibreSDR window's lock state read "locked" (field lock, `vLock` true)
in 3 of the 22 pre-key seconds, with `hLock` only 0.12–0.19 and no picture
built. The same happens offline: FM noise at 16 MS/s (narrow parameters)
through `narrow.make_demod` and `VideoDecoder` claims a field anchor in 9
of 250 blocks, with `hLock` 0.04–0.36 and no frames out. The chain test
`test_noise_alone_never_claims_a_picture` uses AM at 10 MS/s, where it does
not happen. So the FIELD pill can flash LOCKED on an empty FM band. Fixed
since (16a), without moving any lock threshold.

Captures and logs (`~/openhamshack-atv-captures/2026-10-05/` on shack):
`rxm-mac-b1.sigmf-*` (10 s, ci16 at 16 MS/s, `rx_format` "mac"),
`rxm-stats.jsonl`, `txmac-b1.log`, `rxm-mac-b1-mid.png`, and the scripts
(`txmac.py`, `rxmac.py`, `restore4.sh`).

Keyed today, with 15's three bursts: 90.9 + 30.5 = **121.4 s**.

### 16a. Field lock on noise, fixed (5 October 2026, simulator and recordings; nothing keyed)

**Why noise got a field.** Noise keeps `VideoDecoder`'s tracker going:
about a quarter of its lines count as found, their strongest "edge"
landing inside the line fit's ±0.6 µs outlier window, and noise through a narrow video filter sits on
one side of the slice for half a line at a time, as broad pulses do. Now
and then the three-line field sync pattern turns up among the "found"
lines, the lines are numbered, and `vLock` is true for that block. On a
real signal the same test runs with most lines found.

**The fix.** A field sync numbers the lines provisionally. Field lock
(`vLock`, frames out) is claimed only while the newest 256 or more lines
tracked have at least half their syncs found; if they do not, the
numbering is forgotten. 0.5 is the decoder's existing hold limit (two
batches under half found and it re-acquires), so any signal that can hold
a field can claim one. Mini-MAC's decoder numbers lines from their data
and never claimed a field on noise; it is unchanged.

**Picking the test, with evidence.** A floor on a single batch does not
work. The first batch after acquisition is 64 lines: on noise its found
fraction averaged 0.25 (sd 0.06) and reached 0.61 in 24 000 batches, and
3 of 689 noise field syncs came in a batch with half or more found —
where a weak real signal's first batch reads 0.5–0.6. A tighter edge
window (fraction within ±0.1 to ±0.3 µs of the fit) separated no better:
weak real signals' jitter is large too. Pooled over 256 lines, noise
averaged 0.16–0.22 (sd 0.04) and never passed 0.34 in 14 000 checks:
0.5 is about seven standard deviations clear.

**False field lock on noise** (white noise, 40 ms blocks as the radio
feeds them, `vLock` read after every block; narrow = 1.2 MHz p-p, 1.6 MHz
video, CCIR 405; wide = 1.5/2.0 MHz at 5–6.25, 1.8/4.9 at 10–12.5, 4/5 +
6 MHz sub at 16, 7/5 + sub at 20). Before: blocks of 250; after: blocks of
4500 (three seeds of 1500, 3 minutes of empty band per cell).

| Blocks claiming a field | 5 | 6.25 | 10 | 12.5 | 16 | 20 MS/s |
| --- | --- | --- | --- | --- | --- | --- |
| FM narrow PAL, before | 0 | 0 | 5 | 2 | 11 | 9 |
| FM wide PAL, before | 0 | 1 | 2 | 0 | 0 | 0 |
| FM narrow colour PAL, before | 0 | 0 | 2 | 4 | 4 | 11 |
| AM PAL, before | 0 | 0 | 0 | 0 | 0 | 0 |
| FM Mini-MAC, before | 0 | 0 | 0 | 0 | 0 | 0 |
| **every row, after** | **0** | **0** | **0** | **0** | **0** | **0** |

No frames came out of noise before or after; the before claims had
`hLock` 0.05–0.30.

**True lock through the chain.** `tools/narrow_eval.py` (thresholds for
lock = H ≥ 0.9 and field sync, picture, colour, sound; FM narrow PAL,
narrow colour PAL, Mini-MAC and AM at 5 and 6.25 MS/s) prints the same
tables before and after, every row. `tests/test_chain.py` and
`tests/test_narrow.py` pass unchanged. A finer sweep, 0–16 dB CNR over the
whole band in 1 dB steps, three seeds, 14 blocks each, `vLock` read
after every block:

| Case | Field held ≥ 90 % of blocks, every seed | Lowest CNR with any field lock |
| --- | --- | --- |
| FM narrow PAL 5 / 6.25 MS/s | 5 / 3 dB, unchanged | 2 / 2 dB, unchanged |
| FM narrow PAL received at 10 / 16 MS/s | 3 / 3 dB, unchanged | 2 dB / **1 → 2 dB** |
| FM PAL 12.5 (1.2 MHz p-p, 4.9 MHz video) | 10 dB, unchanged | 9 dB, unchanged |
| FM PAL 16 (4 MHz p-p) / 20 (7 MHz p-p) | 1 / ≤ 0 dB, unchanged | 1 / ≤ 0 dB, unchanged |
| AM PAL 5 / 6.25 / 10 / 12.5 / 16 / 20 MS/s | 7 / 6 / 8 / 7 / 5 / 5 dB, unchanged | 5 / 5 / 6 / 4 / 2 dB unchanged; 20: **0 → 1 dB** |
| Narrow colour PAL 5 / 6.25 | 6 / 3 dB, unchanged | 4 / 2 dB, unchanged |
| Mini-MAC 5 / 6.25 | ≤ 0 dB, unchanged | ≤ 0 dB, unchanged |

The two "lowest" figures that moved were one block in 33 claiming a field
before (`hLock` 0.37–0.42): noise-like claims on a signal that had gone.
What does change is the band just below the hold limit, where a signal
loses lock every few batches: e.g. AM at 16 MS/s, 4 dB (`hLock` 0.36):
field claimed in 0.39 of blocks, was 0.52, and 12 frames out, was 16;
FM narrow PAL at 5 MS/s, 3 dB (0.34): 0.42, was 0.52, 10 frames, was 12.
Those pictures were numbered from fields with most of their lines' syncs
missing.

**The off-air recordings** (4 and 5 October), played through demodulator
and decoder in 40 ms blocks, as recorded and with white noise added over
the band at 6, 3, 0 and −3 dB below the recording's power:

| Recording | As recorded, before → after | Noise added: what changed |
| --- | --- | --- |
| AM −24 dB, 10 MS/s (`rx8-am-b1`, h 0.91) | field 246/250, 237 frames — the same | +6 dB (h 0): a field claimed in 1 block → 0 |
| AM −14 dB, 10 MS/s (`rx8-am-b2`) | field 246 → 244/250 (first block 0 → 1), 242 frames — the same | at h 0.49 the same (134 blocks, 131 frames); +3 dB (h 0): 1 block → 0 |
| FM 12.5 MS/s (`rx5-fm-b1`, `-b3`) | 499/500, 499 frames — the same | nothing (no lock with noise added, before or after) |
| Narrow colour PAL −14 dB, 16 MS/s | 248/250, 247 frames — the same | h 0.67: the same; h 0.38: field 12 → 7 blocks, frames 9 → 4; drowned: 4 blocks → 0 |
| Mini-MAC −24 / −14 dB (4 Oct), −14 dB (5 Oct) | identical: the Mini-MAC decoder is unchanged (the −24 dB burst holds field lock at h 0.72 in 249 of 250 blocks) | identical |

**Tests.** `test_noise_alone_never_claims_a_picture` now looks after every
block, not only at the end; `test_an_empty_fm_band_at_16_msps_never_claims_a_field`
(test_chain.py) and `NoSignal` in test_narrow.py (narrow colour PAL at 5,
6.25 and 16 MS/s; Mini-MAC at the same rates) are new. The 16 MS/s FM and
narrow colour PAL streams in them claimed a field at blocks 8–12 before
the fix.

## 17. Where FM colour and sound went at 12.5 MS/s: the AD936x's half-band filters (5 October 2026, analysis only, nothing radiated)

**The question.** In 15a the off-air colour burst came in at 0.73 of the
sent one, the chroma band 0–2.3 dB under the ideal and the 5.5 MHz sound
subcarrier 7–10 dB under it. The suspicion was the AD936x's analogue
filters at the edge of the 12.5 MHz `rf_bandwidth`. It is not: they are
about 1 dB of it. The rest is the chip's fixed half-band filters, which
`rf_bandwidth` does not touch.

**What the driver sets.** `rf_bandwidth` = the sample rate, clamped to the
chip (20 MHz on an AD9363 personality, 56 MHz on an AD9361/AD9364; the
LibreSDR reports TX `rf_bandwidth_available` up to 40 MHz), for RX and TX
alike: 12.5 MHz at 12.5 MS/s, wider than the 11.8 MHz Carson width the
plan uses. The Linux driver (ad9361.c) halves it to a baseband bandwidth,
BBBW (6.25 MHz). It puts the receive filter, a third-order Butterworth, at
1.4 × BBBW (8.75 MHz), with the TIA's pole at 2.5 × BBBW. The transmit
Butterworth is at 1.6 × BBBW (10 MHz), with a secondary pole that ADI's
filter designer models at 5 × BBBW. At 5.5 MHz that is −0.77 dB receiving
and −0.25 dB transmitting.

**What it does not set.** No FIR is running: OpenHamShack ATV loads none and
bypasses anyone else's (15). Even so, the samples pass a fixed chain of
half-band filters between the converter and fs. During the burst the
LibreSDR read `BBPLL 800 MHz, DAC 100, T2 50, T1 25, TF 12.5, TXSAMP 12.5`:
HB3, HB2 and HB1 at ×2 each, the driver's nominal rate governor. The
coefficients are ADI's (libad9361-iio's filter designer): RX HB1
[−8 0 42 0 −147 0 619 1013 …]/2048 and TX HB1
[−53 0 313 0 −1155 0 4989 8192 …]/16384. HB1 is a half-band, so it is
6 dB down at fs/2 by construction. ADI's own designs hide that with a
compensating FIR.

| Offset at 12.5 MS/s | 4.43 MHz (0.35 fs) | 5.5 MHz (0.44 fs) | 6.0 MHz (0.48 fs) |
| --- | --- | --- | --- |
| RX half-bands | −0.92 | −3.44 | −5.67 |
| RX analogue (rf_bw 12.5 MHz) | −0.41 | −0.77 | −1.03 |
| TX half-bands + DAC hold | −0.85 | −3.31 | −5.45 |
| TX analogue | −0.12 | −0.25 | −0.36 |
| **Both ends** | **−2.30** | **−7.77** | **−12.5** |

Everything scales with fs (rf_bw = fs), so the same fractions apply at
every rate:
- At 16 MS/s, the 6 MHz subcarrier (0.375 fs) loses 3.1 dB with both ends
  together.
- At 20 MS/s it loses 1.0 dB.
- At 5 MS/s, the top of Mini-MAC (0.44 fs) loses as much as the 5.5 MHz
  subcarrier does here.

**The model checked against the radios.** Two checks that do not use the
FM capture:
- The receivers' own noise floors against the model. The receive chain
  shapes the floor: white noise goes into the decimators, aliasing
  included.
  - The nano at 10 MS/s 8-bit (`rx8-am-b1`, the clean side), model against
    measured: −2.0/−2.2 dB at 3.9 MHz, −3.6/−3.8 at 4.4 and −5.1/−5.4 at
    4.9.
  - The LibreSDR at 16 MS/s 16-bit (`rxm-mac-b1`): −0.7/−1.1 at 5.1,
    −1.8/−2.7 at 6.1 and −5.2/−6.0 at 7.9.
  - That is within 0.3 dB on the nano and 1 dB on the LibreSDR.
- A sample-by-sample simulation of the chain (tests/helpers.py,
  `ad936x_link`) agrees with the model to 0.1 dB up to 0.47 fs. It runs
  HB1 as an interpolator and a decimator, images and aliases included.

The nano's floor at **12.5** MS/s is the exception. It is flat to ±0.4 dB
out to fs/2, about 10 dB above 8-bit quantisation. That noise has not been
through the half-bands, so it must be added after them. Digital gain in
the gain table is a candidate; this is not established. The floor says
nothing about the filters either way. It is also why the 15a table's edge
bands sit on a flat −40 dB.

**The capture against the model.** The table compares the received
spectrum of `rx5-fm-b3` with the ideal one:
- Received: 8 s of the capture, as a 2048-point Welch spectrum.
- Ideal: the same card, 1 kHz tone and parameters, through
  `systems.build_loop`.
- Both are normalised to |f| < 1.5 MHz, with the flat floor subtracted.
- Each figure is a 100 kHz band, used only where the signal is at least
  8 dB over the floor.

| Band centre (MHz) | 4.4 | 4.5 | 5.4 | 5.5 | 5.6 |
| --- | --- | --- | --- | --- | --- |
| Upper side | −4.2 | −4.3 | −10.4 | −10.6 | −9.0 |
| Lower side | −1.4 | −2.7 | −4.7 | −7.4 | −8.7 |
| Model, both ends | −2.2 | −2.5 | −7.0 | −7.8 | −8.6 |

- The mean of the two sides follows the model to within about 1 dB.
- On top of that, the upper side is 2–3 dB lower than the lower side. No
  real filter can do that.
- The carrier offset (+19 kHz, measured from the discriminator's mean) is
  far too small to explain it.
- Frequency-dependent I/Q mismatch at one end is a candidate. This capture
  cannot say which end, and the asymmetry is not corrected.

**TX and RX apart.** The capture alone cannot separate them: it holds
their product, and the receiver's floor at this rate has no shape. The
model was checked on both receivers' floors at other rates. It puts 4.2 dB
of the subcarrier's 7.8 dB on the nano, and 1.3 dB of chroma's 2.3 dB; the
rest is on the LibreSDR. The measured symmetric part is about 1 dB worse
than that. By elimination, that extra is the transmitter's share.

**Options, by the evidence.**
- *Wider `rf_bandwidth`.* 25 MHz would recover 0.85 dB of the 7.8.
  - It does not change aliasing. HB1 sets what folds into the band from
    beyond fs/2, not an analogue corner at 0.7 fs: content 7 MHz out lands
    on 5.5 MHz only 9.8 dB down on the receive side.
  - It would let more of the neighbouring band into the ADC, and pass more
    of the transmitter's images out.
  - **Not changed.**
- *A plan change* (another subcarrier, or steering FM to another rate).
  - The sound was not the problem: SINAD was 44.5 dB in 15a. The colour
    was, and chroma at 4.43 MHz cannot move.
  - 12.5 MS/s is the only colour FM receive rate the USB nano has, so
    there is no other rate to steer to.
  - **Not changed.**
- *Undo the known response.* The response is deterministic and fixed by
  the rate and `rf_bandwidth`, and both checks above support it. **Done**,
  at no cost per sample.

**The fix (ohsatv/devices/ad936x.py and the modems).** The Pluto driver now
describes its own fixed response, RX and TX (`rx_response`, `tx_response`).
It takes this from the rate, `rf_bandwidth` and the driver's
`rx_path_rates` / `tx_path_rates`:
- A radio that does not report its path rates is taken to run the nominal
  ×8 chain.
- If the path rates show a FIR still changing the rate, there is no
  response at all, because the FIR's taps are somebody else's.

Each modem folds the inverse, capped at 6 dB, into a filter it already
runs:
- **FM.** The correction goes into the video filter after the
  discriminator (receive) and before the modulator (transmit).
  - Why this works: chroma and the subcarrier deviate the carrier only
    slightly (index under about 0.1). For such a component, the sidebands
    at ±f come out of a discriminator as the component at f, scaled by the
    response there. So boosting the video at f restores them.
  - Why not filter the IQ: a full-rate filter on the IQ would cost as much
    again as the whole demodulator (measured on shack, under load: 27 ms
    against 25 ms for a 40 ms block).
  - The transmitter also raises the subcarrier's index by what its filters
    take off it.
  - The receiver leaves the subcarrier alone: the sound is FM, so the
    subcarrier's level sets its noise, not its audio.
- **AM-VSB.** AM is linear, so the correction is exact.
  - It goes into the receive filter.
  - In the transmitter it goes into the VSB filter, at each frequency's
    place about the LO, and into the sound carrier's level.

Other behaviour that follows:
- Each radio undoes only its own filters. A Pluto receiving a HackRF (or
  an older OpenHamShack) recovers its own share and no more.
- Recordings stay raw: they hold what the radio delivered. The engine now
  writes `openhamshack:rf_bandwidth` and `openhamshack:rx_path_rates` into them.
- Playback of a recording from an AD936x applies the same receive
  correction. It recognises one by `openhamshack:device`, or by the `core:hw`
  text, which is all the 5 October captures have.
- The decoder now reports the burst amplitude (`stats["burst"]`, in volts;
  0.153 is sent).

**Results.**

| | Burst | / sent | SNR |
| --- | --- | --- | --- |
| `rx5-fm-b3`, decoded as on 5 Oct | 0.112 V | 0.73 | 23.7 dB |
| the same, receiver's correction | 0.131 V | 0.86 | 23.3 dB |
| the same, both ends' correction applied at the receiver | 0.147 V | 0.96 | 23.0 dB |
| simulated link (`ad936x_link`, CNR 30 dB): none / RX / TX / both | | 0.77 / 0.89 / 0.86 / 1.00 | |

- The SNR drops by 0.4–0.7 dB because the decoder measures SNR on flat
  fields, and the boost lifts high-frequency noise along with the signal.
  The in-band SNR of the chroma and the subcarrier does not change.
- The remaining 4 % on air is of the same order as the unexplained
  asymmetry.

**What it costs in emission.**
- The transmitter's HB1 leaves an image of everything at f at f − fs. At
  0.44 fs the image is 9.5 dB under the wanted signal; at 0.35 fs it is
  26 dB under.
- At 12.5 MS/s the subcarrier's sidebands put images at ∓7.0 MHz, about
  −42 dBc in 100 kHz uncorrected. Boosting the subcarrier by 3.6 dB lifts
  them by the same, to about −39 dBc.
- Nothing after the radio removes them. The transmit filter K0SM insists
  on is a band filter, tens of MHz wide at 23cm.
- The images were already there, at this order, before the change.
- No profile transmits at 12.5 MS/s. The USB nano transmits at 5 MS/s and
  the LibreSDR at 16. At 16 MS/s the subcarrier's image is 21.5 dB under
  it and lands 10 MHz out.

**To confirm on air (needs Brian's authorisation).** One burst set up as
in 15a, with IQ recorded:
- **Transmitter:** the LibreSDR, FM at 12.5 MS/s, 1.2 MHz p-p, 4.9 MHz
  video, 5.5 MHz subcarrier, test card and 1 kHz tone, −14 dB,
  1251.000 MHz.
- **Receiver:** the nano, 8-bit at 12.5 MS/s.
- **Software:** this build at both ends.

Measure:
1. The live `burst` stat: **at least 0.14 V** (0.112 V on 5 October;
   0.147 V expected). Also colour lock in every record, SNR 22.5–24 dB and
   SINAD of at least 44 dB.
2. From the IQ, the received spectrum against the ideal, as in the table
   above. The transmitter now pre-corrects, so the side-averaged loss
   should be only the receiver's share:
   - chroma (4.4–4.5 MHz): about 1.3 dB, roughly 1 dB better than 15a;
   - the subcarrier (5.4–5.6 MHz): about 4.2 dB, roughly 3.6 dB better
     than 15a.

   The 2–3 dB upper/lower asymmetry is expected to remain. If it moves
   when the TX/RX pair changes, that shows which end has it.
3. Play that recording back in this build. Without the transmitter's
   share to undo, the burst should read **at least 0.145 V**.
4. The image. Make a second short recording with the nano at **10 MS/s**
   and its LO 4 MHz below the carrier (1247.000 MHz, still in the band).
   - The lower subcarrier sideband is at −1.5 MHz from the LO and its
     image at −3.0 MHz.
   - The upper sideband aliases to −0.5 MHz, clear of both. At 12.5 MS/s
     it would alias exactly onto the image, because the transmitter's
     images and the receiver's aliases share fs.
   - Expect the image about 9.5 dB under the sideband. That ratio is the
     half-band's; both rise by 3.6 dB with the correction.

### 17a. The half-band correction on air (5 October 2026, afternoon, authorised by Brian, GM4BRI)

The confirming bursts that 17 asks for, with 0.1.7 installed on the test
laptop and run at both ends. Nothing else was using either radio: no
client on libre-2's iiod and no pluto_dvb (its DATV scripts idle), nothing
on the laptop holding the nano; checked again just before each key-up.

**As run.** libre-2 (ip:198.51.100.74) transmitting, the nano (usb:3.19.5,
Tezuka, 8-bit receive) receiving in a window on the laptop's desktop.
FM-ATV PAL on 1251.000 MHz at 12.5 MS/s, 1.2 MHz p-p, 4.9 MHz video,
CCIR 405, 5.5 MHz subcarrier (index 0.07), test card with GM4BRI and a
1 kHz tone, −14 dB (about −10 dBm). The driver bypassed libre-2's leftover
DATV FIR on open and turned it back on at close, as in 15.
- **Burst A:** the nano at 12.5 MS/s on 1251.000 MHz, 20 s recorded with
  the app's own Record IQ. Keyed 30.3 s, no abort, no late buffers.
- **Burst B (the image):** the same transmission, the nano at 10 MS/s with
  its LO on 1247.000 MHz, 20 s recorded. Keyed 30.3 s, no abort, one late
  transmit buffer at key-up. For this burst the guard also read the nano's
  own sysfs rate and LO once a second over its USB network (below) and
  would have unkeyed on any difference.
- **Keyed this afternoon:** 60.6 s. Today in all: 121.4 + 60.6 = 182.0 s.

| Measurement (17's list) | Expected | Measured | |
| --- | --- | --- | --- |
| 1. Live `burst` | ≥ 0.14 V (0.112 before) | 0.136–0.140 V, median 0.138 | just short; +1.8 dB on 15a |
| 1. Colour lock | every record | 29 of 29 locked records | yes |
| 1. SNR | 22.5–24 dB | 23.7–25.2, median 24.3 | yes (slightly better) |
| 1. Sound SINAD | ≥ 44 dB | 46.7–47.6, median 46.9 | yes (44.5 in 15a) |
| 2. Chroma 4.4/4.5 MHz, side mean | ≈ −1.3 dB | −2.5 / −3.2 (15a: −2.9 / −3.7) | +0.4–0.5 dB only |
| 2. Subcarrier 5.4/5.5/5.6, side mean | ≈ −4.2 dB | −4.9 / −6.3 / −6.2 (15a: −7.7 / −9.1 / −9.0) | +2.8 dB of 3.6 |
| 3. Playback in 0.1.7 | ≥ 0.145 V | 0.137 V (raw 0.118); 15a's capture 0.131 | short by 0.4 dB |
| 4. Image vs lower sideband | ≈ −9.5 dB | −10.4 dB | within 1 dB |

How each was measured:
- 2 uses 17's method (8 s, 2048-point Welch, the ideal from
  `systems.build_loop` without the transmit correction, normalised to
  |f| < 1.5 MHz, flat floor subtracted, 100 kHz bands). Run on 15a's
  capture it reproduces 17's table to 0.15 dB.
- 3 plays the recording through `make_demod` with `recorded_response`
  and the PAL decoder, 200 blocks of 40 ms: colour in 198, 198 frames,
  SNR 24.2 dB.
- 4 uses a 65536-point spectrum (153 Hz bins) over 8 s. The lower
  sideband is at −1.455 MHz from the LO and the image a narrow line at
  −2.958 MHz, both 45 kHz above nominal (the carrier offset); each is
  integrated over ±80 kHz above a local floor 200–450 kHz either side.
  0.5 MHz either side of the image there is nothing over the floor, so
  the line is the transmitter's image and not aliased video.

**Verdict.**
- The receive half of the correction is confirmed on air. The raw
  capture decodes at 0.118 V and the corrected one at 0.137 V, +1.3 dB,
  which is the model's receive share at 4.43 MHz.
- The transmit half is confirmed in direction but not in size. Chroma
  came up 0.4–0.5 dB of a predicted 1.0, and the subcarrier 2.8 dB of
  3.6. 17 had already found the LibreSDR's transmitter about 1 dB worse
  than its model; the correction undoes the model and no more, so that
  1 dB is still there. The colour is at 0.90 of what was sent (0.73 in
  15a), and SNR and sound are unharmed.
- The image sits where the half-band puts it, −10.4 dB under the
  sideband. That is 0.9 dB under the symmetric figure, which is the right
  direction if the weaker upper sideband (below) is weak before the
  transmitter's half-band.
- **The upper/lower asymmetry is still there**, a little smaller: upper
  minus lower is −2.4 dB at 4.4 MHz and −2.8 at 5.5 (15a: −2.9 / −3.3).
  It is the same pair of radios, so this cannot say which end has it;
  swapping the roles (the nano transmitting, the LibreSDR receiving) would.

**Two things the nano did that the program did not ask for.**
- *8-bit receive did not answer twice.* At the first open of the
  afternoon, and again at one later open, `_probe_rx8` read 16-bit words
  (byte 1 near zero) and the driver fell back to 16-bit receive (5 MS/s
  delivered at a requested 12.5, so pictures would have torn). Opening
  again gave packed 8-bit. By hand the probe answered packed at 12.5, 10
  and 2.33 MS/s once it answered at all. The cause is not known. The
  driver did the right thing (it said so and steered), but a second
  probe would save a reopen.
- *The nano's rate and LO moved under the receiver.* Twice the nano's own
  sysfs read 2.330997 MS/s and RX LO 1254.666998 MHz while the program
  had set 10 MS/s and 1247.000 MHz: once while closed, and once 17 s into
  a receive run, which stalled (`rxRate` frozen, no spectrum). The
  program's own figures (`rx_lo`, the spectrum labels) kept saying 1247,
  because they are what it set, not what the radio holds. Tezuka's
  api_controller can set both through MQTT (cmd/rx/span,
  cmd/rx/frequency), but no client other than its own two was connected,
  nothing was in its log, and with the program closed the values held
  steady for 30 s. What wrote them is not known. Hence the hardware read
  added to the guard for burst B. A receiver that reads back the rate and
  LO it set would catch this in the program itself.

**libre-2 afterwards.** Every attribute either program could touch was
written back from the dump taken before the bench (`restore5.sh`, built
from `libre2-start5-sysfs.txt`) and read back. It matches the start except
for RSSI, temperatures, the AGC's receive gain and the buffer lengths:
666 kS/s, TX LO 1255.000062 MHz powered down, TX gains −89 / −41 dB,
RX LO 2400 MHz, FIR on with `FIR Rx: 128,4 Tx: 128,4`. `datv.sh`,
`udpts.sh`, `rtmppluto.sh` and `libre-ptt.sh` are still running.

Files in `~/openhamshack-atv-captures/2026-10-05/`:
- the two recordings, `ohsatv-20261005-123843Z-fm-1251.000MHz` and
  `ohsatv-20261005-124449Z-fm-1247.000MHz`;
- `tx17-a.log`, `tx17-b.log` and `rx17-stats.jsonl`;
- the scripts `rx17.py`, `tx17.py`, `nanohw.sh`, `ana17.py` and `img17.py`;
- `libre2-start5`, `-after5` and `-restored5-sysfs.txt`, with `restore5.sh`.

### 17b. Roles swapped: the nano transmits, libre-2 receives (5 October 2026, afternoon, authorised by Brian, GM4BRI)

To place 17a's upper/lower imbalance: does it follow a radio's role, or
the path between them? And, if measurable, to check the nano's own
transmit correction. Nothing else was using either radio: before keying,
libre-2's iiod had only our own receive window's two connections, there was
no pluto_dvb, and nothing on the laptop held the nano.

**As run.** 0.1.7 installed, at both ends, from the laptop.
- **Transmit:** the nano (usb:3.19.5). FM-ATV PAL on 1251.000 MHz at
  **5 MS/s**, 1.2 MHz p-p, 1.6 MHz video, CCIR 405, **mono, no sound
  subcarrier**. That is the USB profile: 0.1.7 has no 8-bit transmit, so the
  engine refuses to key a USB Pluto above 5.2 MS/s, and at 5 MS/s neither
  colour nor a subcarrier fits. Test card with GM4BRI, −14 dB (about
  −10 dBm).
- **Receive:** libre-2 (ip:198.51.100.74) in a window on the laptop's
  desktop at 12.5 MS/s on 1251.000. Its leftover DATV FIR was bypassed as
  in 15, and IQ was recorded: 10 s with nothing keyed, then 20 s of the
  burst.
- **Guard:** the nano's own sysfs (TX rate, TX LO, TX LO powerdown) was
  read once a second over its USB network. The burst would have unkeyed on
  any change, on a move of the receiver's LO, or on anything more than
  2.7 MHz from the carrier rising 10 dB over the pre-key level. None of
  these happened.
- **Keyed: 30.4 s**, no abort, one late transmit buffer at key-up. Today in
  all: 182.0 + 30.4 = **212.4 s**. One burst; the second allowed was not
  needed.

**Live.** All 29 locked records had line and field lock: SNR 18.7–21.3 dB
(median 20.0), jitter 80–142 ns (median 105; 17a's 12.5 MS/s signal gave
33–44). No colour, as planned for mono. No receive skips or drops. Nothing
more than 2.7 MHz out rose above −51 dB, against a floor of −58 to −61.
- **An external line:** with nothing keyed, a steady carrier at
  **1249.987 MHz** stood ~20 dB above libre-2's floor. It sits at the −1.0
  MHz anomaly in 17a's nano capture as well, so it is a real signal, not
  either receiver's. Every comparison below notches it.

**The imbalance.**
- **Method:** received/ideal in 50 kHz pairs at matched RF offsets, upper
  minus lower, with 5 kHz Welch bins at both rates. The ideal is
  `build_loop` at the transmitter's rate. Each capture's carrier offset
  (−18.9 kHz here, +18.7 kHz in 17a) and flat floor are removed. The
  result is the mean of four independent 4 s segments.
- **Range:** the narrow 5 MS/s signal stands at least 5 dB over the floor
  only out to 0.9 MHz; at 1.5 MHz it is under it. So the comparison runs
  from 0.15 to 0.90 MHz, against 17a's capture treated the same way.

| U − L vs ideal (dB) | 0.3 MHz | 0.5 | 0.7 | 0.8 | 0.9 | fit over 0.15–0.90 MHz |
| --- | --- | --- | --- | --- | --- | --- |
| 17a, libre-2 TX → nano RX | −0.33 ± 0.08 | −0.16 ± 0.04 | −0.26 ± 0.08 | −0.40 ± 0.05 | −0.31 ± 0.09 | −0.47 ± 0.04 dB/MHz through zero (χ²/dof 2.9) |
| 17b, nano TX → libre-2 RX | +0.03 ± 0.08 | +0.16 ± 0.05 | +0.03 ± 0.04 | −0.06 ± 0.03 | −0.33 ± 0.08 | +0.44 − 0.78 ± 0.18 F (χ²/dof 11) |

(± is the standard error of the four-segment mean.)

- **17a is a clean tilt.** U − L = −0.47 dB per MHz of offset, through zero
  and straight. Extended to 4.4 and 5.5 MHz it gives −2.1 and −2.6 dB:
  17a's measured −2.4 and −2.8. So the "2–3 dB asymmetry" of 17 and 17a
  is one linear slope across the channel, about −0.24 dB/MHz in |H|, with
  the upper side weaker.
- **The swap tilts the same way but less cleanly.** Within 0.6 MHz it is
  flat to slightly positive; from 0.65 to 0.9 MHz it falls to −0.3 dB,
  the same as 17a there. A straight line fits it poorly. The narrow
  signal's few strong bins carry little weight, and its own spectrum is
  asymmetric by ±0.5–2.7 dB band to band, so small leakages, such as an
  IQ image from the nano's transmitter, move it.
- **libre-2's receive chain leans the other way.** With nothing keyed, its
  noise floor is higher on the upper side, rising from about +0.2 dB at
  1.5 MHz to +0.6 at 2, +1.0 at 3 and +1.5 at 5.5 MHz. It does not make the
  upper side weak, and in the swap it would partly hide a tilt.

**Verdict, with its limits.**
- **Not one radio's transmitter or receiver.** Swapping the roles changes
  every radio's part in the link, and the upper side still comes out weaker
  in the swap, at the same size by 0.9 MHz. That rules out a tilt in the
  LibreSDR's transmit chain alone or the nano's receive chain alone.
- **What is left:** what both directions share. That is the bench path and
  the antennas: four of the same stock type, used at 1.25 GHz where they
  are not resonant and are likely to slope.
- **Not proven.**
  - The swap reaches only 0.9 MHz, because 5 MS/s is all the nano can
    transmit.
  - Its fit is poor, and below 0.6 MHz it does not show 17a's tilt.
  - A conducted test would settle it: transmitter to receiver through a
    cable and attenuator, no antennas. If the tilt goes, it was the
    antennas or the path.
- **Correct the receiver for it? No.** It belongs to the antennas and the
  path, not the radio.

**The nano's transmit correction cannot be measured at this rate.** At
5 MS/s the model's transmit droop, and so the correction, is under
0.1 dB anywhere the signal stands above the floor (0.4 dB at 1.5 MHz,
where it does not). The symmetric part of received/ideal is 0.0 ± 0.1 dB
out to 0.7 MHz, as predicted. Above that it rises 0.4–1.5 dB with the
floor's share. Measuring it would need the nano at 10 MS/s or more, which
0.1.7 does not transmit.

**libre-2 afterwards.** Restored from the dump taken before the window
opened (`restore6.sh`, from `libre2-start6-sysfs.txt`) and read back. It
differs from the start only in xadc voltages, temperatures and RSSI, with
every gain identical:
- 666 kS/s, TX LO 1255.000062 MHz powered down;
- TX gains −89 / −41 dB, RX LO 2400 MHz;
- RF bandwidths 18 MHz / 382950 Hz, DDS 39.4 kHz;
- FIR on, with `FIR Rx: 128,4 Tx: 128,4`.

One line of the script fails as before: the DDS core's
`out_altvoltage_sampling_frequency` can only be read, and it reads the
same. `datv.sh`, `udpts.sh`, `rtmppluto.sh` and `libre-ptt.sh` are still
running, and iiod has no clients. The nano is left with its TX LO powered
down at −89.75 dB.

Files in `~/openhamshack-atv-captures/2026-10-05/`:
- the recordings: `ohsatv-20261005-125614Z-fm-1251.000MHz` (quiet) and
  `-125702Z-` (the burst);
- `tx17b-1.log`, `rx17b.log` and `rx17b-stats.jsonl`;
- the scripts `rx17b.py`, `tx17b.py`, `nanotxhw.sh` and `ana17b*.py`;
- `libre2-start6` and `-restored6-sysfs.txt`, with `restore6.sh`.

### 17c. The read-back and guard on the nano (5 October 2026, receive only, nothing keyed)

Main eed9726 run from a scratch copy on the test laptop against the nano
(usb:3.19.5, Tezuka v0.3.21), hardware transmit off in the config. Nothing
else held the radio. Six 60 s runs headless (FM 12.5 MS/s 8-bit on 1251 MHz,
AM 10 MS/s 8-bit tuned for 1247 MHz, each with and without an induced
drift) and one 60 s run of the window (offscreen, FM 12.5).

- **No false trips at open.** Every read-back held within the 10 Hz
  tolerances (the LOs read 2 Hz under what was set, the rate exactly); no
  open notes. `rx8Checks` was 1 on every open: the 8-bit check passed first
  time, so the retry was not exercised on the real radio.
- **The guard's reads cost 1.3–14 ms a call over usb: (median 2.3 ms at
  10 MS/s, 6.5 ms at 12.5 MS/s; one first-move call 53–98 ms, which includes
  setting back the rate and both RF bandwidths).** About 0.5 % of a second,
  on its own thread; receive CPU unchanged (headless 0.43–0.49 of a core,
  window 0.69). No skips, no drops in any run.
- **A second usb: client cannot touch a nano that is streaming:**
  `iio_attr -u usb:…` fails with "Unable to claim interface … busy". So the
  two moves of 17a did not come from another program on the laptop over
  USB; they came through the nano's own iiod (its USB-network address
  ip:192.168.2.1 or the LAN) or from inside it (Tezuka's api_controller).
- **The drift reproduced through ip:192.168.2.1, while receiving:** the
  sample rate set to 2330997 (the nano gives 2.330996 MS/s) at 20 s, the RX
  LO to 1254.666998 MHz at 40 s. The guard caught each within a second, set
  it back, and it held; the toast and the plan card said "moved from 12.5
  MS/s to 2.330996 MS/s by itself; set back, it held"
  (`~/openhamshack-atv-captures/2026-10-05/guard17c/winguard-moved.png`).
  **Receive recovered on its own**: the delivered rate dipped to 9.8 of 10
  (12.2 of 12.5) MS/s for the three seconds the rate meter averages over,
  then read the full rate again, with no skips, no drops and no reopen. So
  setting the rate back is enough for this case; 17a's stall, which did need
  a reopen, came with something this test did not do (whatever else the
  api_controller changes with a "span").
- The nano was left as found: 5 MS/s, RF bandwidths 5 MHz, slow-attack AGC,
  both LOs 1250.999998 MHz, TX LO powered down, −89.75 dB, no FIR.

Scripts and logs: `~/openhamshack-atv-captures/2026-10-05/guard17c/`.

## 18. 8-bit transmit on the nano: what Tezuka has, and what USB carries (5 October 2026, nothing radiated)

The question: could the USB nano send 10 MS/s - AM-VSB in colour with
sound - by transmitting 8-bit samples, as it already receives them (13)?
Section 13 found the nano's v0.3.21 bitstream does not unpack 8-bit
transmit. This section looks in Tezuka's sources for a way it might, builds
the driver to use one if a radio ever offers it, and measures what the USB
link would carry.

**How Tezuka's 8-bit mode works, from its sources.**
- *No attribute.* The mode is chosen by the channel mask alone, as 13
  found. tezuka_fw's README says only "Complex 8-bit mode unlocks stable
  streaming up to 14 MHz over USB and 45 MHz over GbE", without a direction.
- *Receive only, in the HDL.* The packer is `cs12_8mux`
  (F5OEO/maia-sdr, branch `refactor`, `maia-hdl/projects/common/cs12_cs8mux.v`;
  wired by `cs12_cs8.tcl` between the ADC FIFO and `util_ad9361_adc_pack`).
  Per receive channel pair, the enables pick the format: both on, 16-bit
  as ADI ships it; I alone, int8 I and Q packed in one word; Q alone,
  12-bit "CS12" packing with a sync frame. The int8 path rounds, saturates
  at +127/−128, and carries a first-order error feedback (each sample's
  rounding error is added to the next), so receive's quantisation noise is
  shaped: lower near the centre of the band, higher towards ±fs/2. Not
  measured here; it matters for 8-bit receive's noise floor across the
  channel, and for nothing below.
- *The transmit side is ADI's.* `system_bd.tcl` sets no transmit options
  for the nano (no `dvb`, `txfir` or `dac_dds`), so its DAC path is
  `xilinx_ad9361.tcl`'s stock DMA → `util_ad9361_dac_upack` → FIFO →
  `axi_ad9361`. No board's design has an 8-bit unpacker: `dvb.tcl` (libre,
  e200, e310, fishball7020, signalsdrpro, plutoskyr2) routes the DMA
  through a DVB-S2 encoder and RRC filter, and `txfir.tcl` adds an
  interpolating FIR chosen by a DAC GPIO bit. tezuka_fw's
  `doc/fpga_usage.md` calls `cs12_8mux` part of the "DVB-S2 TX path"; the
  Tcl connects it to the ADC, and that label is wrong.
- *The host side exists.* F5OEO's SoapyPlutoPAPR, for Tezuka formats,
  disables transmit channel 1 ("Tezuka TX CS8 output") and writes int8 I/Q
  (CS16 >> 8). That is the receive mechanism mirrored, and it is what the
  driver now sends. On the nano, the bitstream then treats each word as one
  16-bit I sample.
- *The on-device tools* do not help. `/root/plutotx` is a DDS tone and sweep
  generator (`ddsgenerator`), not a streamer. v0.3.22 (2 October) changes
  nothing here; its notes are about a kernel corruption fix, a Libre NCO and
  packaging.

**The driver (c340f3e).** The setting is `plutoTx8` (on by default).
- A Tezuka radio on usb: is checked at open (`_probe_tx8`). It reads back
  the TX LO powered down and −89.75 dB first; without both there is no
  check. It then sets `loopback` 1, sends a complex tone at +fs/8 (100 of
  127) packed int8 on voltage0 in a cyclic buffer, and reads it back 16-bit
  on both receive channels. It is a yes only if the tone carries over half
  of what comes back and stands 20 dB over its mirror at −fs/8. The stock
  unpacker makes a real signal (tone and mirror equal: "real"); a
  byte-swapped unpacker puts it in the mirror ("mirrored"). Loopback is put
  back and read back, and a radio left in loopback is refused.
- **On the nano at every open (42 opens): "real".** The whole of open(),
  check included, took 0.12–0.25 s. The note reads: "Tezuka firmware
  (tezuka-v0.3.21), but 8-bit transmit came back through the radio's
  digital loopback as a real signal: its bitstream takes each word as one
  16-bit sample, not int8 I and Q: transmitting 16-bit, up to 5.2 MS/s over
  USB".
- If a radio passes, transmit is 8-bit above 5.2 MS/s, through the HackRF's
  own `to_int8` (×127, rounded, clipped to ±127). Quantisation SNR on the
  test-card loops: 44.3 dB for AM-VSB at 10 MS/s (colour and sound, rms
  0.53), 49.3 dB for FM at 12.5 MS/s. Converting a live buffer costs 8.0 ms
  per million samples on the laptop (16-bit: 6.6), 7.6 % of a core at
  10 MS/s; a static card is converted once.

**What USB carries at two bytes a sample.**
Each run opened the radio through the branch's own driver (git archive of
c340f3e, no install), with the LO power-up and any gain other than
−89.75 dB refused in a subclass. The TX LO powerdown and the attenuation
were read back before streaming and every second (they never moved), and
the DAC core's sticky underflow bit (register 0x88, bit 0) was read and
cleared every second. 8-bit was forced for the bench, since the check said
no: the DAC got each word as one real sample, but the link carried exactly
what 8-bit transmit would, 2 bytes a sample. The source was a 0.16 s
test-card loop through `write_loop`, the engine's static-card path, with
1 M-sample buffers and the receive buffer open as the engine leaves it.

Two regimes appeared, with nothing on the laptop or the nano to tell them
apart. Receive was unaffected in both: 14.92 MS/s at 8 bits and 7.42 at
16, 29.8 MB/s, as in 13.

| Rate, bits | Normal link (14:41–14:46): delivered, late pushes | Slow link (15:09–15:17): delivered, late pushes, DAC underflow seconds |
| --- | --- | --- |
| 5 MS/s, 16-bit (the USB profile) | 5.07–5.10, 0 of 103–153 | 4.35, 87 of 89, 20 of 20 |
| 6.25 MS/s, 16-bit | 5.50, 157 of 159 | 4.34, 124 of 126, 30 of 30 |
| 6.25 MS/s, 8-bit | 6.29, 0 of 362 | 6.29, 0 of 361, 1 of 60 |
| 8 MS/s, 8-bit | 8.04, 0 of 461 | 8.04, 0 of 461, 1 of 60 |
| **10 MS/s, 8-bit** | **10.09, 2 of 194** (both in the first second; 20 s run) | 8.57, 490 of 492, 60 of 60 |
| 12.5 MS/s, 8-bit | 10.77, 205 of 207 (20 s) | 9.08, 519 of 521, 60 of 60 |

The single underflow second at 6.25 and 8 MS/s is the start, before the
first buffer lands. At 10 MS/s in the slow regime the buffer size made no
difference: 8.26 MS/s with 256 k buffers, 8.57 with 1 M, 8.69 with 2 M.
Dropping the receive buffer and the register reads made none either (8.58).

- **In bytes it is one link.** The normal regime carries ~21.5 MB/s either
  way: 16-bit tops out at 5.5 MS/s, 8-bit at 10.8. The slow one carries
  ~17.4 MB/s: 4.35 and 8.6. 8-bit 10 MS/s is 20 MB/s, exactly what the
  5 MS/s USB profile already asks for, so the two stand or fall together.
  4 October's 16-bit figures (5.14–5.33 at 6.25 MS/s) and 17b's on-air
  5 MS/s burst (one late buffer, ~13:00) were the normal regime.
- **The slow regime is not the HackRF, the host CPU, or the nano's CPU.**
  It was there at 14:26 (a 5 s smoke run at 10 MS/s, 8-bit: 8.4 MS/s, every
  push late), before the HackRF was plugged in - the kernel log shows it
  arriving at 14:37:31 - and at 15:09–15:25 with the HackRF closed (no
  process held it). A busy-loop on the laptop changed nothing (4.32 vs
  4.36). The nano's two cores were ~36 % busy while saturated (iiod 2–5 %).
  Its USB network link carried nothing. Runs taken while another session's
  HackRF was sending 20 MS/s on the same bus (from 14:35, and two later
  burst sets) are kept - `contaminated/`, the end of `bench2.log`,
  `bench3.log` - and not used above; they read like the slow regime. The
  cause is not found, and it matters as much to today's 5 MS/s 16-bit
  transmit as to 8-bit.
- **Host CPU**: 1.6–2.8 % of a core for the bench process at every rate
  (the static-card loop is converted once; `write_loop` only pushes).

**The proposed profile** (`usb-tx8`, ohsatv/profiles.py), for a radio that
passes the check. It is one rate both ways, 10 MS/s: AM-VSB has 4.8 MHz of
video (colour) and the sound carrier. FM at 10 MS/s carries 9.5 MHz, too
little for colour (4.73 MHz of video leaves no deviation) and the 5.5 MHz
subcarrier is past the 4.8 MHz video limit, so FM is mono at 3 MHz p-p with
3 MHz of video (Carson 9.0 MHz). FM colour needs 12.5 MS/s, which
two-byte transmit does not reach. No radio is steered there today.

**What a confirming on-air burst should be.** None can confirm 8-bit
transmit on this firmware: the bitstream does not unpack it, so a burst
would radiate a real-only signal, mirrored about the LO. A burst is worth
asking for only after `_probe_tx8` says "packed" on a new bitstream; until
then the 5 MS/s USB profile is what the nano transmits.

When one does, the confirming burst should be:
- *Before it, nothing radiated:* `_probe_tx8` reads "packed"; a 60 s bench
  as above at 10 MS/s, 8-bit, gives no late pushes after the first second
  and no DAC underflow, that day (so the link is in its normal regime).
- *Transmit:* the nano on usb:, the engine's `usb-tx8` profile: AM-VSB PAL,
  10 MS/s, 8-bit, test card with GM4BRI and the 1 kHz tone, vision carrier
  on 1251.000 MHz (occupied 1249.75–1257.1 MHz, inside the 23cm ATV
  segment and clear of Galileo E6). −24 dB, below 17b's −14. 30 s keyed,
  one burst.
- *Receive:* libre-2 (ip:198.51.100.74) at 10 MS/s 16-bit on 1251.000, its
  DATV FIR bypassed, IQ recorded 10 s before keying and 20 s during.
- *Guard, once a second:* the nano's TX LO, rate and `txLate`; libre-2's
  LO and the level beyond 7 MHz from the carrier. Unkey on any move, on a
  late push after the first second, or on 10 dB of rise out of band.
- *It confirms 8-bit transmit if:* line and field lock throughout; colour
  (saturation near 13a's, where the LibreSDR sent the same signal); the
  sound carrier decoded; nothing new out of band; and the received spectrum
  matches `build_loop` at 10 MS/s with no mirror of the picture about the
  LO, which is what a real-only (stock-unpacked) signal would show.

Left as found (`restore.py`, then compared attribute by attribute): 5 MS/s,
RF bandwidths 5 MHz, slow-attack AGC, both LOs 1250.999998 MHz, TX LO
powered down, −89.75 dB, loopback 0, no FIR. The DAC core's sticky
underflow bit (0x88) read 1 before and after: the end of every transmit
stream leaves it set.

Scripts and logs: `~/openhamshack-atv-captures/2026-10-05/tx8-18/`.

### 18a. The slow USB transmit: the nano's own USB network (5 October 2026, evening, nothing radiated)

Every run with the TX LO read back powered down and −89.75 dB each second
(it never moved). The nano (usb:3.19.5) sits directly on the laptop's xHCI
root port 3-4 at 480 Mb/s, no hub; the HackRF on 3-1 stayed closed.

**The cause of 18's slow regime is the nano's RNDIS network being up on the
host** (`enx00e022121743`, 192.168.2.10, the gadget's network function, which
shares the one USB link with the IIO samples). Up or down on alternate runs,
two rounds each, no packets on it while measuring:

| | Network up | Network down |
| --- | --- | --- |
| Raw libiio push, 20 MS/s asked, 1 M samples (`txprobe.py`) | 20.0 MB/s, every run | 25.8–25.9 MB/s, every run |
| The driver's write_loop at 6.25 MS/s, 16-bit (`tx8bench.py`) | 4.35 MS/s (17.4 MB/s), 62 late in 15 s | 6.36 MS/s, 0 late |
| The driver at 5 MS/s, 16-bit, 30 s | 4.38 MS/s, 131 late | 5.07 MS/s, 0 late |
| Receive, 8-bit / 16-bit (`rxrate.py`) | 29.8 / 29.6 MB/s | 31.1 / 31.3 MB/s |

- Idle costs the same as busy: the host keeps the network's receive
  transfers queued while the interface is up, and the link spends its time
  on them. Transmit loses about a quarter, receive about 4 %.
- **Not the cause:** a host-side USB reset (`usbreset 0456:b673`) changed
  nothing, up or down, so this is not the "sticky collapse after a session"
  that a power cycle cures. Kernel buffer count (default, 4, 8, 16) and
  buffer size (256 k–4 M samples) changed nothing (19.7–20.1 MB/s up). The
  host is not the limit: the push itself blocks for the link (207 ms per
  4 MB up, 160 down), CPU 2 % of a core.
- 18's "normal" 21.5 MB/s between 14:41 and 14:46 is not explained: the
  journal shows no change to the interface then. Both of 18's regimes are
  below what the link carries with the network down.
- **What to do**, in order of reach:
  - On the laptop, while transmitting: `sudo ip link set enx00e022121743
    down`, and `up` afterwards (the program uses `usb:`, not the network).
    NetworkManager brings it back on its own at the next replug.
  - For good, on the laptop: tell NetworkManager not to manage the nano's
    interface (`unmanaged-devices=mac:00:e0:22:12:17:43` in
    `/etc/NetworkManager/conf.d/`), so it stays down; ssh to the nano then
    needs `ip link set … up` first. Not done - Brian's call.
  - On the nano: Tezuka's `S23udc` reads `fw_printenv usb_ethernet_mode`
    (rndis, the default; ncm; ecm), as Pluto firmware does. NCM is the more
    efficient class, but an up interface still queues receive transfers, so
    it may not cure this; untested. Changing it is a persistent `fw_setenv`
    and a reboot - not done.
  - In the program (branch `fix/usb-tx`, not merged): the driver finds an up
    network interface on the radio's own USB device and says so in a note
    with the command, and in `status()["usbNetUp"]`.
- Also seen on the nano, not chased: Tezuka runs `api_controller.sh`
  (MQTT; a "sweep" that writes `in_voltage_sampling_frequency` and talks to
  maia-httpd's DDC `output_sampling_frequency`), `watchdatveasy.sh` (powers
  the TX LO **up** whenever `ensm_mode` is written `tx`; the program never
  writes it) and `maia-httpd` on port 80. Any of them could be what moved
  the nano's rate and LO in 17a; none was seen to.
- The nano was left as found (attribute diff empty), its network up.
  Scripts and logs: `~/openhamshack-atv-captures/2026-10-05/usbslow/`.

### 18b. The nano's USB network as NCM: no better (5 October 2026, evening, nothing radiated)

Brian chose to try CDC-NCM in place of RNDIS, since NCM is the faster
gadget network elsewhere. Tezuka v0.3.21's `S23udc` reads
`usb_ethernet_mode` (rndis when unset, or ncm, ecm). It was unset;
`fw_setenv usb_ethernet_mode ncm` and a reboot gave `ncm.usb0` on the nano
and `cdc_ncm` on the laptop. The MAC is the same, so the interface keeps
its name (`enx00e022121743`) and NetworkManager's "Wired connection 1";
192.168.2.1 answers over it, `usb:` libiio still finds the nano (now
usb:3.21.5), and iiod, maia-httpd, `api_controller.sh` and
`watchdatveasy.sh` all came back.

**The reboot transmitted.** A Pluto boots with its TX LO powered up at
2450 MHz and −10 dB of attenuation (DDS tones at scale 0, so only LO
leakage). From the nano re-enumerating (16:30:10) to quieting it
(16:30:36) that was about 26 s of LO leakage on its antenna. Any reboot of
a Pluto does this until something powers the TX LO down. 2450.000 MHz is
the top edge of the UK 13cm allocation and inside the 2.4 GHz ISM band.
Nothing in OpenHamShack ATV caused it, but a reboot is now known to radiate,
and a script that reboots a Pluto must quiet it at once afterwards.

Measured exactly as in 18a, the transmitter quiet (TX LO read back
powered down at −89.75 dB every second), 30 s each:

| | NCM up | NCM down | RNDIS up (18a) | RNDIS down (18a) |
| --- | --- | --- | --- | --- |
| Raw libiio push | 20.04 MB/s | 25.79 MB/s | 20.0 | 25.8–25.9 |
| Driver, 5 MS/s 16-bit | 4.39 MS/s, 131 late | 5.07, 0 late | 4.38, 131 late | 5.07, 0 late |
| Driver, 6.25 MS/s 16-bit | 4.35 MS/s, 124 late | 6.31, 4 late | 4.35, 62 late in 15 s | 6.36, 0 late |
| Receive, 8-bit / 16-bit | 30.0 / 29.7 MB/s | 31.0 / 31.2 | 29.8 | 31.1 |

NCM up slows transmit exactly as RNDIS up does: the cost is a network
function being live on the link, whichever class. `usb_net_up()` names the
interface under NCM too (and nothing when it is down). NCM is left set, as
Brian chose; to revert, `fw_setenv usb_ethernet_mode rndis` and reboot
(then quiet the TX at once). The interface still has to be down for a
nano transmit, or NetworkManager told to leave it unmanaged. The nano's
attributes were restored to the 18a starting snapshot (diff empty), TX LO
down at −89.75 dB; its network is up. Records:
`~/openhamshack-atv-captures/2026-10-05/usbslow/ncm/`.

## 19. HackRF transmit (5 October 2026, afternoon, authorised by Brian, GM4BRI)

**What was authorised.** The HackRF One (laptop bus 3, 1d50:6089, serial
…930c64dc295da8c3) into a large dummy load, 1251.000 MHz, FM-ATV PAL to the
HackRF profile (20 MS/s, 7 MHz p-p, 5 MHz video, CCIR 405, 6.0 MHz sound
subcarrier), test card with GM4BRI and a 1 kHz tone, **minimum gain only**
(txGain −47 dB = TX VGA 0, amplifier off), up to three bursts of 30 s;
libre-2 receiving the leakage at 16 MS/s on 1251.000 and recording IQ.

**How it was run.** The installed 0.1.7 on the laptop, two processes,
because the engine runs one sample rate for receive and transmit and
libre-2 holds only ~16 MS/s:
- the transmitter: `txDevice=hackrf` beside the simulator as the receiving
  device (no stations), so the engine ran at 20 MS/s; it asserted from the
  HackRF's own status every 0.2 s that the VGA was 0 and the amplifier off,
  and would unkey on any parameter drift or on anything outside 1240–1325
  MHz in libre-2's view 10 dB over the pre-open baseline;
- the receiver: libre-2 (`ip:198.51.100.74`) at 16 MS/s, FM with the same
  settings, receive only, its leftover DATV FIR bypassed by the driver.
Scripts: `~/openhamshack-atv-captures/2026-10-05/hackrf/` (`txh.py`, `rxh.py`,
`ana1.py`, `comb1.py`, `comb2.py`).

**One burst, 28.1 s keyed, no abort.** The second and third were not run:
the first showed that nothing the HackRF sends at this gain reaches libre-2,
so retuning libre-2 ±10 MHz for the spectrum edges would have measured
nothing.

- **Quiet open.** The HackRF opened at VGA 0, amplifier off, not streaming;
  libre-2's in-band mean read −44.19 dB with it open against −44.1 before
  it opened.
- **Underruns: none.** 0 underruns (0.0 ms) in 28.1 s; delivered rate
  20.42 MS/s in the first second, then 20.0 for the rest (the meter counts
  from tx_start). The 8-bit transmit bench on the nano (another agent's,
  §18) started 6 s after key-up and streamed 16-bit 5 MS/s to the nano on
  the same USB bus for 22 s of the burst; the HackRF still lost nothing.
- **CPU (laptop, i5-1135G7, 8 threads, everything running):** 19 % user +
  3 % system while keyed (≈ 1.8 threads in all: libre-2 receive and decode
  0.42 of a core, the transmitting engine 0.9–1.0 by its own meter,
  including the simulator's receive at 20 MS/s), against 12 + 1 % with the
  receiver alone.
- **Unkey.** `ptt(False)` returned in 1.4 ms; the HackRF then read not
  streaming, VGA 0, amplifier off, still 0 underruns. Whether the carrier
  vanished at once could not be measured from libre-2 (below).
- **Nothing reached libre-2.** Its per-second view showed no change at all
  (in-band mean −44.0 to −44.4 dB keyed, as before). In the 20 s IQ
  recording (15.5 s keyed, 3.4 s after unkey; no lost samples), Welch
  spectra keyed against unkeyed differ by −0.03 to +0.02 dB in every
  1.5–2.5 MHz band across ±8 MHz, per-bin sd 0.06 dB: the leakage is at
  least ~22 dB under libre-2's floor across the channel. A search for the
  FM-TV line-rate comb (lines every 15 625 Hz about the carrier, which
  gathers far more of the signal than band power does) over carrier offsets
  of ±60 kHz found nothing keyed that was not there unkeyed (below).
- **Expected.** VGA 0 with the amplifier off is the HackRF's lowest
  setting: 47 dB of VGA and the 14 dB amplifier under its full output
  (Great Scott Gadgets publish full output of a few to ~15 dBm below
  2.2 GHz, which would put this near −45 dBm into the load - not measured
  here), and a large dummy load radiates little of it: the leakage at
  libre-2's antenna is far
  below the floor of a receiver with a 16 MHz bandwidth. The spectrum,
  decode and unkey-timing checks need a coupled path (an attenuator and a
  cable into libre-2, or a sniffer antenna at the load) or more gain.
- **Two outside signals on the bench.** The steady carrier at 1249.987 MHz
  (17b) was there throughout (−26 dB peak, ~18 dB over the floor), and a
  line-rate comb, lines at +36 400 Hz + k × 15 625 Hz from 1251.000 MHz
  (~10 dB over the bins between them) with a weaker one at +31 575 Hz +
  k × 15 625 Hz, was there keyed and unkeyed alike - something on or near
  the bench radiating at a 15 625 Hz line rate. Neither is ours.

**libre-2 afterwards:** written back from the dump taken before it was
opened and read back. It matches except the XADC supply readings: 666 kS/s,
TX LO 1255.000062 MHz powered down, TX gains −89 / −41 dB, RX LO 2400 MHz,
RF bandwidths 18 MHz / 382 950 Hz, DATV FIR on (`FIR Rx: 128,4 Tx: 128,4`),
`datv.sh`, `libre-ptt.sh` and `watchconsoletx.sh` running, no iiod
clients. (`out_altvoltage_sampling_frequency` on the DDS core is read-only
and reads back the same value; its write fails, as before.) The HackRF is
closed and still plugged in.

**Keyed today:** 212.4 + 28.1 = **240.5 s** (the HackRF's into the dummy
load).

### 19a. The HackRF again, libre-2's antenna at the load as a sniffer (5 October 2026, afternoon, authorised by Brian, GM4BRI)

Brian put libre-2's receive antenna right next to the dummy load and
authorised up to two more bursts on 19's parameters (minimum gain only),
with burst 3 to look at the Carson edges (libre-2 at ±10 MHz) if burst 2
showed the signal, and to stop after burst 2 if it did not.

**Before keying:** nothing on the laptop held the nano or anything else on
USB bus 3 (the 8-bit bench had finished; the transmit script also unkeyed
if `tx8bench` started), libre-2 had no iiod clients and no pluto_dvb, and
its state matched 19's starting dump exactly.

**Changes to the scripts:** the receiver stamps every block it hands the
recorder with the laptop's clock (the clock `ptt(False)` is stamped with),
so a sample's time is its block's arrival less what followed it in the
block. The fit put sample 0 at a fixed wall time with residuals of 2.2 ms
median, 7.7 ms p95, 18 ms worst.

**Burst 2: 25.0 s keyed, no abort.** Recording started 0.42 s after key
and ran 30 s, so `ptt(False)` fell 24.591 s into it. 0 underruns, 20.0
MS/s, VGA 0 and amp off throughout and after, `ptt(False)` 1.9 ms. Laptop
CPU 19–20 % user + 3–4 % system while keyed, 12 + 1 % with the receiver
alone.

**Still nothing from the HackRF at libre-2:**
- Live view unchanged (in-band −44.1 to −44.5 dB keyed, −44.3 before).
- Welch spectra over the whole recording, keyed (23.6 s) against unkeyed
  (5.1 s): every band across ±8 MHz −0.05 to +0.003 dB, per-bin sd 0.06
  dB. Keyed is, if anything, a few hundredths of a dB *lower*, which is
  libre-2's slow AGC, not a signal.
- The comb search: the only keyed-minus-unkeyed peak (+1.2 dB near
  +31.5 kHz) is the edge of the outside comb, below. Chunk by chunk it
  falls steadily, 2.4 → 1.9 → 1.5 → 1.0 dB through the keyed stretch and
  0.6 unkeyed, while its neighbour at +31 500 Hz sits at 2.3–2.8 dB in
  every chunk: the outside comb drifting, not ours.
- So no spectrum, no decode, and no unkey timing: the carrier was never
  above libre-2's floor to vanish. The sample-clock mapping is in place for
  when it is.

**The outside comb has changed.** Burst 1's comb at +36 400 Hz +
k × 15 625 Hz (~10 dB over the bins between its lines) is gone (0.06–0.18
dB). In its place, lines at multiples of 15 625 Hz from 1251.000 MHz
(strongest at +31 250 / +46 875, which are the same comb) at 3.4–4.1 dB,
keyed and unkeyed alike and drifting by a few hundred Hz over the 30 s. A
line-rate source near the bench that moves; not ours. The steady carrier
near 1250 MHz moved too, from 1249.987 to 1250.031 MHz (−28 dB peak).

**Stopped after burst 2, as asked: burst 3 was not run and the gain was
not raised.** At VGA 0 with the amplifier off the HackRF's output into the
load does not reach a receiver antenna beside it at 16 MHz bandwidth. The
owed measurements need a cabled path (the HackRF through an attenuator
into libre-2) or more gain, each needing fresh authorisation.

**libre-2 afterwards:** written back from this test's starting dump and
read back. It matches except the XADC/temperature/RSSI readings: 666 kS/s,
TX LO 1255.000062 MHz powered down, TX gains −89 / −41 dB, RX LO 2400 MHz,
RF bandwidths 18 MHz / 382 950 Hz, DATV FIR on (`FIR Rx: 128,4 Tx:
128,4`), `datv.sh`, `libre-ptt.sh` and `watchconsoletx.sh` running, no
iiod clients. (The DDS core's read-only `out_altvoltage_sampling_frequency`
write fails as before and reads back unchanged.) The HackRF is closed and
still plugged in.

**Keyed today:** 240.5 + 25.0 = **265.5 s**.

Scripts and analysis: `~/openhamshack-atv-captures/2026-10-05/hackrf/`
(`rxh.py`, `txh.py`, `ana2.py`, `ana2b.py`, `ana2c.py`); the recording
`ohsatv-20261005-135114Z-fm-1251.000MHz.sigmf-*` and `hk-feed.jsonl`,
`hk-times-1791208273.json`.

### 19b. The HackRF over the air, small antenna (5 October 2026, afternoon, authorised by Brian, GM4BRI)

**Authorised.** The HackRF with a small antenna in place of the dummy load,
libre-2's antenna nearby: 1251.000 MHz, FM-ATV PAL at 20 MS/s on the
HackRF profile (7 MHz p-p, 5 MHz video, CCIR 405, 6.0 MHz sound), test card
with GM4BRI and a 1 kHz tone; VGA 0 with the amplifier off, stepping to VGA
10 and then 20 only if libre-2 did not see it clearly, never the amplifier
and never above VGA 20; at most 3 bursts of 30 s; one of them at ±10 MHz if
the signal was clear.

**Guards** (`txh.py`): the HackRF's own status read every 0.2 s, unkey on
VGA other than the step's or the amplifier on; unkey on anything in
1262–1299.21 MHz (Galileo E6 beyond our Carson edge) or outside 1240–1325
MHz 10 dB over the pre-open baseline; unkey if `tx8bench` started on the
nano. Before each burst nothing on the laptop held the nano or USB bus 3,
libre-2 had no iiod clients, and its state matched 19's start exactly.

**Bursts** (each 25 s keyed, 30 s recorded from key-up, no abort, 0
underruns at 20.0 MS/s, VGA and amplifier as intended throughout and after,
`ptt(False)` 1.4–1.7 ms):

| Burst | Gain | Live at libre-2 | In the IQ |
| --- | --- | --- | --- |
| 1 | VGA 0, amp off | nothing (in-band −44.0 to −44.6 dB, as before) | ±2 MHz +0.07 dB; one bin +1.8 dB at −394.5 kHz |
| 2 | VGA 10, amp off | nothing | ±0.5 MHz +1.4 to +1.7 dB; a line at −396 kHz 20 dB over its neighbours, another at +219 kHz |
| 3 | VGA 20, amp off | in-band +1.7 dB (−42.6 to −42.9); line lock 0.12–0.51, one record "locked" at SNR 5.6 dB; no field or colour lock, no sound | below |

The third burst used the last of the three, so there was no ±10 MHz burst.
**Keyed: 25.1 + 25.0 + 25.0 = 75.1 s.**

**What the HackRF sent, seen at VGA 20** (`ana4.py`):
- **Shape.** The received excess (keyed over unkeyed) is the ideal
  signal's core: 81 % of it within ±0.5 MHz of the carrier against 70 %
  for the ideal. With CCIR 405 most of a 7 MHz p-p FM test card's power
  sits near the carrier, at the sync-tip and blanking frequencies (the two
  lines, 615 kHz apart: sync is 0.3 of the video range, and 405 cuts the
  low-frequency deviation by about 11 dB). The rest of the signal, ideal
  shares of 0.1–0.6 % per 0.5 MHz out to ±5 MHz, was under the floor:
  across the whole ±8 MHz view the signal is −2.8 dB against the noise.
- **Occupied bandwidth.** 99 % of the received excess lies in −2.0 to
  +1.5 MHz; the ideal's 99 % inside the view is −4.5 to +5.0 MHz (Carson
  ±8.9). So the measurement is limited by the signal-to-noise ratio, not
  by the transmitter: no energy appears anywhere the ideal has none.
- **Spurs and images.** None in libre-2's ±8 MHz view: no keyed-only
  line or band outside the signal's core, nothing beyond 1262 MHz or
  outside 1240–1325 (the guards never fired). The view cannot see beyond
  ±8 MHz and the ±10 MHz burst was not run, so the Carson edges and
  anything past them are unmeasured.
- **Sound subcarrier.** Not seen: ±6 MHz read −0.5 dB keyed against
  unkeyed (libre-2's slow AGC turning down for the signal), no line.
- **Decode.** Not possible: partial line lock, no field, colour or sound.
  The colour-burst figure the decoder logged (0.12–0.13 V) is measured
  without colour lock and means nothing here.

**Unkey to silence** (`ana5.py`). The ratio of power in the signal's core
(±0.6 MHz) to the band outside ±2.5 MHz, in 2 ms windows on the sample
clock fitted from the block stamps (residuals 3.9 ms median, 7.7 ms p95):
7.8 dB keyed, 0.9 dB after. Against `ptt(False)` it reads:
- −40 to 0 ms: 7.4–8.3 dB, as keyed;
- +2 to +16 ms: **15.4 dB**, the core 7 dB stronger than keyed;
- +18 to +54 ms: back to 6.8–8.1 dB;
- **from +58 ms: 1.6 dB and below - gone.**

The fit maps samples by when they *arrived* at the laptop, so true times
are earlier by the network and iiod latency, which this recording cannot
measure (it started after key-up, so there is no key-up edge to calibrate
against). The output therefore stopped **at most ~58 ms** after
`ptt(False)`. That is longer than the driver's order of events suggests
(VGA 0 under the lock, then `hackrf_stop_tx`): some 40 ms of full-level
signal after a 16 ms burst of concentrated power. Two readings fit and
neither is proven: libhackrf's in-flight transfers playing out at the old
gain because the VGA write waits behind them, or the latency offset
shifting the whole trace. The 16 ms rise looks like the carrier
concentrating, as it would if a transfer of zeros or a part-filled one went
out. Worth a cabled test with a key-up edge in the recording, to pin the
latency and see whether the VGA drop takes effect before the stream stops.

**The outside comb.** Lines at multiples of 15 625 Hz from 1251.000 MHz,
2.9 dB over the bins between them, keyed and unkeyed alike, as in 19a; the
+36.4 kHz comb of 19 stayed gone. It sits at a level our core already
exceeds by 5 dB at VGA 20 and is narrow; it did not stop the decoder (the
signal-to-noise ratio did).

**Laptop CPU** while keyed: 15 % user + system median (34 % peak at
key-up), receiver included.

**libre-2 afterwards:** written back from the dump taken before these
bursts and read back; it matches except the XADC/temperature/RSSI
readings: 666 kS/s, TX LO 1255.000062 MHz powered down, TX gains −89 / −41
dB, RX LO 2400 MHz, RF bandwidths 18 MHz / 382 950 Hz, DATV FIR on (`FIR
Rx: 128,4 Tx: 128,4`), `datv.sh`, `libre-ptt.sh` and `watchconsoletx.sh`
running, no iiod clients (the read-only DDS rate write fails as before).
The HackRF is closed, still plugged in, with the small antenna on it.

**Keyed today:** 265.5 + 75.1 = **340.6 s**.

Recordings `ohsatv-20261005-140026Z`, `-140107Z`, `-140418Z` (VGA 0 / 10 /
20), `hk-feed.jsonl`, `hk-times-*.json`, `ana3.py`–`ana5.py` in
`~/openhamshack-atv-captures/2026-10-05/hackrf/`.

### 19c. What the HackRF does at unkey and key-up: libhackrf 2024.02 and its firmware (5 October 2026, evening, analysis only, nothing radiated)

19b read the HackRF as going on for ~58 ms after `ptt(False)`, the first
~16 ms with the core of the signal 7 dB over its keyed level. Three
readings were open: (a) the stream runs dry and the radio sends an
unmodulated carrier (LO leakage with zero samples), concentrating power at
the carrier; (b) samples already queued in libhackrf play out at the old
gain because the VGA write is not ordered with them; (c) a timing offset
in the measurement. This section works out what the library and firmware
actually do, from the source of release 2024.02.1 (the version Debian 13
ships as `libhackrf0`): `host/libhackrf/src/hackrf.c` and `hackrf.h`,
`firmware/hackrf_usb/usb_api_transceiver.c` and `sgpio_m0.s`,
`firmware/common/rf_path.c` and `rffc5071.c`. It then re-reads burst 3's
recording and says what changed in the driver.

**What libhackrf does.**
- **The queue.** Four bulk transfers of 256 KiB (`TRANSFER_COUNT`,
  `TRANSFER_BUFFER_SIZE`) are in flight whenever the stream runs. That is
  524 288 samples: **26 ms at 20 MS/s, 52 ms at 10, 262 ms at 2**.
  `hackrf_start_tx` fills all four from the callback before submitting any,
  so the first 26 ms at 20 MS/s are whatever the callback had then (our
  ring is empty at that point, so zeros). On Linux the kernel copies an OUT
  transfer when it is submitted, so nothing already queued can be changed.
- **A callback that returns non-zero.** That transfer is not resubmitted
  and no further callbacks are made. The transfers in flight finish. It
  does **not** leave transmit mode. From `hackrf.h`: "in TX mode, when this
  happens, and the transmitter runs out of data to transmit, it will start
  transmitting all 0 values (but in older firmware versions, it started
  repeating the last buffer). To actually stop the operation, a call to
  `hackrf_stop_*` is needed." The header does not say which release made
  that change; `hackrf_info` reports the radio's firmware.
- **`hackrf_stop_tx`** cancels the transfers in flight, waits until all
  have come back, then sends transceiver mode OFF.
- **`hackrf_set_txvga_gain` and `hackrf_set_amp_enable`** are vendor
  control requests on endpoint 0. They are not queued behind the bulk
  data. The firmware handles the request in its USB interrupt at the SETUP
  stage and writes the MAX2837's TX VGA register over SPI straight away. A
  gain change therefore acts within one control transfer (a few tenths of a
  ms) of the call, on whatever sample is playing at that moment. **Reading
  (b) has no mechanism.**
- **`hackrf_set_antenna_enable`** sets `SWITCHCTRL_ANT_PWR`, the 3.3 V
  bias on the antenna port. It is not an RF path switch and has no part
  in stopping.
- **`hackrf_set_freq`** calls `rffc5071_set_frequency`, which calls
  `rffc5071_enable`. Retuning switches the front-end mixer's synthesiser
  back on, so retuning the LO away after a stop would undo part of the
  stop.
- **libusb threading.** These control calls are synchronous. Their
  completion is reaped by libhackrf's event thread, which is also the
  thread that runs the sample callback. The driver set the gain while
  holding the lock the callback takes (at key-up, on gain changes while
  keyed, and at unkey). If a bulk transfer came due just before the
  control transfer, the event thread waited in the callback for the lock,
  and the control transfer was never reaped: a hang, with a chance of a
  few per cent per call at 20 MS/s. It was not seen on 5 October. Fixed
  below.

**What the firmware does.**
- It has a 32 KiB bulk buffer on the device (`USB_BULK_BUFFER_SIZE`:
  0.8 ms at 20 MS/s, 8 ms at 2).
- When that buffer runs short in transmit, the M0 core writes **zeros** to
  the DAC and counts a shortfall (`sgpio_m0.s`: "In TX mode, zeroes are
  written to SGPIO"). It also sends zeros before the first data arrives.
  Zeros leave the DAC at mid-scale, so what goes out is the carrier from
  LO leakage and DC offset, at the current gain. **Reading (a)'s mechanism
  is real**, but it only applies while transmit mode is on with no data,
  and in the old order the gain was already at VGA 0 by then.
- Transceiver mode OFF (`transceiver_shutdown`) stops SGPIO and flushes
  the bulk endpoints. `rf_path_set_direction(OFF)` then puts the MAX2837
  and the MAX5864 DAC in standby, disables the RFFC5071 mixer, unpowers
  both RF amplifiers and sets the switches to receive. That is the actual
  "TX path off". After it, the radio emits no more than an idle HackRF.

So with the order the driver had (VGA 0, then `hackrf_stop_tx`), the
output should have dropped to VGA 0 within ~0.5 ms of `ptt(False)`. At
burst 3's VGA 20 that is 20 dB down. It should have been off within
~1.5 ms; the call itself took 1.4–1.7 ms. Neither a 7 dB rise nor 40 ms
at full level fits that.

**The recording again.** This is burst 3 (`ohsatv-20261005-140418Z`), with
ana5.py's 2 ms core/outer measure split into its two bands and indexed by
position in libre-2's 1 048 576-sample receive buffers:

| ms into each 1 M buffer | 0–48 | 48–52 | 52–56 | 56–60 | 60–64 | 64–65.5 |
| --- | --- | --- | --- | --- | --- | --- |
| outer band, keyed (dB) | 25.5–25.7 | 25.5 | 24.7 | 21.0 | 20.6 | 26.4 |
| outer band, unkeyed | 25.8–26.0 | 25.6 | 24.8 | 21.0 | 20.5 | 25.9 |
| core, keyed | 33.8–34.1 | 34.2 | 34.3 | 34.7 | 35.4 | 35.7 |
| core, unkeyed | 26.7–27.0 | 26.5 | 25.6 | 21.2 | 20.3 | 26.8 |

- **The last ~12 ms of every receive buffer reads ~5 dB low across the
  band**, keyed and unkeyed alike. That comes from libre-2 or its iiod
  path, not from the HackRF. Unkeyed, both bands drop and the ratio holds.
  Keyed, the core is mostly signal, so it holds while the outer band
  drops, and the ratio jumps. Through the keyed stretch the ratio reaches
  13 dB or more in 265 of 1975 windows (4 s), in runs of 12–16 ms spaced
  65.5 ms apart: one run per buffer.
- `ptt(False)` fell 49.6 ms into a buffer, so 19b's "+2 to +16 ms" burst
  sits 51.6–65.6 ms into it. **It is the buffer-tail artefact, not a
  carrier.** During it the outer band falls from 25–26 dB to 19–21 dB,
  while the core's strongest bins stay where they are when keyed: the
  line at −396 kHz, in the same 20 ms field pattern before and after.
- **The 58 ms.** The sample clock was fitted on the arrival basis: each
  block's last sample is placed at the median of the arrival stamps, so
  every sample is placed late by the pipeline latency. A 4 MiB buffer
  cannot reach the laptop until it has crossed the network. At 16 MS/s,
  libre-2's link runs close to its measured ceiling of 16.1–16.4 MS/s
  (blocks arrived every 65.5 ms, median), so each buffer takes ~64 ms to
  cross. The latency is therefore at least ~60 ms, and the true end lies at
  or before ~0 ms, within the fit's 8 ms p95. That is consistent with the
  ~1.5 ms the firmware analysis gives, and cannot resolve anything finer.

**Verdict:** (c) explains both observations. (b) has no mechanism in
libhackrf or the firmware. (a)'s mechanism exists but did not apply here.
There is no evidence that the HackRF radiated anything after `ptt(False)`
beyond the ~1 ms its order of events allows.

**Changed in the driver anyway** (`ohsatv/devices/hackrf.py`):
- **The lock.** Control calls no longer hold the ring's lock. A second
  lock, which the callback never takes, serialises them with the decisions
  that go with them. Key-up raises the gain under that lock, so a stop
  cannot slip in between the check and the raise (and see the key-up
  sequence below).
- **The unkey sequence**, all in `tx_stop()`:
  1. Stop feeding. A blocked `write()` returns, and the callback returns
     non-zero the next time it is asked, so no more picture is queued.
  2. Ramp the TX VGA to 0 along a raised cosine in amplitude over 4 ms,
     with one control transfer each time the envelope crosses a 1 dB step.
     Meanwhile the transfers already in flight (≥ 13 ms of signal at any
     rate we use) keep the picture playing under it. A slow bus skips steps
     rather than stretching the ramp.
  3. `hackrf_stop_tx`: the queue is cancelled, and transceiver mode OFF
     turns the RF path off.
  4. Amplifier off, so the next key-up starts quiet.

  Two things are deliberately not done: retuning away (it re-enables the
  mixer) and the antenna enable (it is DC bias). The gain still reaches 0
  before the stream stops; since a gain write acts at once, the device's
  32 KiB and any underrun zeros play at VGA 0. Ramping the samples instead
  could only begin behind the queue, 26–262 ms late depending on the rate.
  Shortening the transfers to make that fast would cut the underrun margin
  from 26 ms to a few ms, below Python's 5 ms thread switch interval, and
  is not worth doing untested.
- **The bound.** The radio is off 4 ms plus at most three control
  transfers after the call: 3.75–4.65 ms on the model at 0.3 ms per
  control transfer. The tests hold it to 10 ms. `ptt(False)` now takes
  about that long instead of 1.5 ms. A control transfer that never
  completes still hangs it, as before, because libhackrf's control calls
  have no timeout.
- **What the ramp cannot do.** It only covers the VGA's range. The last
  step, from VGA 0 to off, is still a hard edge, g dB under the keyed
  level for VGA g. That is −20 dB at burst 3's setting. Keyed at VGA 0
  (§19, 19a, burst 1 of 19b) there is nothing to ramp, and the whole level
  goes in that one step. With the amplifier on, its 14 dB goes with the
  RF path at step 3.
- **The model.** The tests' fake libhackrf now radiates. It plays four
  256 KiB transfers at fs, has the 32 KiB device buffer, applies control
  transfers when they complete, and handles cancel and mode OFF as above.
  While the path is on, output power = gain × (|sample|² + LO leakage at
  an assumed −30 dBc). It is tested at 2, 10 and 20 MS/s, at VGA 0, 20
  and 47, with the amplifier on and off. After the stop call the output
  never rises and is zero from 10 ms. It falls no more than 6 dB in any
  0.25 ms while within 20 dB of the keyed level, and takes over 1.6 ms to
  get 10 dB down. Nothing is queued after the call, and the same holds
  when the stream had already run dry.

**The carrier at key-up, found on the way and fixed.** `hackrf_start_tx`
fills its four transfers before `write()` has queued anything, so they are
zeros. The driver raised the gain as soon as `write()` had put its first
block in our ring, while those zeros were still waiting to play. For the
first ~26 ms at 20 MS/s (262 ms at 2), the radio sent the LO-leakage
carrier at the keyed gain, with the amplifier on if it was set, before
the picture started. Now:
1. `tx_start()` starts the stream at VGA 0 with the amplifier **off**,
   whatever `hackrfAmp` says.
2. The sample callback counts its calls. libhackrf asks for a refill as
   each transfer completes, in order, so call n + 4 means transfer n has
   gone to the device. The first transfer filled entirely from our ring is
   the first picture transfer. Once it has gone (its last byte in the
   device's 32 KiB, so nearly all of it already played), the picture is on
   the air. libhackrf 2024.02 also has
   `hackrf_set_tx_block_complete_callback`, which reports the same
   completions. It is not used, because it adds a symbol that would make
   `load_lib()` refuse an older library, and it says nothing the count
   does not.
3. The callback only sets a flag; libhackrf's thread must make no control
   calls. The transmit thread's next `write()` sees the flag, even while it
   is waiting for room in a full ring, and under the control lock switches
   the amplifier on (if set) and ramps the VGA up from 0 to the setting.
   The ramp mirrors the unkey: a raised cosine in amplitude over 4 ms. A
   `tx_stop()` during the rise stops it where it is and ramps down from
   there.

So the zeros, and any picture-less stretch before the gain is up, go out
at VGA 0 with the amplifier off. The picture itself goes out at VGA 0 for
about one transfer before the rise. On the model, the picture reaches the
air 27 / 53 / 263 ms after `tx_start()` and the gain is up at 37 / 83 /
325 ms, at 20 / 10 / 2 MS/s. The cost is that much later to full power.
The tests check, at 2, 10 and 20 MS/s, with and without the amplifier,
that no sample without picture goes out at more than VGA 0 or with the
amplifier on. With the old trigger, this fails in every case. They also
check that the rise is a ramp, with no quarter-ms more than 6 dB up while
within 20 dB of the setting, and that the gain is full within one transfer
plus the ramp of the picture starting.

`status()` now carries `txGainUpMs` (from `tx_start()` to the gain reaching
its setting) and `txStopMs` (the last `tx_stop()`, from the call to the
amplifier off), on the driver's monotonic clock.

**On air, to confirm** (fresh authorisation; VGA 30 or 40, antennas,
20 MS/s HackRF profile, libre-2 at 16 MS/s as in 19b):
- **Stamp on the laptop clock:** `ptt(True)` called; `ptt(False)`
  called and returned; and, polled from the HackRF's `status()` after
  each, `txGainUpMs` and `txStopMs`. Add a stamp inside the transmit
  thread just before `tx_start()` if the scripts can; otherwise
  `ptt(True)` plus the loop build. Record IQ from before key-up to at
  least 0.5 s after unkey, as in 19b, with the per-block arrival stamps.
- **Calibrate libre-2's latency on the key-up edge.** The rise is now a
  sharp, known event: full gain at `t_key + txGainUpMs`. Find the 4 ms
  rise in the recording, take the offset, and apply it to the unkey. Do
  not read anything in the last ~12 ms of any 1 M-sample buffer (the
  ~5 dB dip at 52–65.5 ms into each); either mask those windows or use a
  shorter receive buffer.
- **Key-up, expected:** from `tx_start()`, ~27 ms with only the bare
  carrier at VGA 0, amplifier off. That is 30–40 dB under the keyed level,
  less the LO leakage, so probably under libre-2's floor; if a line at
  the carrier shows there at all, record its level against the keyed
  signal, since that sizes the leakage the model assumes at −30 dBc.
  Then ~7 ms of picture at VGA 0, then a smooth 4 ms rise of 30 or 40 dB
  with no step over ~5 dB, gain up ~37 ms after `tx_start()`. Flag any
  carrier at more than the VGA 0 level before the picture, and any
  sudden step.
- **Unkey, expected:** from `ptt(False)`, a smooth fall over ~4 ms of 30
  or 40 dB, no step over ~5 dB, then the last ~30–40 dB-down remnant gone
  within ~1 ms. The RF path is off by `txStopMs` (≈ 4.5 ms). Flag
  anything after that, any rise at any point, and any line at the
  carrier (the zero-sample carrier) at any time.
- Run `hackrf_info` for the firmware release, because older firmware
  repeats the last buffer instead of sending zeros when a stream ends
  without a stop.

### 19d. The HackRF over the air at VGA 30, with the new key-up and unkey (5 October 2026, evening, authorised by Brian, GM4BRI)

**As run.** Main a6a8f39 (the key-up and unkey of 19c) from a scratch copy on
the laptop, not installed; HackRF firmware **v2.3.1** (USB API 0x0108, board
id 2, HackRF One), libhackrf 0.9 / 2024.02.1. The HackRF on its small
antenna, libre-2's antenna nearby as in 19b. 1251.000 MHz, FM-ATV PAL to the
HackRF profile (20 MS/s, 7 MHz p-p, 5 MHz video, CCIR 405, 6.0 MHz sound
subcarrier), test card with GM4BRI and a 1 kHz tone. **TX VGA 30, amplifier
off** throughout, read from the driver every 0.2 s (setting and the value
last written); every VGA write and the `tx_start`/`tx_stop` calls stamped on
the laptop clock. libre-2 received at 16 MS/s on 1251.000 (its DATV FIR
bypassed by the driver), recording IQ from 2 s before key-up with per-block
arrival stamps. Before keying: nothing on the laptop held the nano or USB bus
3; libre-2 had no iiod clients but ours and no pluto_dvb, and its writable
state matched 19a-19b's restore values exactly. Guards: Galileo E6 above
1262 MHz, anything outside 1240-1325 MHz, the gain. Two bursts, no abort:

| Burst | libre-2 | Keyed | Result |
| --- | --- | --- | --- |
| 1 | 1251.000 | 25.1 s | picture locked, colour, no sound |
| 2 | 1241, then 1261 MHz (the Carson edges) | 25.0 s | nothing of ours above the floor in either view |

VGA 30 decoded, so VGA 40 was not used; burst 3 was not needed. **Keyed this
evening 50.1 s; today 340.6 + 50.1 = 390.7 s.** 0 underruns in both bursts
at 20.0 MS/s; `ptt(False)` returned in 6.1 and 5.4 ms; `txStopMs` 4.76 and
4.89 ms; `txGainUpMs` 61.5 and 55.0 ms (19c's model gave 37: the first
picture transfer reached the device later than the model's).

**Decode (burst 1, 22 one-second records keyed).** Locked in 21, field lock
21, colour lock 19; hLock median 0.93 (0.53-0.96); SNR median 14.3 dB
(12.6-18.6); jitter median 82 ns (40-123); colour burst 0.139 V median
(0.136-0.143). Sound: SINAD 2.8 dB, i.e. none - the 6.0 MHz subcarrier
(index 0.07) is under libre-2's floor at this level (the 5-7.4 MHz bands
show no excess keyed). In-band level rose from −44.4 to −40.9 dB; the
floor fell 2.4 dB keyed (libre-2's slow-attack AGC).

**Spectrum (burst 1, 7.5 s keyed against 1.7 s unkeyed, 1 kHz bins).** The
excess power keyed lies 99 % within −1.31 to +1.16 MHz, the part of the
signal above libre-2's floor (+13.1 / +12.3 dB in ±0.5 MHz, +2.8 / +4.7 in
0.5-1.5 MHz, nothing beyond 1.5 MHz). The strongest bins are the lines at
−396 and +220 kHz seen in 19b (sync tip and blanking). Nothing new anywhere in
±7.4 MHz. Burst 2's ±10 MHz views (1233-1249 and 1253-1269 MHz) read the same
keyed and unkeyed to within ~1 dB: the Carson edges are under the floor, and
there are no spurs or images there. The lines in those views - 1240.0,
1259.89, 1260.016 (−20 dB) and 1264.0 MHz - are all there unkeyed at the
same level and sit at fixed offsets from libre-2's LO (−1.0, −1.1, −0.98,
+3.0 MHz). So does the "steady carrier near 1250 MHz" of 17b-19b (LO −1.016
MHz at 1251.000): **it is libre-2's own, not an outside signal.**

**The receive timing could not be used as planned.** libre-2's receiver lost
0.88 s of samples during burst 1 (`rxDropped` 12.8 M; 29.59 s recorded over
30.47 s of wall time; recorder `lost_samples` 0, so the loss was upstream of
it). The receiving process was decoding, recording at 64 MB/s and sharing
the laptop with the 20 MS/s transmitting engine: up to 2.2 cores, laptop
mean 33 % busy, peak 57 %. The arrival lag was 2-12 ms around key-up and
grew to ~700 ms between +2 and +14 s, so the one sample-to-clock mapping
holds at key-up (calibration: the recording's half-way point on the rise
sits 51 ms after the laptop's mid-ramp write) but not at unkey. The edges
were therefore found in the recording directly and timed against
themselves; how long after `ptt(False)` the output stopped, in absolute
terms, is not measured.

**Key-up.**
- Laptop: `tx_start` at t0; VGA writes 0 (at t0+3.5 ms), then 7, 14, 20, 23,
  25, 27, 29, 30 over 4.31 ms, starting 60.6 ms after t0; amplifier off.
- Recording, ±0.5 MHz core at 0.25 ms (excluding ±20 kHz): floor ~16-18 dB,
  then 22.1, 25.2, 26.3, 28.0, 27.4, 30.2 - floor to keyed in ~1.25 ms -
  then flat at ~30. Only the ramp's top ~13 dB is above the floor (VGA ≥ ~17:
  writes 20-30, 2.8 ms apart on the laptop; the recording shows them in
  ~1.5 ms, so the control transfers did not land at the moment they were
  written - not resolved). Largest 0.25 ms step 6.0 dB, the VGA 14→20 write
  coming out of the floor. The ramp's own bottom steps are 7, 7 and 6 dB (a
  raised cosine in amplitude, quantised to the VGA's 1 dB steps and ~0.4 ms
  per control transfer): 19c's "no step over ~5 dB" cannot hold at the
  bottom of the ramp, though it does from VGA 20 up.
- **No bare carrier before the picture.** The 80 ms before the edge (start_tx's
  zeros, then picture, both at VGA 0) against 400-120 ms before: core +0.7 dB
  (noise), largest 1 kHz bin within ±60 kHz +1.8 dB at −2 kHz. A carrier at
  keyed gain for 26 ms - the old driver - would have stood ~13 dB over the
  bin floor if the leakage is the −30 dBc 19c assumes. At VGA 0 the leakage
  stays under the floor: this bounds it only at about −11 dBc, so the −30 dBc
  figure is still unmeasured.

**Unkey.**
- Laptop: `ptt(False)` at t1; VGA writes 29, 28, 26, 24, 21, 16, 8, 0 over
  4.0 ms; `tx_stop` done at t1 + 4.8 ms.
- Recording: core ~30 dB, then down to ~18 (the unkeyed level) over ~2.5 ms,
  with the window-to-window wobble an FM picture has at 0.25 ms (±3 dB; one
  18.3 dB window at the start of the fall bounces back to 29). The visible
  part is again the ramp's top ~12 dB (writes 29-16, 2.8 ms on the laptop).
  No rise at any point. After the edge: 4-204 ms against 1.0-1.2 s later,
  −0.07 dB over the whole view, largest 1 kHz bin within ±60 kHz +1.5 dB.
  **Nothing after the ramp, and no carrier line.**
- The edge fell inside a 1 M buffer's last 12.5 ms; no ~5 dB dip shows there
  (keyed level 30 inside the tail as outside it), so the buffer-tail dip 19c
  describes did not appear in this recording.

**Verdict.** At VGA 30 with the amplifier off the HackRF decodes over the air:
locked picture in colour, SNR ~14 dB, no underruns, nothing out of band in
1233-1269 MHz. The new key-up keeps the carrier off until the picture: none
seen before the rise. Both edges are ramps over the top ~12-13 dB visible,
with nothing after the unkey ramp. Still owed: sound (needs more signal at
libre-2: VGA 40 or a cabled path), the Carson edges and the LO-leakage level
(both under the floor here), and the absolute time from `ptt(False)` to
silence, which needs a receive chain that does not drop samples while the
transmitter shares its host (a second machine, or no decode while
recording). Libre-2 restored and read back: matches its starting state
except readings (XADC, temperatures, RSSI, the AGC's gain); FIR on (`FIR Rx:
128,4 Tx: 128,4`), DATV scripts running, no clients. The read-only DDS rate
write fails as before. The HackRF is closed and still plugged in, with its
antenna.

Recordings `ohsatv-20261005-145045Z` (burst 1) and `-145212Z` (burst 2),
stamps `hk-times-1791211847.json` / `-1791211934.json`, `hk-feed.jsonl`,
`hk-rx.jsonl`, `txh4.py`, `rxh4.py`, `ana8.py`, `ana9.py` and libre-2's dumps in
`~/openhamshack-atv-captures/2026-10-05/hackrf/hk4/`.

## 20. Teletext: what the standards say (5 October 2026, for ohsatv/vbi.py, ttx.py, tti.py)

Sources read, not remembered: **ETSI EN 300 706 V1.2.1 (2003-04), Enhanced
Teletext specification** (the ETSI PDF; clause numbers below are its), the
**MRG Systems "TTI Page file format" v1.1** (2005, zxnet.co.uk/teletext/
documents/ttiformat.pdf), and **vbit2's wiki "Page files"**
(github.com/peterkvt80/vbit2/wiki). Cross-checked against OpenHamShack
Teletext's DESIGN.md "The .tti contract" and a capture of what vbit2
transmitted (its tests/fixtures/vbit2-sample.t42, copied into ours).

**Physical layer (System B, 625 lines).**
- Lines: "lines 6 to 22 and 318 to 335 may be used" when multiplexed with a
  picture (4.1). Not during field sync and equalising pulses (4.2).
- NRZ (5.1). Levels in negative-modulation systems: 0 = black level ± 2 %;
  1 = 66 ± 6 % of black to peak white (5.2) - 0.462 V on a 0.7 V picture.
- Bit rate 444 fH = 6.9375 Mbit/s ± 25 ppm (5.3).
- Spectrum of the data pulses: "skew symmetrical about 0,5 × bit rate,
  substantially zero by 5 MHz" (5.4, figures 2-3). A Nyquist pulse; the
  roll-off is left to the transmission filters. 5 MHz from a 3.47 MHz
  centre is a raised cosine of roll-off 0.44.
- Clock run-in 1010101010101010, framing code 11100100, both in
  transmission order (6.1, 6.2). The run-in's first ones may be missing
  at the receiver (6.1 note).
- Timing reference: from 0H to "the mid point of the penultimate '1' of
  the clock run in", nominally **12.0 µs, +0.4 / −1.0 µs** (6.3, figure 4).
  So the first run-in bit is centred at 10.27 µs and the 360th bit at
  62.0 µs.
- 45 bytes a line, LSB first (7.1).

**Coding.** Odd parity: bit 8 makes the count of ones odd (8.1). Hamming
8/4: bits 1, 3, 5, 7 protection, 2, 4, 6, 8 data; P1 = 1⊕D1⊕D3⊕D4, P2 =
1⊕D1⊕D2⊕D4, P3 = 1⊕D1⊕D2⊕D3, P4 = 1⊕P1⊕D1⊕P2⊕D2⊕P3⊕D3⊕D4; single errors
corrected, double detected (8.2). Packet address: byte 4 magazine (3 bits,
8 sent as 0) and packet bit 0, byte 5 packet bits 1-4 (7.1.2).

**Pages.** Header X/0: bytes 6-7 page units, tens; 8 S1; 9 S2 + C4; 10
S3; 11 S4 + C5, C6; 12 C7-C10; 13 C11-C14; 14-45 32 displayable
characters, the last 8 "usually ... a real-time clock" (9.3.1). C4 erase,
C5 newsflash, C6 subtitle, C7 suppress header, C8 update, C9 interrupted
sequence, C10 inhibit display, C11 serial mode, C12-14 national option
(table 2). A page ends at the next header of its magazine (parallel) or
any header (serial) (7.2.1). Rows X/1-X/25 are 40 odd-parity characters;
"packets containing no information do not need to be transmitted" (7.2.3).
Page FF is the time-filling header (7.3). After a header, rows of a normal
page wait 20 ms; enhancement pages may follow at once (Annex B.6).

**Links (FLOF).** X/27/0: designation 0, six links of 6 Hamming bytes
(units, tens, S1, S2 + M1, S3, S4 + M2 M3; the M bits complement the
packet's magazine), byte 43 the link control (bits 1-3 set "in the absence
of any local Code of Practice", bit 4 display row 24), bytes 44-45 a CRC
(9.6.1): a 16-stage register fed with input ⊕ stages 7, 9, 12, 16, over
header bytes 14-37 and rows 1-25, bits b8 first. **vbit2 sums the bytes
with their parity bits**; with that, our CRC equals both of the capture's.
No link: page FF, sub-code 3F7F; vbit2 sends 8FF:3F7F.

**8/30 Format 1** (9.8.1): designation 0 (multiplexed), initial page with
absolute magazine, network ID (16 bits), time offset (half hours, bit 7
negative), MJD and UTC as digits each plus one, 20 characters of status
display. Sent "immediately following the boundary between clock-seconds"
(Annex B.5).

**Level 1 display** (12.2): set-at and set-after attributes as in
ohsatv/ttxrender.py's docstring. The English national set (15.6.2): 23 £,
5B ←, 5C ½, 5D →, 5E ↑, 5F #, 60 ―, 7B ¼, 7C ‖, 7D ¾, 7E ÷.

**.tti** (MRG v1.1, with vbit2's reading where MRG is silent): DE, PN
mppss (each PN a subpage), SC hex, PS hex (bit 15 transmit, 14 C4, 0-6
C5-C11, 7-9 C12-C14), CT n,T|C, OL rr,text (controls as 80-9F, or ESC +
code + 40 - the "Viewdata" escape, which OpenHamShack Teletext writes), FL six
mpp. vbit2 defaults PS to 0000 and then sends nothing; carousels are
renumbered 0001... (Annex A.1). vbit2 sends rows X/1-X/24 every time,
blank ones as spaces.

Not looked up because not built: Level 1.5-3.5 (X/26, X/28, M/29),
magazine inventory, TOP, Packet 8/30 Format 2 (PDC).

## 21. 405 lines on air: the nano and the HackRF to libre-2 (5 October 2026, evening, authorised by Brian, GM4BRI)

Main 865646d (405-line System A, just merged) from a scratch copy on the
laptop, nothing installed. Transmit: AM-VSB, Format "405 lines", test card
with GM4BRI and a 1 kHz tone, vision carrier 1255.000 MHz (sound 1251.500,
channel 1251.45-1255.75). Receive: libre-2 at 16 MS/s on 1255.000, Format
left on "625 PAL" with format recognition on, IQ recorded 20 s per burst.
Before keying: nothing held the nano, the HackRF or libre-2 (no iiod
clients, no pluto_dvb); a 60 s non-radiating nano bench at 5 MS/s (TX LO
down, -89.75 dB) gave **0 late buffers** (5.035 MS/s delivered) now that
the laptop leaves the nano's USB network down (18b). The nano's TX LO and
rate and the HackRF's VGA and amplifier were read back during every burst.
Captures, logs, scripts and dumps: `~/openhamshack-atv-captures/2026-10-05/a405/`
(recordings in `~/r405-rec/` on the laptop).

| Burst | Transmitter | Keyed | Lock (records) | hLock | SNR | jitter | tone SINAD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | nano, 5 MS/s, -24 dB | 30.5 s | 24/28 | 0.57 | 4.8 dB | 281 ns | 11.8 dB |
| 2 | nano, 5 MS/s, -14 dB | 30.6 s | 27/27 | 0.94 | 13.9 dB (12-17.5) | 180 ns | 18.2 dB |
| 3 | HackRF, 8 MS/s, VGA 30, amp off | 30.3 s | 7/29 | 0.41 | ~4 dB | 320 ns | 8.8 dB |
| 4 | HackRF, 8 MS/s, VGA 40, amp off | 30.5 s | 28/28 | 0.79 | 9.4 dB | 231 ns | 16.5 dB |

Medians over the records from 2-3 s after key-up. Keyed **121.9 s**; the
day's total is 390.7 + 121.9 = **512.6 s**. VGA 40 was used because at VGA
30 libre-2 held lock in only 7 records of 29. No abort, no late transmit
buffers from the nano, no HackRF underruns; the nano's TX LO read back
1253599998 Hz and 5 MS/s throughout.

- **Format recognition works off air.** In burst 1 the receiver, started
  on 625 lines, switched to "Receiving 405 lines" 1.6 s after key-up,
  moved its LO from 1257.4248 to 1253.6 MHz, and held FIELD LOCKED from
  2.6 s. It stayed on 405 afterwards, so the other bursts were found
  already tuned. A 405-line test card was received off air for the first
  time: `docs/screenshots/hardware-23cm-405-nano-to-libre-minus14.png`.
- **Playback** of each recording with Format forced to 405 gives the same:
  15, 19, 1 and 17 locked records of 22, SNR 4.8 / 13.7 / - / 8.9 dB,
  SINAD 12.3 / 18.2 / 9.5 / 14.7 dB.
- **Carrier levels.** The AM sound carrier's line came out 0.8 dB (nano)
  and 0.1 dB (HackRF) above the vision carrier's. That fits the standard's
  4:1: with positive modulation the vision carrier line is the mean
  carrier, about half of peak white on a grey test card, and the sound is
  6 dB under peak white.
- **HackRF timing.** ptt(False) returned in 5.4 and 7.9 ms; txStopMs 4.8
  and 6.5 ms; full gain 87.7 ms after tx_start at 8 MS/s (four 16 ms
  transfers, then the 4 ms ramp: 19c). libre-2's receiver, sharing the
  laptop with an 8 MS/s transmitter and the window, dropped 0.8 s of
  samples in burst 3 and 8.4 s in burst 4 (rxSkips); the IQ recordings lost
  none.

**Out of the channel: images the live guard did not see.** The
recordings, analysed keyed against unkeyed (`ana405.py`: 977 Hz bins,
the floors aligned for the slow AGC), show components outside 1251.4-1255.8
MHz that appear only when keyed:

| Transmitter | Frequency | Level | What it is |
| --- | --- | --- | --- |
| nano, 5 MS/s (both bursts) | 1256.481 MHz | **-13.4 dBc** (14.2 dB under the sound carrier) | the sound carrier's image, sound + fs |
| HackRF, 8 MS/s, VGA 30 and 40 | 1259.504 MHz | **-18.6 / -18.8 dBc** | the sound carrier's image, sound + fs |
| HackRF, 8 MS/s, VGA 40 | 1247.004 MHz | -29.0 dBc | the vision carrier's image, vision - fs |

(dBc against the vision carrier line.) All are inside 23cm and below
1262 MHz (nothing new in Galileo E6), but above the -40 dBc the bursts
were held to. The live guard was meant to abort on just that; it took
only the strongest bin outside the channel, which was an outside line at
1260.006 MHz about 10 dB stronger than any of the images, present keyed
and unkeyed. It never saw them. A guard for the next burst must look for
components that are **new**, not for the strongest.

Why: System A's sound sits 3.5 MHz below vision, at 0.42 fs at 5 MS/s.
The AD936x interpolates to its DAC through half-bands, and a half-band's
transition band is symmetric about fs/2, so the image at fs - 0.42 fs is
barely filtered. The 17 model (`devices/ad936x.py`) gives 13.0 dB for it,
against 14.2 measured; it gives 30.5 dB at 6.25 MS/s and 56 at 8. A HackRF
has no interpolating filter at all. A band-pass filter after the radio
cannot remove any of these: they are inside the band, beside the signal.
On a USB nano, 405 lines with sound at 5 MS/s cannot meet -40 dBc.

Branch `fix/405-air` (not merged): the 405 plan now carries `txImage` and
the transmit panel names the image for a Pluto above -40 dBc, and gives a
HackRF its measured figures (a warning, not a refusal: Brian decides); and
an IQ recording now gets a new SigMF capture segment when the receiver
retunes - burst 1's metadata gave 1257.4248 MHz for all 20 s, 16 of which
were at 1253.6.

libre-2 was restored from the dump taken before the first burst and read
back: everything matches except temperatures, XADC supplies and RSSI
(666 kS/s, TX LO 1255.000062 MHz powered down, -89 / -41 dB, RX LO 2400
MHz, slow attack, FIR on with `FIR Rx: 128,4 Tx: 128,4`, the DATV scripts
running, no iiod clients). The nano was put back as the NCM work left it
(both LOs 1250.999998 MHz, TX LO down, -89.75 dB, 5 MS/s, 5 MHz
bandwidths) and read back with no difference. The HackRF is closed.


### 21a. The images: modelled, decided, and one HackRF burst at 20 MS/s (5 October 2026, late evening, authorised by Brian, GM4BRI)

**Brian's decisions** after 21: on a Pluto, 405 lines at 5 MS/s go without
sound (the sound carrier is not generated) and need at least 6.25 MS/s for
it; on a HackRF they run at 20 MS/s with the baseband filter narrowed to
the signal; the txImage warning stays for whatever remains over -40 dBc.

**The AD936x model**, applied to the whole signal - the 405 test card and a
1 kHz tone through `sysa.Mod`, interpolated as the chip does (TX HB1 at 2
fs from ADI's taps, then HB2, HB3, the DAC's hold and the analogue filters
as responses), in 1 kHz bins as `ana405.py` used, dBc against the vision
carrier's bin, outside 1251.4-1255.85 MHz (`tests/test_sysa.py`
`test_a_pluto_s_images`; scratch `model.py`):

| Rate, placement | Strongest outside the channel | What it is |
| --- | --- | --- |
| 5 MS/s with sound, centred (as on 5 October) | -12.8 dBc at vision +1.50 MHz | sound + fs; measured -13.4 |
| **5 MS/s mono** (vision +1.125 MHz from the LO) | **-68.3 dBc** at vision -4.40 MHz | the vestige's image; the carrier's own is -92 |
| 6.25 MS/s with sound, centred | -30.3 dBc at vision +2.75 MHz | sound + fs |
| **6.25 MS/s with sound, carriers either side of the LO** (vision +1.75, sound -1.75 MHz) | **-48.1 dBc** at vision +2.75; -48.4 at vision -6.25 | sound + fs, vision - fs; the picture's under -59 |
| 8 MS/s with sound, centred | -56.1 dBc at vision +4.50 MHz | sound + fs |

Mono at 5 MS/s meets -40 dBc by 28 dB: the channel is 3.75 MHz in the 4.75
the rate carries, and its edges - the top of the vestige, the finest
picture detail - are its weakest parts. No mitigation was needed there.
At 6.25 MS/s the centred plan misses by 10 dB; a scan of the LO placement
(vision from +0.8 to +2.0 MHz from the LO) found the minimum where the two
carriers sit equally far either side of the LO, which puts both images
equally deep in the half-bands' stop band. `sysa.plan` does that whenever
centred would exceed the limit and the band fits (`_placement`), so 8 MS/s
and up keep the centred plan that went on air, and the warning no longer
fires at 6.25.

**The MAX2837 model** (`devices/hackrf.py`, from Maxim 19-5235 Rev 3; the
datasheet was fetched from the Wayback Machine's copy of
datasheets.maximintegrated.com, analog.com refusing the connection): one
filter field serves receive and transmit (LPF register 1, D7:D4; D3:D2 picks
the path); transmit flat to 0.2 dB over 0-2.3 MHz at the 5 MHz setting and
0-4.6 at 10; the receive table's rejection points (7 dB at 3.3 MHz for 5
MHz; 7 at 6.7, 57 at 15, 75 at 20 for 10 MHz) fit 10 log10(1 + (x /
1.208)^14.42), x the frequency over half the setting, within 0.4 dB;
stopped at the transmit table's 80 dB. With the converter's hold, that
model makes 21's 8 MS/s images (6 MHz filter) -39.5 and -51 dBc where -18.6
and -29.0 were measured: a shortfall of about 20 dB, not explained (the
transmit corner, the converter's output stage, libre-2 itself), carried as
a constant. With it the model gives -19.5 and -31.0 for 21's bursts, and
**at 20 MS/s through the 5 MHz setting -78.6 dBc** (sound + 20 MHz) and
-82.5 (vision - 20 MHz); -98.6 and -102.5 on the datasheet alone. The
passband loses 0.2 dB at the signal's edge, 2.15 MHz out.

**CPU at 20 MS/s** (shack, Ryzen 9 6900HX): a static picture is a 0.16 s
loop built once (0.29 s), then 8.7 % of a core to convert it to 8-bit
for the HackRF; a moving picture through `sysa.Mod` takes 62 %.

**The burst.** Branch `worktree-agent-a4f61b2f9ccd44e34` at 0e8751d, from a
scratch copy on the laptop (`~/ohsatv-a405b`), nothing installed. The
HackRF on its small antenna; 405 lines, test card with GM4BRI and a 1 kHz
tone, vision 1255.000 MHz (sound 1251.500), 20 MS/s (`a405Rate`), TX VGA
40, amplifier off. libre-2 receiving at 16 MS/s with manual gain 68 dB (so
levels compare across retunes), Format "625 PAL" with recognition on.
Scripts: `rxa405b.py`, `txa405b.py`, logs and dumps in
`~/openhamshack-atv-captures/2026-10-05/a405b/`.

- **The shared lock, taken by the drivers.** Before: no marker on the
  laptop, none on libre-2, no OpenHamShack process on either. Opening libre-2
  wrote `net-198.51.100.74` in `/run/lock` on the laptop and in `/tmp` on
  libre-2 (use rx, "1255.000 MHz AM-VSB PAL"); opening the HackRF wrote
  `hackrf-0000000000000000930c64dc295da8c3` (use tx, this computer only),
  removed again at close.
- **Unkeyed sweep first**, four views: the main channel with 625's LO
  (1257.4248 MHz) and with 405's (1253.6), sound/vision + 20 MHz (LO
  1273.25, covering 1265.6-1281) and - 20 MHz (LO 1238.6, covering 1230.9-
  1246.3, rxFreq 1240.000, the band's edge). Then back to 625 with
  recognition on.
- **Keyed 3.2 s, then aborted - by the guard, falsely.** The engine set the
  HackRF's filter to 5 MHz before the gain (VGA 40 reached 67.3 ms after
  key-up; 20.2-20.5 MS/s delivered; **0 underruns**). Recognition switched
  libre-2 to 405 lines between 2 and 3 s after key-up and moved its LO to 1253.6; the
  first record after that showed **locked, hLock 0.62, SNR 7.9 dB,
  jitter 281 ns**, vision -18.0 and sound -18.4 dB (0.4 dB apart), and a
  "new" bin at 1251.178 MHz at the vision carrier's level. That is
  1253.6 - 2.4248: the vision carrier seen through the *old* LO, in frames
  the spectrum feed had already labelled with the new one - the guard
  max-held the last six frames under one label, and the retune's latency
  put both LOs' samples under it. The guard unkeyed at once (ptt(False)
  11.2 ms, HackRF off 5.2 ms after the call). A received SINAD of 16.2 dB
  came from the second after.
- **So the images at +-20 MHz were not measured on air.** The plan was to
  move libre-2 to the +20 and -20 MHz views at 14 and 20 s into the over;
  the over ended at 3.2 s. The keyed IQ recording was lost as well: the
  scratch script asked for it while the unkeyed sweep's recording still
  ran, and the app kept the one it had. Brian authorised one burst; it
  was not repeated. Whether anything else outside the channel was new in
  that last second is not known: only the strongest new bin was logged. What remains is the model: -78.6 dBc at
  20 MS/s, with the shortfall.
- **Two things for the next burst.** The guard must ignore at least the
  first second after any retune, including one recognition makes - or the
  app's spectrum feed should label frames by the LO their samples were
  taken at, which would also correct what the window draws for a moment
  after each retune. And the keyed recording must not overlap another.
  libre-2 at 16 MS/s, sharing the laptop with a 20 MS/s transmitter whose
  engine also decoded its own simulated receive, dropped 31.4 M samples
  (~2 s) during the over; the transmitting engine used 1.1-1.9 cores.

Keyed **3.2 s**; the day's total is 512.6 + 3.2 = **515.8 s**.

libre-2: the receiver was stopped by signal, not by its close(), so it
put nothing back and left its marker; both markers (token
42d3363c112567c2, process gone) were removed by hand, and libre-2 was
restored from the dump taken before the burst and read back: everything
matches except temperatures, supplies and RSSI, and RX gain 0 at 71 dB
where it was 69 - the slow-attack AGC's own reading, not a setting (666
kS/s, TX LO 1255.000062 MHz powered down, -89 / -41 dB, RX LO 2400 MHz,
slow attack, FIR on with `FIR Rx: 128,4 Tx: 128,4`, rf bandwidths 18 MHz
and 382.95 kHz, the DATV scripts running). The DDS-core sampling
frequency refused its write with an I/O error, as on 5 October, and reads
back unchanged. The HackRF is closed; the nano was not used.

## 22. NICAM-728: what the standards say, and what the chain measured (5 October 2026, for ohsatv/nicam.py; nothing radiated)

Sources read, not remembered: **ETSI EN 300 163 V1.2.1 (1998-03), NICAM
728: transmission of two-channel digital sound with terrestrial television
systems B, G, H, I, K1 and L** (the ETSI PDF; clause numbers are its),
**BBC RD 1990/6** (A.J. Bower, *NICAM 728 - digital two-channel sound for
terrestrial television*; downloads.bbc.co.uk/rd/pubs/reports/1990-06.pdf,
a scan: the history and the design aims), **ITU-T J.17** through EN 300
163 table 2. **hacktv** (github.com/fsphil/hacktv, src/nicam728.c, GPLv3)
was built in a scratch area as the bit-for-bit cross-check; nothing of it
runs in the program. BBC RD 1986/2, which the brief named, turned out to
be C.K.P. Clarke's *Colour encoding and decoding techniques for
line-locked sampled PAL and NTSC* - nothing to do with NICAM.

**System I, as the standard gives it.**
- Carrier 6.552 MHz above vision ± 1 ppm (5.2.3.2); 728 kbit/s ± 1 ppm
  (5.2.2); DQPSK at 364 kbaud, bit pairs as phase changes 00 0°, 01 -90°,
  11 -180°, 10 -270° (table 4).
- Level: peak vision to NICAM 100:1, -20 dB (5.2.4). (The FM sound, 10 dB
  in the original System I, relaxed to 13 dB in the UK: 5.1.3.2 note.)
- Shaping: H(f) = cos(π f ts / 2) to 1/ts at each end (5.2.5.2), so
  transmitter and receiver together are a **100 %** raised cosine. 40 % is
  B, G, H, K1 and L (5.2.5.1). The brief had 40 % for System I with a
  "check": it is 100 %. NICAM occupies 6.188-6.916 MHz.
- Frame (4.1, figure 1a): 1 ms, 728 bits - FAW 01001110, C0 (1 for eight
  frames, 0 for eight), C1-C3 (000 stereo, 010 dual, 100 mono + data,
  110 data), C4 (FM is the same programme), AD0-AD10, then 64 words of
  11 bits, A1 B1 A2 B2 ..., each LSB first with its parity bit last.
- Interleave (4.1.2): transmitted bits 25, 26, 27 ... carry frame bits 25,
  69, 113 ... 685, then 26, 70 ...; neighbours go out at least 16 apart.
- Scrambling (4.1.3, figure 2): x^9 + x^4 + 1 from 111111111, after the
  FAW, after interleaving; "the sequence shall start 0000 0111 1011 1110
  0010" - which is what ohsatv/nicam.py makes.
- Sound (4.2.5): 32 kHz, 14 bits, J.17 pre-emphasis (|H|² = (1 +
  (ω/3000)²) / (75 + (ω/3000)²)); companding to 10 bits on the largest
  sample in 32 (figure 3: drop 4, 3, 2, 1 or 0 LSBs), the 3-bit scale
  factor 111 / 110 / 101 / 011 / 100-010-001 (protection ranges 5-7)
  (table 3); even parity over each sample's six MSBs, inverted in nine
  samples per scale-factor bit to carry it (4.2.5.5: stereo i = 1, 7 ... 49
  for R2A, 2, 8 ... for R2B, 3, 9 ... for R1A and so on).
- Alignment (4.2.5.2): 400 Hz at 0 dBu0s is 24.3 dB below the coding
  range's maximum in System I, and deviates the FM sound ±17 kHz.

**Checked against hacktv.** Its nicam728.c and ours read the clauses the
same way everywhere looked at (scale-factor table, parity signalling order,
the interleave as `xi += 16` modulo 703, the PRBS, the step table
{0, 3, 1, 2}). Run on 48 frames of stereo audio covering every scale
factor, its frames and ours are identical bits (tests/test_nicam.py,
Hacktv; its input and output are tests/data). Its J.17 is an 83-tap FIR on
the 16-bit samples with 0 dB at high frequencies; ours a first-order IIR
fitted to J.17 within 0.05 dB from 20 Hz to 15 kHz, scaled to the
alignment level above, so a NICAM tone and an FM tone of the same audio
come out equally loud.

**Measured through the chain** (two AD936x, helpers.ad936x_link; noise over
the whole sample band; 19 kHz offset; the stereo test; bit errors counted
against what was sent):

| | 30-40 dB | 20 | 15 | 14 | 13 | 12 |
| --- | --- | --- | --- | --- | --- | --- |
| AM-VSB 10 MS/s | 0 | 0 | 1.9e-4 | 1.1e-3 | 3.7e-3 | 9e-3 (picture on 94 % of lines) |
| FM-ATV 20 MS/s, 7 MHz p-p, 265 kHz RMS | ~6e-5 | 2.1e-3 | 8e-2 | | | |

The receiver's estimate from the parity checks follows the true rate
(within a factor of ~3). Tones come back at 67 and 73 dB SINAD, separation
better than 60 dB. On FM, System I's spacing (133 kHz RMS, 7 dB under the
sound subcarrier) gave 9 % errors at 20 dB CNR: the discriminator's noise
at 6.55 MHz. Beside teletext (ohsatv/vbi.py) on AM-VSB 10 and FM 20 at
30 dB, both arrive as each does alone (tests/test_nicam.py,
WithTeletext).

**What a confirming burst should be.** The simulator says only that the
two ends agree with each other and with the standard's numbers; the first
thing to learn off air is whether a real NICAM decoder agrees. Two ways,
both short, both authorised by Brian before anything is keyed:

- **LibreSDR transmitting AM-VSB at 16 MS/s, the nano receiving at 10 MS/s
  8-bit** (both rates carry NICAM; the nano's usb-rx8 profile is AM-VSB at
  10). NICAM on, the test card and the tone (stereo test). Watch: the
  NICAM badge STEREO, its error-rate caption, the 1 and 2 kHz tones in the
  right ears; record IQ for an offline decode. The AM at 10 MS/s puts
  NICAM at 0.37-0.41 fs from the LO, where the AD936x is still flat: the
  first real test of `nicamRx`'s edge.
- **HackRF at 20 MS/s FM-ATV (7 MHz p-p) into libre-2**: the FM case,
  which the simulator says needs ~25 dB CNR; across the bench it will
  have it. Then the same into a domestic NICAM television through an
  attenuator on a 625-line AM-VSB channel, which is the only test that
  says hacktv and we are both right rather than both wrong the same way.

## 23. 625 lines by cable on UHF: System I, the UK channels, and the radios (5 October 2026, for the cable feed)

For the cable feed's UHF band (DESIGN.md "Cable feed"): 625-line PAL with
teletext into a UK 625-line set's aerial socket, by cable. Nothing
radiated; nothing here measured on hardware.

**The channels.**
- BVWS, Steve Pendlebury, "Frequencies and Handy Numbers" (Old Tellys,
  archive.bvws.org.uk/oldtellys/otfrequenc.html): "47 UHF channels, 21 to
  68. Some sets also use Ch69!"; "Vision Carrier = 8N + 303.25 Mhz, where N
  is the channel Number"; "Sound Carrier = Vision Carrier + 6 Mhz. Level is
  10dB down on Vision"; colour subcarrier vision + 4.43 MHz, 17 dB down.
- Wikipedia, "CCIR System I" (its UK UHF table): E21 470.0-478.0 MHz,
  vision 471.25, sound 477.25; E36 590.0-598.0, 591.25, 597.25; E68
  846.0-854.0, 847.25, 853.25; E69 854.0-862.0, 855.25, 861.25. Channels
  35-37 "originally reserved for radio astronomy. However, from 1997 until
  the finish of analogue TV in the UK in 2012, the UK used these channels
  for analogue broadcasts"; channel 69 "was allocated, but never used in
  the UK". **[derived]** Both are 8N + 303.25 and the channel's lower edge
  vision - 1.25 MHz, as BT.470-6 Table 3 (§3.4) has for System I.
- Channel 36 as the UK's VCR / RF-modulator channel: "Channel 36 was (is)
  about 590-600MHz, and wasn't used by any transmitters (neither was 35 or
  37, at least until the 1990s when Channel 5 came out)" (Hacker News,
  news.ycombinator.com/item?id=40704926); Wikipedia "RF modulator": "In
  Europe standard modulators usually used channel 36 (UHF) by default".
  **[unverified as a standard]**: a convention, from secondary sources.
- What UHF is used for now. Ofcom, "Managing the effects of 700 MHz
  clearance on PMSE and DTT viewers" (31 March 2016), 1.1: DTT, PMSE and
  white-space devices "currently use the spectrum between 470 and 790 MHz
  ... re-allocate the frequencies between 694 MHz and 790 MHz (the '700
  MHz band') for mobile data"; ISPreview (August 2020): Ofcom "officially
  completed" the clearance. The 790-862 MHz band (E61-E69) was auctioned
  for mobile broadband in 2013 (Wikipedia, "Wireless microphone
  licensing"). **[derived]** Television is now E21-E48 (470-694 MHz).
- Channel 38: Ofcom, "PMSE - high power PMSE applications in the lower two
  megahertz of Channel 38 (606-614 MHz)"
  (ofcom.org.uk/spectrum/radio-equipment/pmse-channel-38): "the RAS band at
  608-614 MHz"; high-power PMSE only in 606-608 MHz, "two 200 kHz channels
  at 606.7 and 607 MHz at a maximum ERP of 10 W".
- **Chosen [decision, Brian's brief]**: E21-E48 except E38, whole channels
  only, E36 default.

**System I and the modulator** (BT.470-6 Table 3, §3.4, against
`ohsatv/modem.py`):

| | System I | modem.py (AM-VSB defaults) |
| --- | --- | --- |
| channel | 8 MHz, nearest edge -1.25 MHz | occupied -1.25 .. +6.0996 MHz (inside the 8 MHz) |
| main sideband | 5.5 MHz | `amVideoBw` 5.5 MHz, cut by the rate below 12.5 MS/s |
| vestige | 1.25 MHz; >= 20 dB down at -3.0 MHz | 1.25 MHz: flat to -0.75, gone by -1.25 |
| vision modulation | negative; sync 100 %, blanking 76 ± 2 %, white 20 ± 2 % | `AM_SYNC, AM_BLANK, AM_WHITE = 1.0, 0.76, 0.20` |
| sound | F3E, +5.9996 ± 0.0005 MHz, ±50 kHz, 50 us | `SoundFm`: +5.9996 MHz, 50 kHz, 50 us |
| vision : sound | 5/1 e.r.p.; "20:1 is used in the United Kingdom" | -13 dB (20:1) |
| NICAM | +6.552 MHz, DQPSK 728 kbit/s, 100:1 | `ohsatv/nicam.py`, built since (section 22) |

**[derived]** The UK's System I sound is an FM carrier 6 MHz above vision,
and the modulator already makes it: on UHF nothing in the modem changes.
(405 lines' sound is AM, 3.5 MHz *below* vision, and `sysa.py` makes that
separately: §12's sources.) BVWS gives the sound 10 dB down, BT.470-6
note 25 20:1 (13 dB) for the UK; the standard is followed.

**The radios at UHF.**
- Tuning: AD9363 (a stock Pluto) 325-3800 MHz; driver
  `MIN_TX_CARRIER_FREQ_HZ 46875001` otherwise (§5.2). HackRF One 1 MHz-6
  GHz. **[derived]** Every radio here tunes all of E21-E48.
- Output: a stock Pluto about +3.5 dBm at 432 MHz (K0SM, §5.3); AD9361
  datasheet 8 dBm at 800 MHz; attenuation to 89.75 dB in 0.25 dB steps.
  HackRF One: "10 MHz to 2170 MHz: 5 dBm to 15 dBm, generally decreasing
  as frequency increases" (hackrf.readthedocs.io, "HackRF One"); TX VGA
  0-47 dB.
- **[derived] The least output into a set.** Pluto: about -86 to -82 dBm,
  14-22 uV across 75 ohms (22-27 dBuV). Thermal noise in 5.5 MHz: -174 +
  67.4 = -106.6 dBm; with a tuner noise figure of 6-10 dB
  **[unverified: assumed, not looked up for any set]** the CNR is about
  10-16 dB - a noisy picture; teletext needs about 24 dB in our chain
  (docs/teletext-eval.md) and a set's decoder is not likely to need less.
  HackRF at VGA 0, amplifier off: about -42 to -32 dBm, 50-70 dB above the
  noise; a 30-40 dB attenuator leaves it comfortable.

## 24. 625-line AM-VSB's images on a Pluto: the carriers moved (5 October 2026, late; model and recordings, nothing radiated)

**The question.** The 405-line work (21a) left one thing open: the AD936x
model gave 625-line PAL AM-VSB at 10 MS/s - the colour mode on air on 4
October (12, 13a) - an image of the sound carrier about 25 dB under it,
outside the channel, and the transmit panel said nothing. Brian's
decision: move the carriers, as 405 lines at 6.25 MS/s do, so every image
lands where HB1 and the analogue filters take it 40 dB down; where no
placement does, keep the warning and say so on the plan card.

**Why 10 MS/s images and 12.5 does not.** The image of a component f Hz
from the LO is at f - fs (f + fs below it), and HB1's transition band is
symmetric about fs/2. With `rf_bandwidth` = fs the ratio depends only on
f/fs, the same at every rate (`ad936x.Response.image`):

| f / fs | 0.24 | 0.26 | 0.28 | 0.30 | 0.32 | 0.34 | 0.36 | 0.38 | 0.40 | 0.42 | 0.44 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| image, dB under f | -71 | -58 | -48 | -41 | -35 | -29 | -25 | -21 | -17 | -13 | -10 |

So -40 dB needs every carrier within 0.30 fs of the LO. Centred, 625's
signal (-1.25 to +6.1 MHz about vision) puts the vision carrier at -2.42
MHz and the sound at +3.58 MHz: 0.36 fs at 10 MS/s, 0.29 at 12.5. Vision
and sound are 6.0 MHz apart, so at 10 MS/s the only place both are inside
0.30 fs is either side of the LO, at -3.00 and +3.00 MHz - 0.300 fs, -41.0
dB. At 8 MS/s 0.30 fs is 2.4 MHz and nothing gets there.

**The decision in code (ohsatv/modem.py).** `am_plan` keeps the centred
plan wherever every carrier's image is then under `IMAGE_LIMIT_DB` (-40,
now in modem; sysa's is the same number), and otherwise scans every
vision-carrier position that keeps the occupied band inside the usable
0.95 fs and takes the one whose worst image is lowest (`_place`). For two
carriers that is exactly either side of the LO, as `sysa._placement` does
for 405 lines; the scan includes those points. How each part is judged
(`modem.tx_image`):
- **A carrier against itself.** The vision carrier's line falls as the
  picture whitens: the sound's line, -13 dB against peak vision, is 8.8 dB
  under the vision line on the test card, 10.8 on black and only 4.6 on a
  white field (measured below). Judging the sound's image against the
  sound itself covers the white field and an unmodulated sound carrier,
  as 21's 405 figures did (their lines were within 0.8 dB on air).
- **NICAM against peak vision.** NICAM has no line: it is 0.73 MHz of
  noise 20 dB under peak vision. Its image counts its own 20 dB, so at 10
  MS/s it can sit at +3.55 MHz (0.355 fs, 26 dB under NICAM, 46 under
  vision) beside the carriers rather than pull them off their best place.
- **Not the picture.** At the band's edges the picture's sidebands are
  far weaker than the carriers; in the whole-signal model below, away
  from the carriers' lines, their images are under -55 dBc wherever the
  carriers' are under the limit (tests/test_chain.py asserts it).

The scan costs ~2 ms vectorised, and the engine asks for its plan several
times a block, so it is cached (`_place_cached`): uncached, with a Python
loop per candidate, it took 128 ms a plan, and playback of the 4 October
captures fell from 24 to 7 decoded frames a second before that was seen.

Where a Pluto would still image a carrier above the limit, the plan says
so in `why` (the plan card) - "a Pluto (AD936x) sending this puts an image
of the sound carrier -2.00 MHz from vision, outside the channel, about 11
dB under it; no placement at 8 MS/s gets it 40 dB under" - and
`sysa.tx_image_warning` now warns on a Pluto for **any** AM plan with a
`txImage` over the limit, 405 or 625, the image's frequency named and
whether it falls on the picture or outside the channel.

**Before and after** - every 625-line AM-VSB plan at every rate a Pluto
uses, the model (`modem.tx_image`, dB under the carrier it images; NICAM's
under peak vision) and the whole signal built by the program's own encoder
and modulator (the test card, the 1 kHz tone or the stereo test, teletext
on 32 lines where the plan carries it, the transmitter's edge correction),
interpolated as the chip does (`tests/helpers.ad936x_tx_spectrum`: TX HB1
at 2 fs from ADI's taps, HB2, HB3, the DAC's hold, the analogue filters),
in 1 kHz bins against the vision carrier's bin. Scratch `model625.py`.

| Rate | Plan | Vision carrier from the LO | Worst carrier image (model) | Strongest outside the channel, whole signal |
| --- | --- | --- | --- | --- |
| 5 MS/s | mono (no room for sound, NICAM or teletext) | -0.575, unchanged | vision -90.7 | -58.5 dBc (the picture's edge) |
| 6.25 | mono (as 5) | -0.875, unchanged | vision -104.8 | -60.4 |
| 8 | **with sound** (mono picture, teletext fits) | -2.425 → **-2.550** | sound **-8.4 → -11.1**; vision -40.0 → -35.2 | -29.7 → -32.3 at vision -1.98 MHz (the sound's image) - **warned** |
| 8 | without sound | -1.295, unchanged | vision -91.4 | -63.9 |
| 10 | **with sound** (the 4 October mode) | -2.425 → **-3.000** | sound **-25.3 → -41.0**; vision -68.8 → -41.0 | -46.5 → -41.0 (see below) |
| 10 | with sound and NICAM | -2.858 → **-3.000** | sound **-36.5 → -41.0**; NICAM -42.7 → -46 | -46.1 → -41.0 |
| 10 | without sound | -1.775, unchanged | vision -94.9 | -67.3 |
| 10 | NICAM, no sound | -2.858, unchanged | NICAM -42.7, vision -46.1 | -46.1 |
| 12.5 | with sound | -2.425, unchanged | sound -46.0 | -66.9 |
| 12.5 | with sound and NICAM | -2.858, unchanged | NICAM -62.5 | -74.6 |
| 12.5 | without sound / NICAM only | -2.125 / -2.858, unchanged | -91.9 / -62.5 | -72.2 / -75.6 |
| 16 | every combination | unchanged | -80.6 or lower | -73 or lower |

Teletext changes nothing anywhere by more than 0.4 dB: it is in the
picture's top, which every placement keeps well inside the band. The
whole-signal images of each carrier agree with the model to 0.1 dB.

**What "dBc" means here, and the one row that reads worse.** The last
column is 21's measure: 1 kHz bins against the vision carrier's line, with
the 1 kHz tone on the sound. The tone spreads the FM sound over ±50 kHz,
so its bin - and its image's - is 17 dB under the unmodulated line; by that
measure the old 10 MS/s plan was -46.5 dBc and the new one, whose worst is
now the vision carrier's own image, -41.0. With the sound silent (an
unmodulated line) the same column, measured on three pictures:

| 10 MS/s, sound silent | sound line vs vision | Centred: sound's image | Placed: sound's image | Placed: vision's image |
| --- | --- | --- | --- | --- |
| test card | -8.8 dB | -34.1 dBc | -49.8 | -41.0 |
| white field | -4.6 | **-29.9** | -45.6 | -41.0 |
| black field | -10.8 | -36.2 | -51.8 | -41.0 |

So the old plan's worst case was about -30 dBc against the vision line,
and -25 against the sound carrier itself; the new plan's worst is -41.0
against either, in every case, at the cost of the vision carrier's image
rising from -69 to -41 - under the limit by 1.0 dB. A placement weighing
the sound 4.6 dB lighter than vision would gain about 2 dB on the vision
image; the conservative rule was kept because it is the one 405 lines
use and does not depend on the picture.

**8 MS/s with sound** cannot be fixed by placement: the occupied band is
7.35 MHz of the 7.6 the rate carries, the vision carrier can go no lower
than -2.55 MHz, and the sound then sits at +3.45 (0.43 fs). The plan
card and the transmit panel say so. No profile uses AM at 8 MS/s (the USB
profiles run 5, the others 10, the HackRF 20); whether 625 lines at 8 MS/s
should go without sound on a Pluto, as 405 lines at 5 do, is Brian's.

**A HackRF at 10 MS/s** (not a profile: its profile is 20 MS/s, centred
and unchanged) images worse with the carriers placed for an AD936x: the
signal now reaches ±4.25 MHz, its driver widens the MAX2837 filter from 8
to 10 MHz, and with 21a's 20 dB shortfall both carriers' images model at
the carriers' own level (centred: -3 and -18). Its warning fires either
way; the plan is the same for every radio because both ends must agree
on it.

**The receive side.** The receiver's LO is set from the plan (`Radio._lo`),
so a receiver running this build puts the vision carrier where it expects
it whatever placement the transmitter used - the RF frequency is what both
ends share - and live interop with an older OpenHamShack or anyone else is
unaffected. What does depend on the placement:
- *The demodulator* (`AmVsbDemod`) designs its receive filter, sound
  demodulator and NICAM receiver from the plan, and `track()` follows the
  carrier within ±1 MHz. At 10 MS/s the chain tests (PSNR, colour, SINAD,
  NICAM's BER, teletext beside NICAM through two AD936x) pass at the new
  placement unchanged; `test_am_vsb_at_10_msps_through_two_plutos`, whose
  check is the sound-to-vision ratio and so needs the carriers to lose
  different amounts, runs the centred placement on purpose.
- *Format recognition* (`sysa.detect`) is given the tracked carrier; the
  625↔405 retune follows each format's plan.
- *NICAM* at 10 MS/s: +3.55 MHz from the LO, its band to 3.92 MHz, inside
  `RX_EDGE` (0.45 fs); `nicamRx` holds.
- *Recordings* hold the samples where the radio put them. A recording now
  says where the receiver looked (`openhamshack:placement`); one without it -
  everything before this change - plays with `placement="centred"`, the
  old default (`Radio._rx_params`, applied only to the receiver: what would
  be sent is placed for now). 405 lines are not affected: their old
  recordings were all at 16 MS/s, where both rules centre.

The 4 October captures (`rx8-am-b1`, `-b2`: libre-2 transmitting this
mode at 10 MS/s, the nano receiving 8-bit; metadata with no rx_freq,
params or placement) played back through the engine, 11 s each,
recognition on (scratch `play625.py`):

| | rxFreq shown | carrierHz | locked records | colour | SNR | SINAD |
| --- | --- | --- | --- | --- | --- | --- |
| b1, main be9fa4b | 1251.000 | +18.75 kHz | 10/11 | 10 | 10.8 | 21.0 |
| b1, this branch | 1251.000 | +18.75 kHz | 10/11 | 10 | 10.9 | 21.0 |
| b1, metadata saying "images" | **1250.425** | **+593.75 kHz** | 11/11 | 11 | 10.8 | 24.9 |
| b2, main | 1251.000 | +18.75 kHz | 10/11 | 10 | 18.1 | 26.5 |
| b2, this branch | 1251.000 | +18.75 kHz | 11/11 | 9 | 18.0 | 22.3 |
| b2, metadata saying "images" | **1250.425** | **+593.75 kHz** | 10/11 | 9 | 18.0 | 23.5 |

This branch replays them exactly as main does (the record-to-record
differences are where the 1 s windows fall). Read with the new placement
the picture still locks - `track()` finds the carrier 0.594 MHz from
where it is looked for - but the tuning and carrier offset shown are wrong
by that much, the receive filter and the sound's channel are designed
around the wrong place until it does, and NICAM's `nicamRx` is decided
for the wrong band. Hence the fallback.

**FM-ATV on a Pluto.** FM has a constant envelope centred on the LO, so
nothing can be placed; what images is whatever sits near fs/2 - the
Carson edge and a subcarrier's sidebands. Modelled the same way (scratch
`modelfm.py`; the whole signal from `systems.build_loop` with the
transmitter's correction; everything beyond ±fs/2 is image):

| Plan | Carson / fs | Strongest image, 100 kHz bins, dBc | Where (from the LO) | 1 kHz bins, vs the strongest in-band bin |
| --- | --- | --- | --- | --- |
| USB 5 MS/s, 1.2 MHz p-p, 1.6 MHz video | 0.88 | -73.4 | +2.60 | -70.6 |
| Mini-MAC, 5 MS/s | 0.88 | -59.7 | +2.80 | -56.3 |
| **Low-PAL, 5 MS/s** (2.2 MHz subcarrier, 0.44 fs) | 0.90 | **-37.4** | -2.80 | **-38.4** |
| 6.25 MS/s, 1.2 MHz p-p | 0.70 | -87.4 | -3.70 | -84.5 |
| 8 MS/s, 2 MHz p-p, 2.5 MHz video | 0.88 | -70.1 | -4.30 | -70.6 |
| USB_TX8 10 MS/s, 3 MHz p-p, no subcarrier | 0.90 | -71.1 | -5.20 | -69.8 |
| **USB_RX8 12.5 MS/s, 1.2 p-p, 5.5 MHz subcarrier** (0.44 fs) | 0.94 | **-38.8** | -7.00 | **-39.5** |
| ETH16 16 MS/s, 4 p-p, 6.0 MHz subcarrier | 0.93 | -53.2 | +9.80 | -53.6 |
| 16 MS/s with NICAM, 3.2 p-p | 0.95 | -50.8 | +9.40 | -52.4 |
| 20 MS/s, 7 p-p, 6.0 MHz subcarrier | 0.89 | -56.1 | +11.10 | -56.7 |
| 20 MS/s with NICAM | 0.95 | -55.9 | +11.10 | -56.5 |

Two plans come out 1-2.6 dB over -40, both by a subcarrier at 0.44 fs
whose sidebands the transmitter lifts by what its filters take off them
(17: +3.6 dB at 12.5 MS/s, so 17's "-42 uncorrected, -39 corrected" is
this row). Not changed: FM cannot move its carrier, the 12.5 MS/s plan is
a receive profile (the nano transmits at 5; keying above 5.2 MS/s on USB
is refused), Low-PAL's 2.2 MHz subcarrier is its format, and the margin
is within what the measure itself moves (100 kHz against 1 kHz bins).
Options for Brian, if wanted: carry `txImage` on FM plans and warn as AM
does, or hold the transmitter's subcarrier boost to what keeps its image
under -40 (it would give back ~1.5 dB of the 3.6 dB 17 restored).

**Decided (6 October 2026): warn, change nothing.** FM plans now carry
`txImage` (`modem.fm_tx_image`), and the plan card and transmit panel warn
over -40 dBc as for AM. It needs no spectrum: a subcarrier's first
sideband is J1 of its index (the transmit correction included) times the
carrier's spectrum, whose strongest 100 kHz sits 3.4-3.9 dB under the
whole in every row above (`FM_SPREAD_DB` -3.5), taken through
`ad936x.Response`'s image ratio. NICAM's band is integrated 10 kHz at a
time, with no spread. Low-PAL at 5 MS/s: -37.5 (whole signal -37.4);
12.5 MS/s with 5.5 MHz: -38.6 (-38.8); ETH16: -52.8 (-53.2); 16 MS/s with
NICAM: -48.6 (-50.8, so 2 dB on the side of warning). Plans with no
subcarrier and no NICAM have none: the picture's own Carson edge is not
modelled, and was at most -56 dBc above. A HackRF is judged on its own
model (converter hold, libhackrf's default filter, the 20 dB shortfall, no
correction): its 20 MS/s profile is -47.2 with or without NICAM, Low-PAL at
5 MS/s -31.5, 16 MS/s with a 6 MHz subcarrier -37.0. 0.15-0.4 ms a plan
uncached; cached as `_place` is.

**Still owed.** All of this is the model and the simulator; 21's 405
measurement (13.4 dB against 13.0 modelled) is the only on-air check of it.

**What a confirming burst should be** (Brian's authorisation; not run):
libre-2 transmitting 625-line PAL AM-VSB, 10 MS/s, colour, sound, test
card with GM4BRI, vision 1251.000 MHz, -24 dB as on 4 October, into the
nano receiving 8-bit at **12.5 MS/s** with manual gain (levels must
compare across retunes), IQ recorded 15 s per view. Two transmit builds,
one over each: main be9fa4b (centred) and this branch (placed). Each over
with the tone off for 5 s (the sound a bare line) and on for 10.
- **Two receive views**, because the 12.5 MS/s window cannot hold both
  images: LO 1250.000 (1243.75-1256.25: vision and the sound's image at
  **1247.000**) and LO 1258.000 (1251.75-1264.25: the sound at 1257.000
  and the vision carrier's image at **1261.000**). Neither image is
  aliased onto by the receiver's own folding at those LOs (the sound folds
  to 1244.5 in the first, vision to 1263.5 in the second). The sound line,
  in both views, bridges them.
- **Expect**, tone off, against the vision line: centred, the sound's image
  at 1247.000 about **-34 dBc** and the vision's at 1261.000 about -69;
  placed, **-50 and -41**. Against the sound line: -25 → -41 for its image.
  The images do not move in frequency - an image is always fs from its
  carrier - only in level.
- **And on the picture:** the placed over decodes as the centred one did
  (colour, and SNR and SINAD as the 4 October captures replay: SNR 10.9
  and 18.0 dB), the engine's carrierHz near the 19 kHz offset seen then,
  not 594 kHz.
- A second placed over with NICAM on, if the nano's NICAM badge is wanted
  too: NICAM at 1257.552 MHz, its image 46 dB under peak vision at
  1247.55 (in the first view, 0.55 MHz from the sound's).
