#!/usr/bin/env python3
"""
parse_qhf.py -- Standalone parser for QIP Infium .qhf history files.

QHF (QIP History File) is a proprietary binary format used by QIP Infium,
a popular Russian ICQ/Jabber IM client (circa 2008-2013). The format stores
chat history with a simple position-dependent cipher applied to message text.

File structure (reverse-engineered):
  - Magic bytes: "QHF" (3 bytes)
  - Version byte: 0x01 or 0x02 (1 byte)
  - Reserved / unknown fields (0x1E bytes, offsets 0x04..0x21)
  - Message count: big-endian uint32 at offset 0x22
  - Verification copy of message count: uint32 (4 bytes, skipped)
  - Reserved: 2 bytes
  - Contact UIN: length-prefixed string (uint16 BE length + UTF-8 bytes).
    For ICQ this is a numeric UIN; for Jabber it is a JID (user@domain).
  - Contact nickname: length-prefixed string (uint16 BE length + UTF-8 bytes)
  - Message records (repeated msg_count times):
      - Signature: uint16 BE, must be 0x0001
      - Block size: uint32 BE (size of the rest of this record)
      - Field: msg_id (uint32 BE, preceded by 4-byte field header)
      - Field: timestamp (Unix epoch, uint32 BE, preceded by 4-byte header)
      - Field: direction (1 byte; 0 = incoming, 1 = outgoing; preceded by
        4-byte header)
      - Field: text (preceded by 4-byte header):
          - v2: text length is uint16 BE
          - v1: text length is uint32 BE
        Text bytes are encrypted with a position-dependent cipher (see below).
        v1 files apply the cipher twice.

Text cipher:
  For each byte at position i in the encrypted blob:
      plaintext[i] = (0xFF - encrypted[i] - i - 1) & 0xFF
  The result is UTF-8 encoded text.  Version 1 applies this transform twice.

Usage:
  python parse_qhf.py path/to/file.qhf          # summary for one file
  python parse_qhf.py path/to/History/            # scan entire directory
  python parse_qhf.py --dump path/to/file.qhf    # print all messages
"""

from __future__ import annotations

import argparse
import struct
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def decode_qhf_text(encrypted_bytes: bytes) -> str:
    """Decrypt QHF-encrypted text using the position-dependent cipher.

    Each byte is transformed as: plain[i] = (0xFF - enc[i] - i - 1) & 0xFF.
    The resulting bytes are decoded as UTF-8.

    Args:
        encrypted_bytes: Raw encrypted bytes from the QHF message record.

    Returns:
        Decrypted UTF-8 string.
    """
    result = bytearray(len(encrypted_bytes))
    for i, b in enumerate(encrypted_bytes):
        result[i] = (0xFF - b - i - 1) & 0xFF
    return result.decode("utf-8", errors="replace")


def parse_qhf(filepath: str | Path) -> Optional[tuple[str, str, list[dict]]]:
    """Parse a .qhf file and return contact info and messages.

    Args:
        filepath: Path to a .qhf history file.

    Returns:
        A tuple of (uin, nickname, messages) where each message is a dict
        with keys: id, time (datetime), incoming (bool), text, timestamp.
        Returns None if the file is not a valid QHF file.
    """
    filepath = Path(filepath)
    data = filepath.read_bytes()

    if len(data) < 10 or data[:3] != b"QHF":
        return None

    version = data[3]
    pos = 0x22

    msg_count = struct.unpack(">I", data[pos : pos + 4])[0]
    pos += 8  # msg_count + verification copy
    pos += 2  # reserved

    # Contact UIN (ICQ number or Jabber JID)
    uin_len = struct.unpack(">H", data[pos : pos + 2])[0]
    pos += 2
    uin = data[pos : pos + uin_len].decode("utf-8", errors="replace")
    pos += uin_len

    # Contact nickname
    nick_len = struct.unpack(">H", data[pos : pos + 2])[0]
    pos += 2
    nick = data[pos : pos + nick_len].decode("utf-8", errors="replace")
    pos += nick_len

    messages: list[dict] = []
    for _ in range(msg_count):
        if pos >= len(data):
            break
        try:
            sig = struct.unpack(">H", data[pos : pos + 2])[0]
            if sig != 0x0001:
                break
            pos += 2

            block_size = struct.unpack(">I", data[pos : pos + 4])[0]
            pos += 4
            block_start = pos

            # Message ID
            pos += 4  # field type + size header
            msg_id = struct.unpack(">I", data[pos : pos + 4])[0]
            pos += 4

            # Timestamp (Unix epoch)
            pos += 4  # field header
            timestamp = struct.unpack(">I", data[pos : pos + 4])[0]
            pos += 4

            # Direction
            pos += 4  # field header
            direction = data[pos]
            pos += 1

            # Text
            pos += 4  # field header
            if version == 0x02:
                text_len = struct.unpack(">H", data[pos : pos + 2])[0]
                pos += 2
            else:
                text_len = struct.unpack(">I", data[pos : pos + 4])[0]
                pos += 4

            encrypted = data[pos : pos + text_len]
            text = decode_qhf_text(encrypted)
            if version == 0x01:
                # v1 applies the cipher twice
                text = decode_qhf_text(text.encode("utf-8", errors="replace"))
            pos += text_len

            dt = datetime.fromtimestamp(timestamp)
            messages.append(
                {
                    "id": msg_id,
                    "time": dt,
                    "incoming": direction == 0,
                    "text": text,
                    "timestamp": timestamp,
                }
            )
        except (struct.error, IndexError):
            pos = block_start + block_size
            continue

    return uin, nick, messages


def _print_summary(filepath: Path, result: tuple[str, str, list[dict]]) -> None:
    """Print a one-line summary for a parsed QHF file."""
    uin, nick, messages = result
    if messages:
        dates = [m["time"] for m in messages]
        earliest = min(dates).strftime("%Y-%m-%d")
        latest = max(dates).strftime("%Y-%m-%d")
        date_range = f"{earliest} .. {latest}"
    else:
        date_range = "(no messages)"
    print(f"{filepath.name:40s}  UIN: {uin:24s}  nick: {nick:20s}  msgs: {len(messages):5d}  {date_range}")


def _print_messages(result: tuple[str, str, list[dict]]) -> None:
    """Print all messages from a parsed QHF file."""
    uin, nick, messages = result
    print(f"=== {uin} ({nick}) -- {len(messages)} messages ===\n")
    for m in messages:
        direction = "<-" if m["incoming"] else "->"
        print(f"[{m['time']:%Y-%m-%d %H:%M:%S}] {direction} {m['text']}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse QIP Infium .qhf history files.",
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Path to a .qhf file or a directory containing .qhf files.",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Print all messages (default: summary only).",
    )
    args = parser.parse_args()

    target: Path = args.path
    if not target.exists():
        print(f"Error: {target} does not exist.", file=sys.stderr)
        sys.exit(1)

    if target.is_file():
        files = [target]
    else:
        files = sorted(target.rglob("*.qhf"))
        if not files:
            print(f"No .qhf files found in {target}", file=sys.stderr)
            sys.exit(1)

    total_msgs = 0
    total_files = 0

    for fpath in files:
        result = parse_qhf(fpath)
        if result is None:
            print(f"SKIP  {fpath.name} (not a valid QHF file)", file=sys.stderr)
            continue
        total_files += 1
        total_msgs += len(result[2])
        if args.dump:
            _print_messages(result)
        else:
            _print_summary(fpath, result)

    if not args.dump and total_files > 1:
        print(f"\nTotal: {total_files} files, {total_msgs} messages")


if __name__ == "__main__":
    main()
