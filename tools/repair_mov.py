#!/usr/bin/env python3
"""Rebuild the missing moov atom of an unfinalised ProRes .mov.

Unreal's Movie Render Queue writes frames into mdat as it goes and only writes
the moov index at the very end. When it hangs or crashes at finalisation the
frames are all on disk but there is no index, so nothing can open the file.

Every ProRes frame is self-delimiting -- [4-byte BE size]['icpf'][header] -- so
the sample table can be reconstructed by scanning for frames, and a fresh moov
built from it.

    repair_mov.py <broken.mov> <fixed.mov> [fps] [fourcc]
"""
import os
import struct
import sys

START = 36                  # ftyp(20) + wide(8) + mdat header(8)
MIN_FRAME = 50_000
MAX_FRAME = 8_000_000
MATRIX = struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)


def box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def walk(path):
    """Collect every ProRes frame as (offset, size), plus the frame dimensions.

    One linear pass gathers candidates; the chain is then built by only ever
    accepting a candidate at or after the END of the previous frame. That skips
    both the writer's interstitial index tables and the false 'icpf' matches
    that occur by chance inside compressed frame data.
    """
    total = os.path.getsize(path)
    cands, order = {}, []
    with open(path, "rb") as f:
        pos, tail = 0, b""
        while pos < total:
            buf = f.read(32 * 1024 * 1024)
            if not buf:
                break
            data = tail + buf
            base = pos - len(tail)
            s = 0
            while True:
                i = data.find(b"icpf", s)
                if i < 0:
                    break
                s = i + 4
                if i < 4 or i + 16 > len(data):
                    continue
                size = struct.unpack(">I", data[i - 4:i])[0]
                if MIN_FRAME <= size <= MAX_FRAME:
                    off = base + i - 4
                    if off not in cands:
                        # after 'icpf': hdr_size(2) version(2) encoder(4) w(2) h(2)
                        cands[off] = (size, struct.unpack(">HH", data[i + 12:i + 16]))
                        order.append(off)
            tail = data[-8:]
            pos += len(buf)

    order.sort()
    offs, szs, w, h = [], [], None, None
    nxt = START
    for off in order:
        if off < nxt:
            continue
        size, wh = cands[off]
        if w is None:
            w, h = wh
        offs.append(off)
        szs.append(size)
        nxt = off + size
    return offs, szs, w, h


