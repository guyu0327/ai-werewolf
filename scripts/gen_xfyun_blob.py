"""生成讯飞凭证密文：写入 backend/app/voice/credentials.py 的 _BLOB/_NONCE/_KEY_PART1，
并输出 iat.py 需要的 _KEY_PART2。

用法（参数任选其一）：
  python scripts/gen_xfyun_blob.py --app-id XXX --api-key YYY --api-secret ZZZ
  XF_APP_ID=... XF_API_KEY=... XF_API_SECRET=... python scripts/gen_xfyun_blob.py
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent.parent
CRED_FILE = ROOT / "backend" / "app" / "voice" / "credentials.py"
IAT_FILE = ROOT / "backend" / "app" / "voice" / "iat.py"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-id", default=os.environ.get("XF_APP_ID", ""))
    ap.add_argument("--api-key", default=os.environ.get("XF_API_KEY", ""))
    ap.add_argument("--api-secret", default=os.environ.get("XF_API_SECRET", ""))
    args = ap.parse_args()
    if not (args.app_id and args.api_key and args.api_secret):
        sys.exit("需要 --app-id/--api-key/--api-secret（或 XF_APP_ID/XF_API_KEY/XF_API_SECRET 环境变量）")

    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    plain = "\n".join([args.app_id, args.api_key, args.api_secret]).encode("utf-8")
    blob = AESGCM(key).encrypt(nonce, plain, None)

    key_part1, key_part2 = key[:16], key[16:]
    cred_block = (
        f'_BLOB = "{blob.hex()}"  # AES-GCM 密文+tag（hex）\n'
        f'_NONCE = "{nonce.hex()}"  # 12 字节 nonce（hex）\n'
        f'_KEY_PART1 = "{key_part1.hex()}"  # 密钥前 16 字节（hex），后半在 iat.py'
    )

    src = CRED_FILE.read_text("utf-8")
    src = re.sub(
        r'_BLOB = "[0-9a-f]*".*?_KEY_PART1 = "[0-9a-f]*".*?iat\.py',
        cred_block,
        src,
        count=1,
        flags=re.S,
    )
    CRED_FILE.write_text(src, encoding="utf-8")

    iat_src = IAT_FILE.read_text("utf-8")
    iat_src = re.sub(r'_KEY_PART2 = b"[0-9a-f]*"', f'_KEY_PART2 = bytes.fromhex("{key_part2.hex()}")', iat_src)
    IAT_FILE.write_text(iat_src, encoding="utf-8")

    print(f"已写入 {CRED_FILE.name} 与 {IAT_FILE.name}（密钥两半分藏，明文未落盘）")


if __name__ == "__main__":
    main()
