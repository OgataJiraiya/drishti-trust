from backend.core.signing import generate_key_pair, load_private_key, load_public_key, sign_bytes, verify_signature


def test_ed25519_sign_and_verify(tmp_path):
    paths = generate_key_pair(tmp_path)
    signature = sign_bytes(load_private_key(paths.private_key), b"receipt")
    public = load_public_key(paths.public_key)
    assert verify_signature(public, b"receipt", signature)
    assert not verify_signature(public, b"changed", signature)
    assert not verify_signature(public, b"receipt", "not-base64")


def test_private_key_permissions_are_restrictive_at_creation(tmp_path, monkeypatch):
    import os
    import stat
    real_open = os.open
    modes = []
    def checked(path, flags, mode=0o777):
        modes.append((str(path), flags, mode))
        return real_open(path, flags, mode)
    monkeypatch.setattr(os, 'open', checked)
    paths = generate_key_pair(tmp_path)
    private = next(item for item in modes if item[0].endswith('.private.pem'))
    assert private[1] & os.O_EXCL and private[2] == 0o600
    assert stat.S_IMODE(paths.private_key.stat().st_mode) == 0o600


def test_receipt_key_pair_mismatch_fails_closed():
    import pytest
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from backend.services.provenance_service import ProvenanceService
    with pytest.raises(RuntimeError, match='inconsistent'):
        ProvenanceService(Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate().public_key(), 1024)


def test_missing_receipt_keys_do_not_silently_rotate_existing_history(tmp_path):
    import pytest
    from backend.core.config import Settings
    from backend.main import create_app
    from backend.schemas.inference import CreateReceiptRequest
    settings = Settings(data_dir=tmp_path / 'data', key_dir=tmp_path / 'keys')
    app = create_app(settings)
    with app.state.session_factory() as session:
        app.state.provenance_service.create_receipt(CreateReceiptRequest(
            filename='sample', input_base64='eA==', model_id='model', model_sha256='0' * 64,
            preprocessing={}, config={}, output={}), session)
    (settings.key_dir / 'receipt_signing.private.pem').unlink()
    (settings.key_dir / 'receipt_signing.public.pem').unlink()
    with pytest.raises(RuntimeError, match='receipt trust-root keys are missing'):
        create_app(settings)
