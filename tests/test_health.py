def test_healthz(client):
    res = client.get("/api/healthz")
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_readyz_ok_without_checks(client):
    res = client.get("/api/readyz")
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_readyz_503_when_a_check_fails(app, client):
    async def database() -> None:
        raise ConnectionError("db down")

    app.state.readiness_checks.append(database)

    res = client.get("/api/readyz")
    assert res.status_code == 503
    assert res.json() == {"ok": False, "failed": ["database"]}
    # liveness must not depend on dependencies
    assert client.get("/api/healthz").status_code == 200


def test_probes_are_not_exposed_without_prefix(client):
    assert client.get("/healthz").status_code == 404
