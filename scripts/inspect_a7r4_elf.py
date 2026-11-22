#!/usr/bin/env python3
"""Read-only ELF32 ARM analyzer for A6400-Extra-Menu -> ILCE-7RM4A (Ver. 1.12).

Inspects pulled camera ELF/SO binaries (im.elf, libObj.so, viewUnified2.so,
libc-*.so, libdl-*.so, mpr_monio.elf, mwf_monio.elf) without external binutils:
  1. File size, SHA-256, 32-bit FNV-1a hash (matching A6400-Extra-Menu).
  2. ELF32 ARM header, PT_INTERP, PT_LOAD segments, DT_NEEDED, ABI flags.
  3. GNU symbol versions (.gnu.version_r) vs glibc <= 2.11.2 constraint.
  4. The 19 mangled C++ UI symbols from camera/native_film_ui_trial3.c:145-163
     plus loader/probe symbols (dlopen, dlsym, sys_getInstance, vtable/input).
  5. Zero-padding code caves (>= 2296 B in r-xp) & state/scratch space
     (>= 1760 B / >= 1184 B in rw-p).
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile

# Constants matching A6400-Extra-Menu
FNV_OFFSET = 2166136261
FNV_PRIME = 16777619
MAX_GLIBC = (2, 11, 2)
DEFAULT_MIN_CODE_CAVE = 2296
DEFAULT_MIN_STATE_CAVE = 1760
STATE_STRUCT_SIZE = 1184

# 19 mangled C++ symbols from camera/native_film_ui_trial3.c:145-163
UI_TRIAL3_SYMBOLS = [
    ("_ZN2ux6wgtsys6WidgetC1Ev", 0x5ED5AD, "Widget::Widget()"),
    ("_ZN9GEN_PanelC1Ev", 0x4149AD, "GEN_Panel::GEN_Panel()"),
    ("_ZN9GEN_LabelC1Ev", 0x3C8D41, "GEN_Label::GEN_Label()"),
    ("_ZN2ux6wgtsys6Widget10setLayerIDERKNS_4core2IDE", 0x5ED50D, "Widget::setLayerID"),
    ("_ZN2ux6wgtsys6Widget11setLocalPosERKNS_4core7Vector2E", 0x5ED45D, "Widget::setLocalPos"),
    ("_ZN9GEN_Panel7setSizeEff", 0x414DEF, "GEN_Panel::setSize"),
    ("_ZN9GEN_Label7setSizeEff", 0x3C9381, "GEN_Label::setSize"),
    ("_ZN9GEN_Label13setTextDirectEPKc", 0x3C88CF, "GEN_Label::setTextDirect"),
    ("_ZN9GEN_Label7setFontEj", 0x3C89AF, "GEN_Label::setFont"),
    ("_ZN9GEN_Panel14setColorDirectERKN6stella6CColorE", 0x4148E5, "GEN_Panel::setColorDirect"),
    ("_ZN2ux6wgtsys5Layer9setActiveEb", 0x5EF12F, "Layer::setActive"),
    ("_ZN2ux6wgtsys5Layer8setClearEb", 0x5EF159, "Layer::setClear"),
    ("_ZN2ux6wgtsys10WidgetBase10setAnimateEb", 0x606951, "WidgetBase::setAnimate"),
    ("_ZN2ux6wgtsys9LayerBase8moveLastEv", 0x601331, "LayerBase::moveLast"),
    ("_ZN2ux4core2ID5genIDEPKc", 0x63DCD9, "ID::genID"),
    ("_ZN2ux6wgtsys12WidgetSystem11createLayerERKNS_4core2IDE", 0x5F0F49, "WidgetSystem::createLayer"),
    ("_ZNK2ux6wgtsys12WidgetSystem24getMousePosInFramebufferEhRsS2_", 0x5F1019, "WidgetSystem::getMousePosInFramebuffer"),
    ("_ZNK2ux6wgtsys12WidgetSystem13getMouseStateEhh", 0x5F1045, "WidgetSystem::getMouseState"),
    ("_ZN10InfraInput20GetPhyKeyStatusCacheEiP21_InfraInputPhyKeyData", 0x860FA9, "InfraInput::GetPhyKeyStatusCache"),
]

EXTRA_SYMBOLS = [
    ("_ZN2ux6wgtsys12WidgetSystem15sys_getInstanceEv", "WidgetSystem::sys_getInstance"),
    ("_ZTVN2ux6wgtsys6WidgetE", "vtable for Widget"),
    ("_ZTVN2ux6wgtsys10WidgetBaseE", "vtable for WidgetBase"),
    ("dlopen", "libdl dlopen"),
    ("dlsym", "libdl dlsym"),
    ("dlclose", "libdl dlclose"),
]


def fnv1a_32(data: bytes) -> int:
    h = FNV_OFFSET
    for b in data:
        h = ((h ^ b) * FNV_PRIME) & 0xFFFFFFFF
    return h


def read_cstr(buf: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(buf):
        return ""
    end = buf.find(b"\x00", offset)
    if end < 0:
        end = len(buf)
    return buf[offset:end].decode("utf-8", errors="replace")


def parse_version_tuple(name: str):
    m = re.fullmatch(r"GLIBC_(\d+(?:\.\d+)*)", name)
    if not m:
        return None
    return tuple(int(part) for part in m.group(1).split("."))


def parse_elf32_arm(data: bytes) -> dict:
    if len(data) < 52 or data[:4] != b"\x7fELF":
        raise ValueError("Not an ELF file")
    ei_class, ei_data, ei_version = data[4], data[5], data[6]
    if ei_class != 1 or ei_data != 1:
        raise ValueError(f"Unsupported ELF class/endianness: class={ei_class}, data={ei_data} (expected ELF32 LSB)")

    (
        e_type,
        e_machine,
        e_version,
        e_entry,
        e_phoff,
        e_shoff,
        e_flags,
        e_ehsize,
        e_phentsize,
        e_phnum,
        e_shentsize,
        e_shnum,
        e_shstrndx,
    ) = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)

    if e_machine != 40:
        raise ValueError(f"Not an ARM ELF (e_machine={e_machine}, expected 40)")

    elf_types = {1: "ET_REL", 2: "ET_EXEC", 3: "ET_DYN", 4: "ET_CORE"}
    abi_flags = []
    if e_flags & 0x400:
        abi_flags.append("hard-float")
    if e_flags & 0x200:
        abi_flags.append("soft-float")
    eabi_ver = (e_flags >> 24) & 0xFF
    if eabi_ver:
        abi_flags.append(f"EABI{eabi_ver}")

    phdrs = []
    interp = None
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if off + 32 > len(data):
            break
        p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = struct.unpack_from("<IIIIIIII", data, off)
        perms = (
            ("r" if p_flags & 4 else "-")
            + ("w" if p_flags & 2 else "-")
            + ("x" if p_flags & 1 else "-")
        )
        ph = {
            "index": i,
            "type": p_type,
            "offset": p_offset,
            "vaddr": p_vaddr,
            "filesz": p_filesz,
            "memsz": p_memsz,
            "flags": p_flags,
            "perms": perms,
            "align": p_align,
        }
        phdrs.append(ph)
        if p_type == 3 and p_filesz > 0:  # PT_INTERP
            interp = read_cstr(data[p_offset : p_offset + p_filesz], 0)

    shdrs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        if off + 40 > len(data):
            break
        (
            sh_name,
            sh_type,
            sh_flags,
            sh_addr,
            sh_offset,
            sh_size,
            sh_link,
            sh_info,
            sh_addralign,
            sh_entsize,
        ) = struct.unpack_from("<IIIIIIIIII", data, off)
        shdrs.append(
            {
                "index": i,
                "name_off": sh_name,
                "name": "",
                "type": sh_type,
                "flags": sh_flags,
                "addr": sh_addr,
                "offset": sh_offset,
                "size": sh_size,
                "link": sh_link,
                "info": sh_info,
                "align": sh_addralign,
                "entsize": sh_entsize,
            }
        )

    if 0 <= e_shstrndx < len(shdrs):
        shstr = shdrs[e_shstrndx]
        shstr_bytes = data[shstr["offset"] : shstr["offset"] + shstr["size"]]
        for sh in shdrs:
            sh["name"] = read_cstr(shstr_bytes, sh["name_off"])

    needed = []
    soname = None
    for sh in shdrs:
        if sh["type"] == 6 and sh["entsize"] >= 8:  # SHT_DYNAMIC
            strtab_sh = shdrs[sh["link"]] if sh["link"] < len(shdrs) else None
            strtab = data[strtab_sh["offset"] : strtab_sh["offset"] + strtab_sh["size"]] if strtab_sh else b""
            for off in range(sh["offset"], sh["offset"] + sh["size"], sh["entsize"]):
                d_tag, d_val = struct.unpack_from("<Ii", data, off)
                if d_tag == 0:
                    break
                if d_tag == 1 and strtab:
                    needed.append(read_cstr(strtab, d_val))
                elif d_tag == 14 and strtab:
                    soname = read_cstr(strtab, d_val)

    verneed = {}
    glibc_versions = []
    glibc_defined = []
    glibc_violations = []
    for sh in shdrs:
        if sh["type"] == 0x6FFFFFFE:  # SHT_GNU_verneed
            strtab_sh = shdrs[sh["link"]] if sh["link"] < len(shdrs) else None
            strtab = data[strtab_sh["offset"] : strtab_sh["offset"] + strtab_sh["size"]] if strtab_sh else b""
            pos = sh["offset"]
            end = sh["offset"] + sh["size"]
            while pos + 16 <= end:
                vn_version, vn_cnt, vn_file, vn_aux, vn_next = struct.unpack_from("<HHIII", data, pos)
                lib_name = read_cstr(strtab, vn_file)
                aux_pos = pos + vn_aux
                versions = []
                for _ in range(vn_cnt):
                    if aux_pos + 16 > end:
                        break
                    vna_hash, vna_flags, vna_other, vna_name, vna_next = struct.unpack_from("<IHHII", data, aux_pos)
                    ver_name = read_cstr(strtab, vna_name)
                    versions.append(ver_name)
                    vt = parse_version_tuple(ver_name)
                    if vt is not None:
                        glibc_versions.append(ver_name)
                        if vt > MAX_GLIBC:
                            glibc_violations.append((lib_name, ver_name))
                    if vna_next == 0:
                        break
                    aux_pos += vna_next
                verneed[lib_name] = versions
                if vn_next == 0:
                    break
                pos += vn_next
        elif sh["type"] == 0x6FFFFFFD:  # SHT_GNU_verdef
            strtab_sh = shdrs[sh["link"]] if sh["link"] < len(shdrs) else None
            strtab = data[strtab_sh["offset"] : strtab_sh["offset"] + strtab_sh["size"]] if strtab_sh else b""
            pos = sh["offset"]
            end = sh["offset"] + sh["size"]
            while pos + 20 <= end:
                vd_version, vd_flags, vd_ndx, vd_cnt, vd_hash, vd_aux, vd_next = struct.unpack_from("<HHHHIII", data, pos)
                aux_pos = pos + vd_aux
                for _ in range(vd_cnt):
                    if aux_pos + 8 > end:
                        break
                    vda_name, vda_next = struct.unpack_from("<II", data, aux_pos)
                    ver_name = read_cstr(strtab, vda_name)
                    if parse_version_tuple(ver_name) is not None:
                        glibc_defined.append(ver_name)
                    if vda_next == 0:
                        break
                    aux_pos += vda_next
                if vd_next == 0:
                    break
                pos += vd_next

    symbols = {}
    symbol_counts = {"dynsym": 0, "symtab": 0}
    for sh in shdrs:
        if sh["type"] in (2, 11) and sh["entsize"] >= 16:  # SHT_SYMTAB=2, SHT_DYNSYM=11
            table_kind = "dynsym" if sh["type"] == 11 else "symtab"
            strtab_sh = shdrs[sh["link"]] if sh["link"] < len(shdrs) else None
            if not strtab_sh:
                continue
            strtab = data[strtab_sh["offset"] : strtab_sh["offset"] + strtab_sh["size"]]
            count = sh["size"] // sh["entsize"]
            symbol_counts[table_kind] += count
            for idx in range(count):
                off = sh["offset"] + idx * sh["entsize"]
                st_name, st_value, st_size, st_info, st_other, st_shndx = struct.unpack_from("<IIIBBH", data, off)
                if st_name == 0:
                    continue
                name = read_cstr(strtab, st_name)
                if not name:
                    continue
                is_defined = st_shndx != 0
                entry = {
                    "name": name,
                    "value": st_value,
                    "size": st_size,
                    "bind": st_info >> 4,
                    "type": st_info & 0xF,
                    "shndx": st_shndx,
                    "defined": is_defined,
                    "table": table_kind,
                }
                prev = symbols.get(name)
                if prev is None or (not prev["defined"] and is_defined) or (table_kind == "symtab" and prev["table"] != "symtab" and is_defined):
                    symbols[name] = entry

    return {
        "elf_type": elf_types.get(e_type, f"0x{e_type:x}"),
        "e_type": e_type,
        "e_entry": e_entry,
        "e_flags": e_flags,
        "abi_flags": abi_flags,
        "interp": interp,
        "soname": soname,
        "needed": needed,
        "verneed": verneed,
        "glibc_versions": sorted(set(glibc_versions), key=lambda s: parse_version_tuple(s) or (0,)),
        "glibc_defined": sorted(set(glibc_defined), key=lambda s: parse_version_tuple(s) or (0,)),
        "glibc_violations": glibc_violations,
        "phdrs": phdrs,
        "shdrs": shdrs,
        "symbols": symbols,
        "symbol_counts": symbol_counts,
    }


def find_zero_runs(buf: bytes, base_vaddr: int, min_len: int, max_results: int = 8):
    runs = []
    n = len(buf)
    i = 0
    while i < n:
        if buf[i] == 0:
            j = i + 1
            while j < n and buf[j] == 0:
                j += 1
            length = j - i
            if length >= min_len:
                # Align start to 4 bytes for Thumb/ARM safety
                aligned_start = (i + 3) & ~3
                aligned_len = j - aligned_start
                if aligned_len >= min_len:
                    runs.append(
                        {
                            "vaddr": f"0x{base_vaddr + aligned_start:08x}",
                            "offset_in_slice": aligned_start,
                            "bytes": aligned_len,
                        }
                    )
                    if len(runs) >= max_results:
                        break
            i = j
        else:
            i += 1
    return runs


def scan_caves(data: bytes, parsed: dict, min_code_cave: int, min_state_cave: int) -> dict:
    exec_caves = []
    rw_caves = []
    load_segments = []

    for ph in parsed["phdrs"]:
        if ph["type"] != 1:  # PT_LOAD
            continue
        vaddr = ph["vaddr"]
        off = ph["offset"]
        filesz = ph["filesz"]
        memsz = ph["memsz"]
        flags = ph["flags"]
        seg_info = {
            "index": ph["index"],
            "perms": ph["perms"],
            "vaddr": f"0x{vaddr:08x}",
            "offset": f"0x{off:x}",
            "filesz": filesz,
            "memsz": memsz,
            "bss_bytes": max(0, memsz - filesz),
            "page_tail_pad": ((vaddr + memsz + 4095) & ~4095) - (vaddr + memsz),
        }
        load_segments.append(seg_info)

        file_slice = data[off : min(len(data), off + filesz)]
        if flags & 1:  # PF_X (r-xp)
            for run in find_zero_runs(file_slice, vaddr, min_code_cave):
                run["source"] = f"PT_LOAD[{ph['index']}] file zeros"
                exec_caves.append(run)
            tail_pad = ((vaddr + filesz + 4095) & ~4095) - (vaddr + filesz)
            if tail_pad >= min_code_cave:
                aligned = (vaddr + filesz + 3) & ~3
                exec_caves.append(
                    {
                        "vaddr": f"0x{aligned:08x}",
                        "bytes": ((vaddr + filesz + 4095) & ~4095) - aligned,
                        "source": f"PT_LOAD[{ph['index']}] page-tail padding (note: check page perms / file mapping)",
                    }
                )
        if flags & 2:  # PF_W (rw-p)
            for run in find_zero_runs(file_slice, vaddr, min_state_cave):
                run["source"] = f"PT_LOAD[{ph['index']}] file zeros"
                rw_caves.append(run)
            bss_and_tail = ((vaddr + memsz + 4095) & ~4095) - (vaddr + filesz)
            if bss_and_tail >= STATE_STRUCT_SIZE:
                aligned = (vaddr + filesz + 3) & ~3
                rw_caves.append(
                    {
                        "vaddr": f"0x{aligned:08x}",
                        "bytes": ((vaddr + memsz + 4095) & ~4095) - aligned,
                        "source": f"PT_LOAD[{ph['index']}] .bss + page-tail rw-p (memsz-filesz={memsz-filesz})",
                    }
                )

    return {
        "load_segments": load_segments,
        "exec_caves": exec_caves,
        "rw_caves": rw_caves,
    }


def inspect_file(path: Path, min_code_cave: int, min_state_cave: int) -> dict:
    data = path.read_bytes()
    info = {
        "path": str(path),
        "name": path.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "fnv1a_32": f"0x{fnv1a_32(data):08x}",
    }
    parsed = parse_elf32_arm(data)
    caves = scan_caves(data, parsed, min_code_cave, min_state_cave)

    trial3_hits = []
    trial3_missing = []
    for mangled, a6400_rva, label in UI_TRIAL3_SYMBOLS:
        sym = parsed["symbols"].get(mangled)
        if sym and sym["defined"]:
            trial3_hits.append(
                {
                    "symbol": mangled,
                    "label": label,
                    "value": f"0x{sym['value']:08x}",
                    "thumb": bool(sym["value"] & 1),
                    "a6400_offset": f"0x{a6400_rva:x}",
                }
            )
        else:
            trial3_missing.append(mangled)

    extra_hits = []
    for mangled, label in EXTRA_SYMBOLS:
        sym = parsed["symbols"].get(mangled)
        if sym and sym["defined"]:
            extra_hits.append(
                {
                    "symbol": mangled,
                    "label": label,
                    "value": f"0x{sym['value']:08x}",
                    "size": sym["size"],
                }
            )

    info.update(
        {
            "elf_type": parsed["elf_type"],
            "entry": f"0x{parsed['e_entry']:08x}",
            "abi_flags": parsed["abi_flags"],
            "interp": parsed["interp"],
            "soname": parsed["soname"],
            "needed": parsed["needed"],
            "glibc_versions": parsed["glibc_versions"],
            "glibc_defined": parsed["glibc_defined"],
            "glibc_le_2_11_2": len(parsed["glibc_violations"]) == 0,
            "glibc_violations": parsed["glibc_violations"],
            "symbol_counts": parsed["symbol_counts"],
            "load_segments": caves["load_segments"],
            "exec_caves": caves["exec_caves"],
            "rw_caves": caves["rw_caves"],
            "ui_trial3_found": trial3_hits,
            "ui_trial3_missing_count": len(trial3_missing),
            "ui_trial3_missing": trial3_missing,
            "extra_symbols_found": extra_hits,
        }
    )
    return info


def build_synthetic_fixture(out_path: Path) -> None:
    """Create a synthetic ELF32 ARM ET_DYN fixture to self-test the parser."""
    # Strings for .shstrtab and .dynstr
    shstr_names = [b"", b".shstrtab", b".dynstr", b".dynsym", b".gnu.version_r", b".text", b".data"]
    shstr_offsets = {}
    shstrtab = bytearray()
    for s in shstr_names:
        shstr_offsets[s.decode()] = len(shstrtab)
        shstrtab.extend(s + b"\x00")

    dynstr_list = [
        b"",
        b"libc.so.6",
        b"GLIBC_2.4",
        b"GLIBC_2.11",
        b"_ZN2ux6wgtsys6WidgetC1Ev",
        b"_ZN9GEN_PanelC1Ev",
    ]
    dynstr_offsets = {}
    dynstr = bytearray()
    for s in dynstr_list:
        dynstr_offsets[s.decode()] = len(dynstr)
        dynstr.extend(s + b"\x00")

    # .dynsym: NULL + 2 defined symbols
    dynsym = bytearray()
    dynsym.extend(struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0))
    dynsym.extend(struct.pack("<IIIBBH", dynstr_offsets["_ZN2ux6wgtsys6WidgetC1Ev"], 0x1001, 16, (1 << 4) | 2, 0, 5))
    dynsym.extend(struct.pack("<IIIBBH", dynstr_offsets["_ZN9GEN_PanelC1Ev"], 0x1021, 16, (1 << 4) | 2, 0, 5))

    # .gnu.version_r: 1 Verneed ("libc.so.6", cnt=2) + 2 Vernaux ("GLIBC_2.4", "GLIBC_2.11")
    verneed = bytearray()
    verneed.extend(struct.pack("<HHIII", 1, 2, dynstr_offsets["libc.so.6"], 16, 0))
    verneed.extend(struct.pack("<IHHII", 0, 0, 2, dynstr_offsets["GLIBC_2.4"], 16))
    verneed.extend(struct.pack("<IHHII", 0, 0, 3, dynstr_offsets["GLIBC_2.11"], 0))

    # .text at offset 0x1000: 16 bytes code + 2400 bytes zero cave
    text_data = b"\x00\xbf" * 8 + b"\x00" * 2400
    # .data at offset 0x2000: 16 bytes data + 1800 bytes zero cave
    data_sec = b"\x01\x02\x03\x04" * 4 + b"\x00" * 1800

    # Layout sections after page 0 (0x1000)
    ehdr_size = 52
    phdr_off = ehdr_size
    phdr_count = 2
    # Metadata sections placed at 0x200
    meta_off = 0x200
    shstr_off = meta_off
    dynstr_off = shstr_off + len(shstrtab)
    dynsym_off = (dynstr_off + len(dynstr) + 3) & ~3
    verneed_off = (dynsym_off + len(dynsym) + 3) & ~3
    text_off = 0x1000
    data_off = 0x2000
    shoff = 0x3000

    total_size = shoff + 7 * 40
    buf = bytearray(total_size)

    # ELF header (ET_DYN=3, EM_ARM=40, soft-float EABI5 flags=0x05000200)
    e_ident = b"\x7fELF\x01\x01\x01" + b"\x00" * 9
    struct.pack_into(
        "<16sHHIIIIIHHHHHH",
        buf,
        0,
        e_ident,
        3,
        40,
        1,
        0,
        phdr_off,
        shoff,
        0x05000200,
        52,
        32,
        phdr_count,
        40,
        7,
        1,
    )
    # PT_LOAD r-xp (.text) & rw-p (.data with bss)
    struct.pack_into("<IIIIIIII", buf, phdr_off, 1, text_off, text_off, text_off, len(text_data), len(text_data), 5, 0x1000)
    struct.pack_into("<IIIIIIII", buf, phdr_off + 32, 1, data_off, data_off, data_off, len(data_sec), len(data_sec) + 2048, 6, 0x1000)

    buf[shstr_off : shstr_off + len(shstrtab)] = shstrtab
    buf[dynstr_off : dynstr_off + len(dynstr)] = dynstr
    buf[dynsym_off : dynsym_off + len(dynsym)] = dynsym
    buf[verneed_off : verneed_off + len(verneed)] = verneed
    buf[text_off : text_off + len(text_data)] = text_data
    buf[data_off : data_off + len(data_sec)] = data_sec

    shdrs_spec = [
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (shstr_offsets[".shstrtab"], 3, 0, 0, shstr_off, len(shstrtab), 0, 0, 1, 0),
        (shstr_offsets[".dynstr"], 3, 2, dynstr_off, dynstr_off, len(dynstr), 0, 0, 1, 0),
        (shstr_offsets[".dynsym"], 11, 2, dynsym_off, dynsym_off, len(dynsym), 2, 1, 4, 16),
        (shstr_offsets[".gnu.version_r"], 0x6FFFFFFE, 2, verneed_off, verneed_off, len(verneed), 2, 1, 4, 0),
        (shstr_offsets[".text"], 1, 6, text_off, text_off, len(text_data), 0, 0, 4, 0),
        (shstr_offsets[".data"], 1, 3, data_off, data_off, len(data_sec), 0, 0, 4, 0),
    ]
    for idx, spec in enumerate(shdrs_spec):
        struct.pack_into("<IIIIIIIIII", buf, shoff + idx * 40, *spec)

    out_path.write_bytes(bytes(buf))


def run_self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "synthetic-libObj.so"
        build_synthetic_fixture(p)
        res = inspect_file(p, DEFAULT_MIN_CODE_CAVE, DEFAULT_MIN_STATE_CAVE)
        assert res["elf_type"] == "ET_DYN", res
        assert res["glibc_versions"] == ["GLIBC_2.4", "GLIBC_2.11"], res["glibc_versions"]
        assert res["glibc_le_2_11_2"] is True
        assert len(res["ui_trial3_found"]) == 2, res["ui_trial3_found"]
        assert res["ui_trial3_missing_count"] == 17
        assert len(res["exec_caves"]) >= 1 and res["exec_caves"][0]["bytes"] >= 2296, res["exec_caves"]
        assert len(res["rw_caves"]) >= 1 and res["rw_caves"][0]["bytes"] >= 1760, res["rw_caves"]
        print("SELF_TEST_OK", json.dumps(
            {
                "elf_type": res["elf_type"],
                "glibc_versions": res["glibc_versions"],
                "exec_caves": len(res["exec_caves"]),
                "rw_caves": len(res["rw_caves"]),
                "ui_trial3_found": len(res["ui_trial3_found"]),
            }
        ))
    return 0


def format_human(results: list) -> str:
    lines = []
    for r in results:
        if "error" in r:
            lines.append(f"=== {r['path']} (ERROR: {r['error']}) ===")
            continue
        lines.append(f"=== {r['name']} ({r['elf_type']}, entry={r['entry']}) ===")
        lines.append(f"  path:   {r['path']}")
        lines.append(f"  size:   {r['bytes']} bytes | fnv1a={r['fnv1a_32']} | sha256={r['sha256']}")
        lines.append(f"  abi:    {', '.join(r['abi_flags']) or 'none'} | interp={r['interp'] or 'none'} | soname={r['soname'] or 'none'}")
        lines.append(f"  needed: {', '.join(r['needed']) or 'none'}")
        lines.append(
            f"  glibc:  needed={', '.join(r['glibc_versions']) or 'none'} | "
            f"defined={', '.join(r['glibc_defined']) or 'none'} | <=2.11.2: {r['glibc_le_2_11_2']}"
        )
        if r["glibc_violations"]:
            lines.append(f"  GLIBC VIOLATIONS (>2.11.2): {r['glibc_violations']}")
        lines.append(
            f"  syms:   dynsym={r['symbol_counts']['dynsym']}, symtab={r['symbol_counts']['symtab']}, "
            f"trial3_found={len(r['ui_trial3_found'])}/19"
        )
        for seg in r["load_segments"]:
            lines.append(
                f"  PT_LOAD[{seg['index']}] {seg['perms']} vaddr={seg['vaddr']} off={seg['offset']} "
                f"filesz=0x{seg['filesz']:x} memsz=0x{seg['memsz']:x} bss=0x{seg['bss_bytes']:x}"
            )
        if r["exec_caves"]:
            lines.append("  exec_caves (r-xp):")
            for c in r["exec_caves"]:
                lines.append(f"    {c['vaddr']} ({c['bytes']} B) - {c['source']}")
        if r["rw_caves"]:
            lines.append("  rw_caves (rw-p):")
            for c in r["rw_caves"]:
                lines.append(f"    {c['vaddr']} ({c['bytes']} B) - {c['source']}")
        if r["ui_trial3_found"]:
            lines.append("  UI trial3 symbols:")
            for s in r["ui_trial3_found"]:
                lines.append(
                    f"    {s['value']} (a6400={s['a6400_offset']}, thumb={int(s['thumb'])}) {s['symbol']}"
                )
        if r["extra_symbols_found"]:
            lines.append("  Extra symbols:")
            for s in r["extra_symbols_found"]:
                lines.append(f"    {s['value']} (size={s['size']}) {s['symbol']}")
    return "\n".join(lines)


def collect_targets(paths):
    targets = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and not child.is_symlink():
                    targets.append(child)
        elif p.is_file():
            targets.append(p)
        else:
            targets.append(p)
    return targets


def main():
    ap = argparse.ArgumentParser(description="Read-only ELF32 ARM analyzer for ILCE-7RM4A binaries.")
    ap.add_argument("paths", nargs="*", help="ELF/SO files or directories to inspect")
    ap.add_argument("--min-code-cave", type=int, default=DEFAULT_MIN_CODE_CAVE)
    ap.add_argument("--min-state-cave", type=int, default=DEFAULT_MIN_STATE_CAVE)
    ap.add_argument("--json", action="store_true", help="Emit JSON output")
    ap.add_argument("--self-test", action="store_true", help="Run built-in synthetic ELF self-test")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test()
    if not args.paths:
        ap.error("Provide at least one ELF/SO path or directory, or --self-test")

    results = []
    for target in collect_targets(args.paths):
        if not target.exists():
            results.append({"path": str(target), "error": "file not found"})
            continue
        try:
            head = target.read_bytes()[:4]
            if head != b"\x7fELF":
                continue
            results.append(inspect_file(target, args.min_code_cave, args.min_state_cave))
        except Exception as exc:
            results.append({"path": str(target), "error": str(exc)})

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_human(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
