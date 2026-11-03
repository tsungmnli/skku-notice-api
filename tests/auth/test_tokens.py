from app.auth.tokens import hash_token, new_token


def test_tokens_are_long_and_unique():
    tokens = {new_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 43 for t in tokens)


def test_hash_is_stable_hex_and_not_the_token():
    t = new_token()
    assert hash_token(t) == hash_token(t)
    assert len(hash_token(t)) == 64
    assert t not in hash_token(t)
    assert hash_token("a") != hash_token("b")
