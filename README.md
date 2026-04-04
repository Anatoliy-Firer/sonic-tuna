# sonic-tuna
Sonic Tuna Project - проект виртуального тоннеля поверх онлайн конференций

## Использование --config

Оба скрипта (`tuna-ip.py` и `tuna-server.py`) поддерживают аргумент `--config`, который позволяет указать путь до YAML файла с конфигурацией.

Когда параметр `--config` указан, остальные параметры командной строки игнорируются, и их значения извлекаются из YAML файла.

### tuna-ip.py

Синтаксис: `tuna-ip.py {up,down} [--config <config-file.yaml>] [другие опции]`

Пример конфига для команды `up`:

```yaml
address: 10.0.0.1/24
user: sonic-tuna
device: tuna
mtu: 1400
route_table: 8042
create_user: false
nat: wlan0
```

Пример конфига для команды `down`:

```yaml
device: tuna
user: sonic-tuna
route_table: 8042
```

Использование:

```bash
# С конфигом (остальные параметры игнорируются)
python3 tuna-ip.py up --config config_up.yaml
python3 tuna-ip.py down --config config_down.yaml

# Без конфига (используются параметры командной строки)
python3 tuna-ip.py up -a 10.0.0.1/24 -d tuna
python3 tuna-ip.py down -d tuna
```

### tuna-server.py

Синтаксис: `tuna-server.py [--config <config-file.yaml>] [другие опции]`

Пример конфига:

```yaml
device: tuna
call_url: https://telemost.yandex.ru/j/88005553535
frame_scale: 1
frame_size: "256x256"
fps: 20
port: 8042
mtu: 1400
show_gui: false
disable_reed_solomon: false
log_level: INFO
```

Использование:

```bash
# С конфигом (остальные параметры игнорируются)
python3 tuna-server.py --config config.yaml

# Без конфига (используются параметры командной строки)
python3 tuna-server.py -d tuna --fps 20
```
