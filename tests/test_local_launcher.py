import pytest

from scripts.run_local import read_env


def test_normalizes_compose_quotes_for_podman(tmp_path):
    path = tmp_path / '.env'
    path.write_text('MQTT_HOST="192.168.1.216"\nMQTT_PASSWORD="a#b=c"\nAPI_TOKEN=""\nMQTT_PORT=1883\n')
    assert read_env(path) == 'MQTT_HOST=192.168.1.216\nMQTT_PASSWORD=a#b=c\nAPI_TOKEN=\nMQTT_PORT=1883\n'


@pytest.mark.parametrize('line', ['BAD KEY=x', 'MISSING_EQUALS', 'VALUE="first\\nsecond"'])
def test_rejects_invalid_env_lines(tmp_path, line):
    path = tmp_path / '.env'
    path.write_text(line)
    with pytest.raises(ValueError):
        read_env(path)


@pytest.mark.parametrize('volume_status,create_volume', [(0, False), (1, True)])
def test_launcher_reuses_existing_volume(monkeypatch, volume_status, create_volume):
    import sys
    from types import SimpleNamespace

    from scripts import run_local

    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1:3] == ['container', 'exists']:
            return SimpleNamespace(returncode=1)
        if command[1:3] == ['volume', 'exists']:
            return SimpleNamespace(returncode=volume_status)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(sys, 'argv', ['run_local.py', '--relay'])
    monkeypatch.setattr(run_local, 'read_env', lambda path: 'MQTT_HOST=broker\n')
    monkeypatch.setattr(run_local.os, 'chdir', lambda path: None)
    monkeypatch.setattr(run_local.shutil, 'which', lambda name: 'podman')
    monkeypatch.setattr(run_local.subprocess, 'run', run)
    run_local.main()
    assert any(command[1:3] == ['volume', 'create'] for command in commands) is create_volume
    assert any('MQTT_HOST=host.containers.internal' in command for command in commands)
