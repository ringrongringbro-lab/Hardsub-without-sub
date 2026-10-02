"""
HVID v1 file format (big-endian)

Header, 76 bytes:
   0   4  magic  b"HVID"
   4   1  version (1)
   5   3  reserved (0)
   8  16  video_iv      AES-CTR initial counter block
  24  32  masked_key    key XOR SHA256(SECRET)
  56   8  sub_len       subtitle ciphertext length incl. 16 byte GCM tag (0 = none)
  64  12  sub_nonce     AES-GCM nonce

Then:
  sub_len bytes   subtitle: AES-256-GCM, key = SHA256(b"sub" + key), aad = b"HVID",
                  plaintext = ASS text (UTF-8)
  rest of file    video: AES-256-CTR, key = key, counter block = video_iv
                  (128-bit big-endian counter, +1 per 16 byte block, so any
                  position can be decrypted: counter = iv + pos // 16)

video_offset = 76 + sub_len
"""
import hashlib
import secrets
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"HVID"
VERSION = 1
HEADER_FMT = ">4sB3x16s32sQ12s"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 76
CHUNK = 8 * 1024 * 1024


def _mask(key: bytes, secret: bytes) -> bytes:
    h = hashlib.sha256(secret).digest()
    return bytes(a ^ b for a, b in zip(key, h))


def _sub_key(key: bytes) -> bytes:
    return hashlib.sha256(b"sub" + key).digest()


def pack(video_path: str, ass_text, out_path: str, secret: bytes) -> None:
    key = secrets.token_bytes(32)
    iv = secrets.token_bytes(16)

    if ass_text:
        sub_nonce = secrets.token_bytes(12)
        sub_ct = AESGCM(_sub_key(key)).encrypt(sub_nonce, ass_text.encode("utf-8"), MAGIC)
    else:
        sub_nonce = bytes(12)
        sub_ct = b""

    header = struct.pack(HEADER_FMT, MAGIC, VERSION, iv, _mask(key, secret), len(sub_ct), sub_nonce)

    enc = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    with open(out_path, "wb") as out, open(video_path, "rb") as src:
        out.write(header)
        out.write(sub_ct)
        while True:
            block = src.read(CHUNK)
            if not block:
                break
            out.write(enc.update(block))
        out.write(enc.finalize())


# ---- helpers below are only for testing / reference for the Android app ----

def read_header(path: str, secret: bytes):
    with open(path, "rb") as f:
        raw = f.read(HEADER_SIZE)
    magic, ver, iv, masked, sub_len, sub_nonce = struct.unpack(HEADER_FMT, raw)
    if magic != MAGIC:
        raise ValueError("not an HVID file")
    return ver, iv, _mask(masked, secret), sub_len, sub_nonce


def read_subtitle(path: str, secret: bytes):
    ver, iv, key, sub_len, sub_nonce = read_header(path, secret)
    if not sub_len:
        return None
    with open(path, "rb") as f:
        f.seek(HEADER_SIZE)
        ct = f.read(sub_len)
    return AESGCM(_sub_key(key)).decrypt(sub_nonce, ct, MAGIC).decode("utf-8")


def read_video_range(path: str, secret: bytes, pos: int, length: int) -> bytes:
    ver, iv, key, sub_len, sub_nonce = read_header(path, secret)
    base = HEADER_SIZE + sub_len
    ctr = (int.from_bytes(iv, "big") + pos // 16) % (1 << 128)
    dec = Cipher(algorithms.AES(key), modes.CTR(ctr.to_bytes(16, "big"))).decryptor()
    with open(path, "rb") as f:
        f.seek(base + pos - pos % 16)
        data = f.read(length + pos % 16)
    return dec.update(data)[pos % 16:]