def build_moov(offs, szs, w, h, fps, fourcc):
    n = len(offs)
    ts = int(round(fps * 1000))          # media timescale
    delta = 1000                          # one frame
    mdur = n * delta
    movie_ts = 1000
    movie_dur = int(n / fps * movie_ts)

    mvhd = box(b"mvhd", struct.pack(">IIIII", 0, 0, 0, movie_ts, movie_dur)
               + struct.pack(">i", 0x10000) + struct.pack(">h", 0x0100)
               + b"\x00" * 10 + MATRIX + b"\x00" * 24 + struct.pack(">I", 2))

    tkhd = box(b"tkhd", struct.pack(">IIIIII", 0x0F, 0, 0, 1, 0, movie_dur)
               + b"\x00" * 8 + struct.pack(">hhhh", 0, 0, 0, 0) + MATRIX
               + struct.pack(">II", w << 16, h << 16))

    mdhd = box(b"mdhd", struct.pack(">IIIII", 0, 0, 0, ts, mdur)
               + struct.pack(">hh", 0x55C4, 0))
    hdlr = box(b"hdlr", struct.pack(">II", 0, 0) + b"vide" + b"\x00" * 12 + b"\x00")

    vmhd = box(b"vmhd", struct.pack(">IHHHH", 1, 0, 0, 0, 0))
    dref = box(b"dref", struct.pack(">II", 0, 1) + box(b"url ", struct.pack(">I", 1)))
    dinf = box(b"dinf", dref)

    name = b"Apple ProRes 422"
    cname = bytes([len(name)]) + name + b"\x00" * (31 - len(name))
    entry = (fourcc + b"\x00" * 6 + struct.pack(">H", 1)
             + struct.pack(">HH", 0, 0) + b"appl"
             + struct.pack(">II", 0, 1024)          # temporal / spatial quality
             + struct.pack(">HH", w, h)
             + struct.pack(">II", 0x00480000, 0x00480000)
             + struct.pack(">I", 0) + struct.pack(">H", 1)
             + cname + struct.pack(">Hh", 24, -1)
             + box(b"colr", b"nclc" + struct.pack(">HHH", 1, 1, 1)))  # bt709
    stsd = box(b"stsd", struct.pack(">II", 0, 1)
               + struct.pack(">I", 8 + len(entry)) + entry)

    stts = box(b"stts", struct.pack(">IIII", 0, 1, n, delta))
    # One sample per chunk, so chunk offsets are simply the frame offsets.
    # stsc is MANDATORY -- without it a reader reports "missing mandatory atoms"
    # and falls back to a guessed timebase.
    stsc = box(b"stsc", struct.pack(">II", 0, 1) + struct.pack(">III", 1, 1, 1))
    stsz = box(b"stsz", struct.pack(">III", 0, 0, n) + b"".join(struct.pack(">I", s) for s in szs))
    co64 = box(b"co64", struct.pack(">II", 0, n) + b"".join(struct.pack(">Q", o) for o in offs))

    stbl = box(b"stbl", stsd + stts + stsc + stsz + co64)
    # QuickTime also expects a data-handler reference inside minf.
    dhlr = box(b"hdlr", struct.pack(">II", 0, 0) + b"alis" + b"\x00" * 12 + b"\x00")
    minf = box(b"minf", vmhd + dhlr + dinf + stbl)
    mdia = box(b"mdia", mdhd + hdlr + minf)
    trak = box(b"trak", tkhd + mdia)
    return box(b"moov", mvhd + trak)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    fps = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0
    fourcc = (sys.argv[4] if len(sys.argv) > 4 else "apcn").encode()

    offs, szs, w, h = walk(src)
    if not offs:
        print("no ProRes frames found", file=sys.stderr)
        sys.exit(1)
    used_end = offs[-1] + szs[-1]
    moov = build_moov(offs, szs, w, h, fps, fourcc)

    print(f"frames:     {len(offs):,}")
    print(f"resolution: {w}x{h}")
    print(f"duration:   {len(offs)/fps:.2f}s @ {fps:g}fps")
    print(f"data ends:  {used_end:,} of {os.path.getsize(src):,}")
    print(f"moov size:  {len(moov):,} bytes")

    if os.path.abspath(src) == os.path.abspath(dst):
        # In-place: the mdat header is already correct from a previous run, so
        # just drop the old index and append the new one. Avoids re-copying
        # multiple GB to rewrite a 90 KB atom.
        with open(src, "r+b") as f:
            f.truncate(used_end)
            f.seek(used_end)
            f.write(moov)
        print(f"patched:    {dst}  ({os.path.getsize(dst):,} bytes)")
        return

    with open(src, "rb") as fi, open(dst, "wb") as fo:
        head = fi.read(START)
        # mdat size was left as 0 ("extends to EOF"); pin it so moov can follow.
        # Past 4 GB a 32-bit size cannot hold it, so consume the 'wide' placeholder
        # at offset 20 to write a 64-bit mdat header instead. Both layouts occupy
        # bytes 20..36, so the payload still starts at 36 and every frame offset
        # already collected stays valid.
        fo.write(head[:20] + struct.pack(">I", 1) + b"mdat"
                 + struct.pack(">Q", used_end - 20))
        remaining = used_end - START
        while remaining > 0:
            chunk = fi.read(min(32 * 1024 * 1024, remaining))
            if not chunk:
                break
            fo.write(chunk)
            remaining -= len(chunk)
        fo.write(moov)
    print(f"written:    {dst}  ({os.path.getsize(dst):,} bytes)")


if __name__ == "__main__":
    main()
