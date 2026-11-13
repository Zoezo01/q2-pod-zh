# Q2 hardware

What's known about the Shanling Q2's hardware: Shanling's published specs, what the stock firmware shows of the parts inside, and what Shanling has said about build tolerances and warranty.

## Specifications

Shanling's figures, as retailers and the launch press list them ([Audiophonics](https://www.audiophonics.fr/en/dap-digital-audio-player-hifi/shanling-q2-black-p-22390.html), [Phile-web](https://www.phileweb.com/sp/news/d-av/202609/18/65987.html)).

| | |
|---|---|
| SoC | Ingenic X2000 |
| DAC | 4 × Cirrus Logic CS43131 |
| Decoding | PCM to 768 kHz / 32-bit, DSD512 |
| Screen | 2.09 in touchscreen, 320 × 375 |
| Controls | Touch wheel, Centre button, four side buttons |
| Storage | microSD, up to 2 TB; no user storage inside |
| Battery | 1850 mAh: 13.5 h on 3.5 mm, 12.5 h on 4.4 mm, 16.5 h on Bluetooth (LDAC) |
| Bluetooth | 5.2, send and receive; LDAC, aptX HD, aptX, AAC, SBC |
| Wi-Fi | Yes (AirPlay, DLNA in stock firmware) |
| USB | USB-C: charging, USB DAC to 384 kHz / 32-bit, digital out |
| Size, weight | 80 × 50 × 13.5 mm, 83 g; aluminium and glass |
| Colours | Smoke grey, silver, orange |

| Output | 3.5 mm | 4.4 mm balanced |
|---|---|---|
| Power @ 32 Ω, low / high gain | 30 / 120 mW | 120 / 425 mW |
| Line out | 1.9 Vrms | 3.8 Vrms |
| SNR, dynamic range @ 32 Ω | 125 dB | 130 dB |
| THD+N @ 32 Ω | 0.0005% | 0.0006% |
| Crosstalk @ 32 Ω | 65 dB | 110 dB |
| Output impedance | 1.4 Ω | 1.8 Ω |
| Frequency response | 20 Hz–40 kHz (±1 dB) | 20 Hz–40 kHz (±1 dB) |

## Inside, as the firmware shows it

Read from the stock rootfs and kernel modules; [internals.md](internals.md) has the detail behind each line.

- **SoC:** Ingenic X2000 (dual XBurst2 MIPS32r5 cores plus an XBurst0 MCU), running Shanling's vendor kernel, Linux 5.10.21. The [X2000 programming manual](https://whycan.com/files/members/3946/X2000_PM_20200515.pdf) covers its registers ([mcu-sleep.md](mcu-sleep.md#hardware-basis), [clock-gating.md](clock-gating.md)).
- **Speaker:** the click is a buzzer driven by the X2000's MCU (`cmd_mcu write_str buzzer`), not the DAC ([Key Tone](internals.md#key-tone)).
- **DAC:** `codec_cs43131.ko`, reached through `/dev/shanling_dac`. Its mute line lets go 90-100 ms after an unmute; the volume comes back on the DAC's soft ramp ([CS43131 product page](https://www.cirrus.com/products/cs43131/)).
- **Jacks:** headphone detect on GPIO `PA07` (3.5 mm) and `PA08` (4.4 mm).
- **Wi-Fi and Bluetooth:** one Realtek RTL8733BS module (`8733bs`, `module_driver/rtl8733bs.sh`), which also holds Bluetooth's power line (`gpio_bt_dis_n`). If the module fails, both go: stock shows **Bluetooth abnormal** and reboots at every start.
- **Touch wheel:** read with tslib by an encoder thread: position 1-200 per turn, plus pressure. Q2 Pod makes a tick every 28 units by default.
- **Clock:** a battery-backed RTC (`rtc0`) with a wake alarm, used by the Standby limit.
- **USB:** the card is exported through the kernel's `f_mass_storage` in a configfs gadget. The stock kernel has no `hid-generic`, so USB keyboards and remotes are not seen.

## Build tolerances and warranty

From Shanling, in reply to owners' reports (Q2 Pod Discord, 2026-11-13):

- **A Centre button that wiggles or rattles is normal.** The wheel and the Centre button come from two different suppliers, and the tolerance lets the button move slightly. Shaken near the ear, nearly every unit rattles a little or more. Shanling does not treat it as a warranty fault.
- **A sticking wheel is a fault.** Amazon has seen a few cases, caused by the mounting tape under the button not being aligned. It is covered under warranty.

## Opening it

- [Replacing the battery](battery.md), with Shanling's tips and a step-by-step PDF.
- [Teardown photo](teardown.jpg).
- If a Q2 won't start, use Shanling's recovery package ([Restoring stock firmware](../README.md#restoring-stock-firmware)).
