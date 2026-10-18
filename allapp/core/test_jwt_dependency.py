"""Regression for upstream verification-options mutation, with a signed control."""

import jwt
import pytest


def test_unverified_decode_cannot_disable_later_expiry_verification():
    key = "synthetic-jwt-regression-key-32-bytes-minimum"
    expired = jwt.encode({"sub": "test", "exp": 1}, key, algorithm="HS256")
    shared = {"verify_signature": False}
    jwt.decode(expired, key, algorithms=["HS256"], options=shared)
    assert shared == {"verify_signature": False}
    shared["verify_signature"] = True
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(expired, key, algorithms=["HS256"], options=shared)
    valid = jwt.encode({"sub": "test"}, key, algorithm="HS256")
    assert jwt.decode(valid, key, algorithms=["HS256"], options=shared)["sub"] == "test"
