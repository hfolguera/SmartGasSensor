# Firmware ESP8266

El sketch és a `SmartGasSensor/SmartGasSensor.ino`. Compte amb els noms: el directori i el `.ino` tenen el mateix nom, com requereix Arduino.

## Fitxers

- `SmartGasSensor.ino`: programa, publicable.
- `config.h`: configuració sense credencials: broker, topic, pins implícits del sketch i període de mostreig. Configurat a 3600 segons (1 hora).
- `secrets.example.h`: plantilla pública amb valors ficticis.
- `secrets.h`: credencials reals, només locals i excloses de Git.

El fitxer local `secrets.h` d’aquesta còpia s’ha preparat amb les credencials del projecte Arduino original. En un clon nou:

```bash
cp firmware/SmartGasSensor/secrets.example.h firmware/SmartGasSensor/secrets.h
```

Edita únicament `secrets.h` per indicar `WIFI_SSID`, `WIFI_PASSWORD`, `MQTT_USER` i `MQTT_PASSWORD`. No afegeixis aquest fitxer amb `git add -f`.

## Compilar i carregar

1. Obre `firmware/SmartGasSensor/SmartGasSensor.ino` amb Arduino IDE.
2. Utilitza el core ESP8266 3.1.2 i la biblioteca **MQTT de Joel Gaehwiler (256dpi), 2.5.3**, que correspon a `#include <MQTT.h>`.
3. Selecciona la placa NodeMCU corresponent (`NodeMCU 1.0 (ESP-12E Module)` per al perfil `esp8266:esp8266:nodemcuv2`) i el port sèrie.
4. Verifica i carrega. Conserva el pont D0/GPIO16–RST per al despertar; si interfereix en la càrrega, retira’l durant la càrrega i reconnecta’l després.
5. Monitor sèrie a 115200 baud. Comprova `sample_period_s: 3600` i confirmació MQTT; les lectures horàries són aproximades.

El sensor utilitza D1/GPIO5 per TRIG i D2/GPIO4 per ECHO. Conserva l’adaptació de nivell d’ECHO. `SWITCH_SENSOR_POWER=false` correspon a alimentació contínua; no activis la commutació sense el circuit físic. No connectis una 18650 carregada directament al pin 3V3; cal alimentació regulada adequada.

La còpia original de Documents/Arduino no es modifica automàticament: treballa en aquest sketch del repositori per mantenir els canvis versionats.

## Comprovar què es publica

Des de l’arrel del repositori:

```bash
git check-ignore firmware/SmartGasSensor/secrets.h
git status --short
git diff --cached --stat
```

El primer comandament ha d’imprimir el camí del fitxer. `secrets.h` no ha d’aparèixer entre els fitxers afegits a un commit. Afegeix fitxers explícits i revisa el diff abans de pujar-los.

No publiquis els binaris compilats amb credencials reals: poden contenir les contrasenyes. La carpeta `firmware/` també està exclosa del context de construcció de la imatge web.

Si una credencial arriba a un commit publicat, eliminar el fitxer del darrer commit no l’elimina de l’historial: revoca o canvia la credencial exposada. La configuració de Git només evita publicacions accidentals habituals; no xifra el fitxer local ni les credencials del dispositiu.
