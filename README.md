# sonic-tuna
Sonic Tuna Project - проект виртуального тоннеля поверх онлайн конференций

## Использование --config

Оба скрипта (`tuna-ip.py` и `tuna-server.py`) поддерживают аргумент командной строки `--config`, который позволяет указать путь до YAML файла с конфигурацией.

Когда параметр `--config` указан, остальные параметры командной строки игнорируются, и их значения извлекаются из YAML файла.

### tuna-ip.py

Пример конфига для команды `up`:

```yaml
command: up
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
command: down
device: tuna
user: sonic-tuna
route_table: 8042
```

Использование:

```bash
python3 tuna-ip.py --config config.yaml
```

### tuna-server.py

Пример конфига:

```yaml
device: tuna
call_url: https://telemost.yandex.ru/j/71720776790697
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
python3 tuna-server.py --config config.yaml
```
