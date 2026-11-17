#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generate amdgpu_chips.rs beside this file, then format it with rustfmt.

FIRMWARE is a linux-firmware checkout at recipes::AMDGPU_REVISION; OUT is the
Rust file to write. Each chip's members are the files whose names start with
one of its prefixes; every prefix must match a file.

Usage: python3 -I amdgpu_chips.py FIRMWARE OUT
"""
import hashlib, os, re, sys
fw, out = sys.argv[1], sys.argv[2]
files = sorted(f for f in os.listdir(os.path.join(fw, 'amdgpu')) if f.endswith('.bin'))
STRIX = ['psp_14_0_0_', 'psp_14_0_1_', 'psp_14_0_4_', 'psp_14_0_5_', 'sdma_6_1_0', 'sdma_6_1_1',
         'sdma_6_1_2', 'sdma_6_1_3', 'sdma_6_1_4', 'vcn_4_0_5', 'vcn_4_0_6', 'dcn_3_5_', 'dcn_3_6_',
         'vpe_6_1_0', 'vpe_6_1_1', 'vpe_6_1_3']
NAVI3 = ['dcn_3_2_0_', 'dcn_3_2_1_']
# (constant, chip, family label, doc, member prefixes)
CHIPS = [
 ('AMDGPU_NAVI22', 'navi22', 'Navi 22', 'Navi 22 (RX 6700, 6700 XT, 6750 XT), CHIP_NAVY_FLOUNDER: every file of its name', ['navy_flounder_']),
 ('AMDGPU_NAVI23', 'navi23', 'Navi 23', 'Navi 23 (RX 6600, 6600 XT, 6650 XT), CHIP_DIMGREY_CAVEFISH: every file of its name', ['dimgrey_cavefish_']),
 ('AMDGPU_NAVI24', 'navi24', 'Navi 24', 'Navi 24 (RX 6400, 6500 XT), CHIP_BEIGE_GOBY: every file of its name', ['beige_goby_']),
 ('AMDGPU_VANGOGH', 'vangogh', 'Van Gogh', 'Van Gogh (the Steam Deck APU), CHIP_VANGOGH: every file of its name', ['vangogh_']),
 ('AMDGPU_REMBRANDT', 'rembrandt', 'Rembrandt', 'Rembrandt (Ryzen 6000 APUs, Radeon 660M/680M), CHIP_YELLOW_CARP: every file of its name', ['yellow_carp_']),
 ('AMDGPU_RAPHAEL', 'raphael', 'Raphael', 'Raphael (Ryzen 7000 desktop graphics): GC 10.3.6, PSP 13.0.5, SDMA 5.2.6, VCN 3.1.2 and DCN 3.1.5', ['gc_10_3_6_', 'psp_13_0_5_', 'sdma_5_2_6', 'vcn_3_1_2', 'dcn_3_1_5_']),
 ('AMDGPU_MENDOCINO', 'mendocino', 'Mendocino', 'Mendocino (Ryzen 7020 APUs): GC 10.3.7, PSP 13.0.8, SDMA 5.2.7, VCN 3.1.2 and DCN 3.1.6', ['gc_10_3_7_', 'psp_13_0_8_', 'sdma_5_2_7', 'vcn_3_1_2', 'dcn_3_1_6_']),
 ('AMDGPU_NAVI31', 'navi31', 'Navi 31', 'Navi 31 (RX 7900 GRE, XT and XTX): GC 11.0.0, PSP and SMU 13.0.0, SDMA 6.0.0, VCN 4.0.0 and DCN 3.2', ['gc_11_0_0_', 'psp_13_0_0_', 'smu_13_0_0', 'sdma_6_0_0', 'vcn_4_0_0'] + NAVI3),
 ('AMDGPU_PHOENIX', 'phoenix', 'Phoenix', 'Phoenix (Ryzen 7040 and 8040 APUs, Radeon 760M/780M): GC 11.0.1, PSP 13.0.4, SDMA 6.0.1, VCN 4.0.2 and DCN 3.1.4', ['gc_11_0_1_', 'psp_13_0_4_', 'sdma_6_0_1', 'vcn_4_0_2', 'dcn_3_1_4_']),
 ('AMDGPU_NAVI33', 'navi33', 'Navi 33', 'Navi 33 (RX 7600, 7600 XT): GC 11.0.2, PSP and SMU 13.0.7, SDMA 6.0.2, VCN 4.0.4 and DCN 3.2', ['gc_11_0_2_', 'psp_13_0_7_', 'smu_13_0_7', 'sdma_6_0_2', 'vcn_4_0_4'] + NAVI3),
 ('AMDGPU_NAVI32', 'navi32', 'Navi 32', 'Navi 32 (RX 7700 XT, 7800 XT): GC 11.0.3, PSP and SMU 13.0.10, SDMA 6.0.3, VCN 4.0.0 and DCN 3.2', ['gc_11_0_3_', 'psp_13_0_10_', 'smu_13_0_10', 'sdma_6_0_3', 'vcn_4_0_0'] + NAVI3),
 ('AMDGPU_PHOENIX2', 'phoenix2', 'Phoenix 2', 'Phoenix 2 (Ryzen 7040 and 8040 APUs, Radeon 740M): GC 11.0.4, PSP 13.0.11, SDMA 6.0.1, VCN 4.0.2 and DCN 3.1.4', ['gc_11_0_4_', 'psp_13_0_11_', 'sdma_6_0_1', 'vcn_4_0_2', 'dcn_3_1_4_']),
 ('AMDGPU_STRIX_POINT', 'strix-point', 'Strix Point', 'Strix Point (Ryzen AI 300, Radeon 880M/890M): GC 11.5.0, with every PSP 14.0, SDMA 6.1, VCN 4.0.5/4.0.6, DCN 3.5/3.6 and VPE 6.1 file of the RDNA 3.5 APUs', ['gc_11_5_0_'] + STRIX),
 ('AMDGPU_STRIX_HALO', 'strix-halo', 'Strix Halo', 'Strix Halo (Ryzen AI Max, Radeon 8050S/8060S): GC 11.5.1, with the RDNA 3.5 APUs\' other files as for Strix Point', ['gc_11_5_1_'] + STRIX),
 ('AMDGPU_KRACKAN', 'krackan', 'Krackan Point', 'Krackan Point (Ryzen AI 5/7 300, Radeon 840M/860M): GC 11.5.2, with the RDNA 3.5 APUs\' other files as for Strix Point', ['gc_11_5_2_'] + STRIX),
 ('AMDGPU_NAVI44', 'navi44', 'Navi 44', 'Navi 44 (RX 9060 XT): GC 12.0.0, PSP and SMU 14.0.2, SDMA 7.0.0, VCN 5.0.0 and DCN 4.0.1', ['gc_12_0_0_', 'psp_14_0_2_', 'smu_14_0_2', 'sdma_7_0_0', 'vcn_5_0_0', 'dcn_4_0_1_']),
]
def members(prefixes):
    found = [f for f in files if any(f.startswith(p) for p in prefixes)]
    for p in prefixes:
        assert any(f.startswith(p) for f in found), p
    return found
lines = ['// SPDX-License-Identifier: GPL-2.0-or-later',
 '//! The files amdgpu asks for on the RDNA 2, 3, 3.5 and 4 chips besides',
 '//! Navi 21 and Navi 48, from linux-firmware [`super::AMDGPU_REVISION`].',
 '//! Generated from that commit by `amdgpu_chips.py` beside this file: each',
 '//! length and digest is its file\'s.',
 '//!',
 '//! A chip named after its codename (Navi 22-24, Van Gogh, Rembrandt) carries',
 '//! every file of that name. A chip amdgpu names by its IP blocks carries every',
 '//! file of its graphics core and the PSP, SMU, SDMA, VCN, DCN and VPE files of',
 '//! the versions its family uses; where a family\'s chips differ in a block',
 '//! version, each carries all of them, as amdgpu asks only for its own. None of',
 '//! these chips has run under Stela yet: Navi 48 and Navi 21 have.',
 'use super::{Recipe, amdgpu};', '']
total = {}
for const, chip, label, doc, prefixes in CHIPS:
    lines.append(f'/// {doc}.')
    lines.append(f'pub const {const}: &[Recipe] = &[')
    for f in members(prefixes):
        data = open(os.path.join(fw, 'amdgpu', f), 'rb').read()
        total[f] = len(data)
        lines.append(f'    amdgpu(\n        "amdgpu/{f}",\n        {len(data)},\n        "{hashlib.sha256(data).hexdigest()}",\n    ),')
    lines.append('];')
    lines.append('')
open(out, 'w').write('\n'.join(lines))
print(len(total), 'distinct files', sum(total.values()), 'bytes')
for const, chip, *_ in CHIPS: print(const, len(members(_[2])))
