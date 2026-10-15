"""讯飞开放平台凭证：AES-256-GCM 加密存储，密钥拆两半分藏于本模块与 iat.py。

诚实定位：这是防「字符串搜索直接抽取」的混淆级保护——任何单一位置都只有半个
密钥；无法对抗愿意逆向二进制的人。凭证仅在内存解密，不落盘、不下发前端。
"""
from __future__ import annotations


class VoiceConfigError(RuntimeError):
    """语音服务凭证未配置或已损坏。"""


# 以下三个占位值在生成真凭证前解密必然失败，路由层把 VoiceConfigError 映射为「语音服务未配置」。
# 生成方式：python scripts/gen_xfyun_blob.py
_BLOB = "ee56b3cf884aa1940b6bbb0acd43e805c71b72ffc8cd73eee1524f29d34b297cfc1e379ff9081e63cbdcdb6e2d62df7bb4841ff99afc836437a5e4d3726a66a4470b2e36115f7cfc67e8c41562dd9435f0da19e957bcdccbd665"  # AES-GCM 密文+tag（hex）
_NONCE = "7360c894d6f51a11ef9e2dfa"  # 12 字节 nonce（hex）
_KEY_PART1 = "8a035e305a0dfbd047e44ae4f68badaf"  # 密钥前 16 字节（hex），后半在 iat.py


def load_credentials(key_part2: bytes) -> tuple[str, str, str]:
    """解密出 (app_id, api_key, api_secret)。明文格式：三行 UTF-8。"""
    try:
        key = bytes.fromhex(_KEY_PART1) + key_part2
        if len(key) != 32:
            raise ValueError("key length mismatch")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        plain = AESGCM(key).decrypt(bytes.fromhex(_NONCE), bytes.fromhex(_BLOB), None)
        app_id, api_key, api_secret = plain.decode("utf-8").split("\n")
        if not app_id or not api_key or not api_secret:
            raise ValueError("empty credential field")
        return app_id, api_key, api_secret
    except VoiceConfigError:
        raise
    except Exception as exc:
        raise VoiceConfigError("语音服务未配置") from exc
