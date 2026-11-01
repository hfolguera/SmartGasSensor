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
