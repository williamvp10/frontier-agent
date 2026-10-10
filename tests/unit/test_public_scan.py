from scripts import check_public_files as scanner


def test_public_provider_endpoint_exclusion_does_not_exclude_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "OPENROUTER_BASE_URL=https://openrouter.ai/api/v1\n"
        "OPENROUTER_API_KEY=canary-credential\n"
        "DATABASE_URL=postgresql://canary-private\n"
        "VNC_WS_URL=wss://canary-desktop\n"
    )
    secrets = scanner.local_secrets()
    assert b"https://openrouter.ai/api/v1" not in secrets
    assert b"canary-credential" in secrets
    assert b"postgresql://canary-private" in secrets
    assert b"wss://canary-desktop" in secrets


def test_custom_provider_endpoint_remains_private(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("OPENROUTER_BASE_URL=https://canary-private/proxy\n")
    assert b"https://canary-private/proxy" in scanner.local_secrets()


def test_langfuse_public_origin_exclusion_keeps_keys_and_custom_hosts_private(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "LANGFUSE_BASE_URL=https://us.cloud.langfuse.com\n"
        "LANGFUSE_PUBLIC_KEY=canary-public-credential\n"
        "LANGFUSE_SECRET_KEY=canary-secret-credential\n"
    )
    assert set(scanner.local_secrets()) == {b"canary-public-credential", b"canary-secret-credential"}
    (tmp_path / ".env.custom").write_text("LANGFUSE_BASE_URL=https://canary-private/traces\n")
    assert b"https://canary-private/traces" in scanner.local_secrets()
