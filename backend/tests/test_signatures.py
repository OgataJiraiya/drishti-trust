from backend.core.signing import generate_key_pair, load_private_key, load_public_key, sign_bytes, verify_signature


def test_ed25519_sign_and_verify(tmp_path):
    paths = generate_key_pair(tmp_path)
    signature = sign_bytes(load_private_key(paths.private_key), b"receipt")
    public = load_public_key(paths.public_key)
    assert verify_signature(public, b"receipt", signature)
    assert not verify_signature(public, b"changed", signature)
    assert not verify_signature(public, b"receipt", "not-base64")
